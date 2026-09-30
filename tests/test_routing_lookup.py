"""Derived placement lookups and the one shared model-id reader.

Two contracts are locked here.

*Placement*: a profile's ``model -> (group, group index, position)`` map is
derived once when the profile is built, so ranking a candidate pool is a map
read rather than a rescan of every group. What matters is that deriving it
changed nothing observable: the same order, the same group and position per
model, the same exclusions, and the same public constructor.

*Identity*: one helper reads ``<developer>/<model>`` for the routing catalogue
and for a council roster, so the two cannot drift. The one intentional
difference is explicit and tested: the catalogue records *dispatch* ids and
requires the ``openrouter/`` namespace, while a roster seat is an operator's
model name and accepts the bare form. ``developer_for_model`` keeps its
historical lenient reading for existing callers; the strict helper is what a
caller uses when a vendor-less id must be rejected rather than guessed at.
"""

from __future__ import annotations

import dataclasses
from types import MappingProxyType

import pytest

from orchestrator import model_routing
from orchestrator.entitlements.errors import PolicyError
from test_compute_runtime import routing_document

LUNA = "openrouter/openai/gpt-6-luna"
SOL = "openrouter/openai/gpt-6.1-sol"
GLM = "openrouter/z-ai/glm-5.3"
SONNET = "openrouter/anthropic/claude-sonnet-5"
OPUS = "openrouter/anthropic/claude-opus-5.5"
GOOGLE = "openrouter/google/gemini-3.8-flash"
UNPLACED = "openrouter/test/unplaced"


def multi_group_profile(models_by_group: dict[str, list[str]]) -> model_routing.WorkloadProfile:
    """A directly constructed profile, the way a deployment fixture would."""
    return model_routing.WorkloadProfile(
        name="reasoning",
        min_output_tokens=512,
        allow_premium=False,
        groups=tuple(
            model_routing.RoutingGroup(name=name, index=index, models=tuple(models))
            for index, (name, models) in enumerate(models_by_group.items())
        ),
        distinct_developers=False,
    )


def placed_in(profile: model_routing.WorkloadProfile, model: str) -> model_routing.Placement:
    """The placement of a model the test has already placed, or a hard failure."""
    found = profile.placement_of(model)
    assert found is not None, f"{model} is not placed in {profile.name!r}"
    return found


def pool(profile_name: str, **kwargs: object) -> tuple[model_routing.RoutedModel, ...]:
    """The profile's candidates, failing loudly rather than as ``None``."""
    found = model_routing.profile_candidates(profile_name, **kwargs)  # type: ignore[arg-type]
    assert found is not None, f"{profile_name!r} has no candidates"
    return found


class TestDerivedPlacementMap:
    def test_map_is_built_once_per_profile_not_per_lookup(self):
        profile = multi_group_profile({"demanding": [GLM, SOL], "escalation": [OPUS]})
        first = placed_in(profile, GLM)
        # Same object on every read: the map is derived at construction, so a
        # candidate scan cannot rebuild it once per candidate.
        assert profile.placement_of(GLM) is first
        assert all(profile.placement_of(GLM) is first for _ in range(5))

    def test_map_agrees_with_the_group_it_was_derived_from(self):
        groups = {"demanding": [GLM, SOL, SONNET], "escalation": [OPUS, GOOGLE]}
        profile = multi_group_profile(groups)
        for index, (name, models) in enumerate(groups.items()):
            for position, model in enumerate(models):
                placed = placed_in(profile, model)
                assert (placed.model, placed.group, placed.group_index, placed.position) == (
                    model,
                    name,
                    index,
                    position,
                )
                assert placed.order == index * model_routing.PLACEMENT_GROUP_STRIDE + position

    def test_order_is_group_major_then_configuration_order(self):
        profile = multi_group_profile({"demanding": [GLM, SOL], "escalation": [OPUS, GOOGLE]})
        orders = [placed_in(profile, model).order for model in profile.ranked_models()]
        assert orders == sorted(orders)
        assert profile.ranked_models() == (GLM, SOL, OPUS, GOOGLE)

    def test_unplaced_model_has_no_placement(self):
        profile = multi_group_profile({"demanding": [GLM]})
        assert profile.placement_of(UNPLACED) is None
        assert UNPLACED not in profile.placement()

    def test_placement_returns_a_fresh_mutable_copy(self):
        profile = multi_group_profile({"demanding": [GLM, SOL]})
        first = profile.placement()
        assert first == {GLM: 0, SOL: 1}
        first.clear()
        first["injected"] = 99
        # A caller mutating the returned map cannot poison the profile's own.
        assert profile.placement() == {GLM: 0, SOL: 1}

    def test_derived_map_is_read_only(self):
        profile = multi_group_profile({"demanding": [GLM]})
        assert isinstance(profile._placements, MappingProxyType)
        with pytest.raises(TypeError):
            profile._placements[UNPLACED] = None  # type: ignore[index]

    def test_placement_records_are_frozen(self):
        profile = multi_group_profile({"demanding": [GLM]})
        placed = placed_in(profile, GLM)
        with pytest.raises(dataclasses.FrozenInstanceError):
            placed.position = 7  # type: ignore[misc]

    def test_public_constructor_is_unchanged(self):
        """The derived map is not a new required argument on the public shape."""
        groups = (model_routing.RoutingGroup(name="demanding", index=0, models=(GLM,)),)
        by_keyword = model_routing.WorkloadProfile(
            name="reasoning",
            min_output_tokens=512,
            allow_premium=True,
            groups=groups,
            distinct_developers=False,
            notes="fixture",
        )
        by_position = model_routing.WorkloadProfile("reasoning", 512, True, groups, False)
        for profile in (by_keyword, by_position):
            assert placed_in(profile, GLM).group == "demanding"
        replaced = dataclasses.replace(by_keyword, min_output_tokens=1024)
        assert replaced.min_output_tokens == 1024
        assert placed_in(replaced, GLM).order == placed_in(by_keyword, GLM).order

    def test_derived_map_does_not_affect_equality_or_hashing(self):
        groups = (model_routing.RoutingGroup(name="demanding", index=0, models=(GLM,)),)
        left = model_routing.WorkloadProfile("reasoning", 512, True, groups, False)
        right = model_routing.WorkloadProfile("reasoning", 512, True, groups, False)
        assert left == right
        assert hash(left) == hash(right)
        assert left != model_routing.WorkloadProfile(
            "reasoning", 512, True, groups, False, "different notes"
        )

    def test_a_model_placed_in_two_groups_resolves_consistently(self):
        """A duplicate is a parse error, so it only reaches a hand-built profile.

        The group a model is reported in and the order it is sorted by must come
        from the same derivation rather than from two different scans that could
        disagree about which group they found.
        """
        profile = multi_group_profile({"demanding": [GLM], "escalation": [GLM, SOL]})
        placed = placed_in(profile, GLM)
        assert (placed.group, placed.position) == ("escalation", 0)
        assert placed.order == profile.placement()[GLM]


class TestPlacementLookupContract:
    """What the derived map must keep returning, checked against the config."""

    def test_candidate_reports_the_group_index_and_position_it_sits_in(self):
        profile = model_routing.load_model_routing().profile("council")
        for candidate in pool("council"):
            placed = placed_in(profile, candidate.model)
            assert (candidate.group, candidate.group_index, candidate.position) == (
                placed.group,
                placed.group_index,
                placed.position,
            )
            assert candidate.placement == placed.order

    def test_candidate_order_is_group_order_then_configuration_order(self):
        profile = model_routing.load_model_routing().profile("reasoning")
        candidates = pool("reasoning")
        assert [candidate.model for candidate in candidates] == list(profile.ranked_models())
        assert [candidate.placement for candidate in candidates] == sorted(
            candidate.placement for candidate in candidates
        )

    def test_exclusions_remove_candidates_without_reordering_the_rest(self):
        full = pool("council")
        excluded_model = full[1].model
        excluded_developers = frozenset({full[0].developer})
        filtered = pool(
            "council",
            excluded_models=frozenset({excluded_model}),
            excluded_developers=excluded_developers,
        )
        remaining = [candidate.model for candidate in filtered]
        assert excluded_model not in remaining
        assert all(candidate.developer not in excluded_developers for candidate in filtered)
        # Exclusion is a removal, not a reshuffle: order is a subsequence.
        assert remaining == [m for m in (c.model for c in full) if m in set(remaining)]

    def test_excluding_every_candidate_is_none_not_an_empty_tuple(self):
        every = pool("council")
        assert (
            model_routing.profile_candidates(
                "council",
                excluded_models=frozenset(candidate.model for candidate in every),
            )
            is None
        )

    def test_single_model_lookup_agrees_with_the_pool(self):
        for candidate in pool("council")[:3]:
            single = model_routing.profile_candidate("council", candidate.model)
            assert single == candidate

    def test_unplaced_model_is_not_a_candidate(self):
        assert model_routing.profile_candidate("routine", UNPLACED) is None
        assert model_routing.is_model_routable("routine", UNPLACED) is False

    def test_group_helpers_are_unchanged(self):
        profile = model_routing.load_model_routing().profile("council")
        assert profile.group_names() == ("diverse", "escalation")
        assert profile.group_index("diverse") == 0
        assert profile.group_index("escalation") == 1
        with pytest.raises(model_routing.RoutingError, match="unknown group"):
            profile.group_index("made-up")

    def test_parsed_profile_derives_its_map(self):
        parsed = model_routing.parse_model_routing(routing_document([LUNA, SOL, GLM]))
        profile = parsed.profile("reasoning")
        # Every parsed profile derives its map at construction, including the
        # single-group ones, so no profile shape is a special case.
        assert placed_in(profile, SOL).order == 1
        assert placed_in(profile, SOL).position == 1
        assert profile.placement() == {LUNA: 0, SOL: 1, GLM: 2}


class TestModelIdentity:
    @pytest.mark.parametrize(
        ("model_id", "developer", "namespaced", "name"),
        [
            ("openrouter/z-ai/glm-5.3", "z-ai", True, "glm-5.3"),
            ("z-ai/glm-5.3", "z-ai", False, "glm-5.3"),
            ("openrouter/x-ai/grok-4.7:free", "x-ai", True, "grok-4.7:free"),
            ("  openrouter/meta-llama/llama-3/70b  ", "meta-llama", True, "llama-3/70b"),
            ("OpenRouter/Z-AI/GLM-5.3", "openrouter", False, "Z-AI/GLM-5.3"),
        ],
    )
    def test_reads_vendor_and_model(self, model_id, developer, namespaced, name):
        identity = model_routing.read_model_identity(model_id)
        assert (identity.developer, identity.model, identity.namespaced) == (
            developer,
            name,
            namespaced,
        )

    def test_namespaced_and_bare_forms_are_the_same_developer(self):
        assert (
            model_routing.read_model_identity("openrouter/z-ai/glm-5.3").developer
            == model_routing.read_model_identity("z-ai/glm-5.3").developer
        )

    @pytest.mark.parametrize(
        "model_id",
        [
            "",
            "   ",
            "claude-sonnet-5",
            "openrouter/",
            "openrouter//claude-sonnet-5",
            "openrouter/anthropic/",
            "anthropic/claude-sonnet-5/",
            "openrouter/anthropic/ claude-sonnet-5",
            "openrouter/anthropic:free/",
        ],
    )
    def test_vendorless_or_malformed_ids_are_rejected(self, model_id: str):
        with pytest.raises(model_routing.RoutingError):
            model_routing.read_model_identity(model_id)

    def test_catalogue_requires_the_dispatch_namespace(self):
        """Same reader, one flag: the catalogue's ids are dispatch ids."""
        assert (
            model_routing.read_model_identity(
                "openrouter/z-ai/glm-5.3", require_namespace=True
            ).developer
            == "z-ai"
        )
        assert model_routing.read_model_identity("z-ai/glm-5.3").developer == "z-ai", (
            "a roster seat is still readable without the namespace"
        )
        with pytest.raises(model_routing.RoutingError, match="openrouter/"):
            model_routing.read_model_identity("z-ai/glm-5.3", require_namespace=True)

    def test_developer_for_model_keeps_its_lenient_reading(self):
        """Existing callers get the historical answer, valid or not.

        ``developer_for_model`` returns a bare vendor-less id as-is; the strict
        helper is the one that refuses it. Changing the old function's contract
        silently would be a behaviour change for every caller, so it stays and
        the strict reading is opt-in.
        """
        assert model_routing.developer_for_model("claude-sonnet-5") == "claude-sonnet-5"
        assert model_routing.developer_for_model("openrouter/z-ai/glm-5.3") == "z-ai"
        assert model_routing.developer_for_model("z-ai/glm-5.3") == "z-ai"
        with pytest.raises(model_routing.RoutingError):
            model_routing.developer_for_model("")

    def test_the_two_readers_agree_wherever_both_can_read(self):
        for model_id in (GLM, "z-ai/glm-5.3", "openrouter/openai/gpt-6.1-sol", "openai/gpt-6-luna"):
            assert (
                model_routing.developer_for_model(model_id)
                == model_routing.read_model_identity(model_id).developer
            )


class TestCatalogueAndRosterShareTheReader:
    """The namespace difference is intentional, and it is the only difference."""

    def test_catalogue_refuses_a_bare_placement_the_dispatcher_could_never_send(self):
        document = routing_document(["z-ai/glm-5.3"])
        with pytest.raises(PolicyError, match="namespaced OpenRouter model id"):
            model_routing.parse_model_routing(document)

    def test_catalogue_accepts_the_same_model_in_its_dispatch_form(self):
        parsed = model_routing.parse_model_routing(routing_document(["openrouter/z-ai/glm-5.3"]))
        assert parsed.model("openrouter/z-ai/glm-5.3") is not None

    def test_catalogue_shape_message_is_preserved(self):
        document = routing_document(["openrouter/z-ai/glm-5.3"])
        document["profiles"][0]["groups"][0]["models"] = ["openrouter/z-ai/"]
        with pytest.raises(PolicyError, match="must name a vendor and a model"):
            model_routing.parse_model_routing(document)

    def test_roster_keeps_accepting_the_bare_form(self):
        from orchestrator.council.models import CouncilConfig, read_developer

        config = CouncilConfig(
            roster={
                "analyst": "anthropic/claude-sonnet-5",
                "strategist": "openai/gpt-6.1-sol",
                "skeptic": "google/gemini-3.8-flash",
            }
        )
        assert config.roster["analyst"] == "anthropic/claude-sonnet-5"
        # The same developer the routing module reads out of the namespaced form.
        assert read_developer("anthropic/claude-sonnet-5") == read_developer(SONNET)

    @pytest.mark.parametrize(
        "model_id", ["claude-sonnet-5", "openrouter/", "anthropic/claude-sonnet-5/"]
    )
    def test_roster_still_rejects_a_vendorless_or_malformed_seat(self, model_id: str):
        from orchestrator.council.models import CouncilConfig

        with pytest.raises(ValueError, match=r"analyst.*<developer>/<model>"):
            CouncilConfig(
                roster={
                    "analyst": model_id,
                    "strategist": SOL,
                    "skeptic": GOOGLE,
                }
            )

    def test_roster_and_routing_never_disagree_about_a_developer(self):
        from orchestrator.council.models import read_developer

        for model_id in (
            LUNA,
            SOL,
            GLM,
            SONNET,
            GOOGLE,
            "z-ai/glm-5.3",
            "openrouter/x-ai/grok-4.7",
        ):
            assert read_developer(model_id) == model_routing.read_model_identity(model_id).developer
