"""Roster validation: a council is independent or it is not a council.

Covers the developer-level diversity rule (the roster validator used to read the
first path segment, so every namespaced id looked like the same "openrouter"
provider and valid rosters were rejected), malformed ids, duplicate seats, and
the single-source-of-truth shipped roster.
"""

from __future__ import annotations

import pytest

from orchestrator.council import engine as council_engine
from orchestrator.council.config import load_roster
from orchestrator.council.models import (
    MIN_COUNCIL_DEVELOPERS,
    CouncilConfig,
    CouncilDiversityError,
    default_roster,
    read_developer,
    roster_developers,
)

PREFIXED_ROSTER = {
    "analyst": "openrouter/anthropic/claude-sonnet-5",
    "strategist": "openrouter/openai/gpt-6-sol",
    "skeptic": "openrouter/google/gemini-3.8-flash",
    "contrarian": "openrouter/x-ai/grok-4.7",
    "auditor": "openrouter/z-ai/glm-5.3",
}


class TestNamespacedRosters:
    def test_namespaced_roster_with_five_developers_is_accepted(self):
        config = CouncilConfig(roster=dict(PREFIXED_ROSTER))
        assert config.roster == PREFIXED_ROSTER

    def test_namespaced_roster_with_three_developers_is_accepted(self):
        config = CouncilConfig(
            roster={
                "analyst": "openrouter/anthropic/claude-sonnet-5",
                "strategist": "openrouter/openai/gpt-6-sol",
                "skeptic": "openrouter/google/gemini-3.8-flash",
            }
        )
        assert len(config.roster) == 3

    def test_bare_roster_is_accepted(self):
        config = CouncilConfig(
            roster={
                "analyst": "anthropic/claude-sonnet-5",
                "strategist": "openai/gpt-6-sol",
                "skeptic": "google/gemini-3.8-flash",
            }
        )
        assert config.roster["analyst"] == "anthropic/claude-sonnet-5"

    def test_prefixed_and_bare_forms_count_as_the_same_developer(self):
        roster = {
            "analyst": "openrouter/anthropic/claude-sonnet-5",
            "strategist": "anthropic/claude-opus-4.6",
            "skeptic": "openrouter/google/gemini-3.8-flash",
        }
        with pytest.raises(ValueError, match="at least 3 different model developers"):
            CouncilConfig(roster=roster)
        assert roster_developers(roster) == {
            "analyst": "anthropic",
            "strategist": "anthropic",
            "skeptic": "google",
        }


class TestDeveloperFloor:
    def test_floor_is_three_developers(self):
        assert MIN_COUNCIL_DEVELOPERS == 3

    def test_two_developers_is_rejected(self):
        with pytest.raises(ValueError, match="at least 3 different model developers"):
            CouncilConfig(
                roster={
                    "analyst": "openrouter/anthropic/claude-sonnet-5",
                    "strategist": "openrouter/openai/gpt-6-sol",
                    "skeptic": "openrouter/openai/gpt-6-luna",
                }
            )

    def test_one_developer_is_rejected(self):
        with pytest.raises(ValueError, match="at least 3 different model developers"):
            CouncilConfig(
                roster={
                    "analyst": "openrouter/openai/gpt-6-sol",
                    "strategist": "openrouter/openai/gpt-6-luna",
                }
            )

    def test_empty_roster_is_rejected(self):
        with pytest.raises(ValueError, match="at least 3 different model developers"):
            CouncilConfig(roster={})

    def test_two_models_from_one_developer_plus_two_more_developers_is_accepted(self):
        config = CouncilConfig(
            roster={
                "analyst": "openrouter/anthropic/claude-sonnet-5",
                "strategist": "openrouter/anthropic/claude-opus-4.6",
                "skeptic": "openrouter/openai/gpt-6-sol",
                "contrarian": "openrouter/google/gemini-3.8-flash",
            }
        )
        assert len(set(roster_developers(config.roster).values())) == 3


class TestMalformedModelIds:
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
        ],
    )
    def test_malformed_model_id_is_rejected(self, model_id: str):
        roster = {
            "analyst": model_id,
            "strategist": "openrouter/openai/gpt-6-sol",
            "skeptic": "openrouter/google/gemini-3.8-flash",
        }
        with pytest.raises(ValueError):
            CouncilConfig(roster=roster)

    def test_malformed_model_id_names_the_role_and_the_shape(self):
        with pytest.raises(ValueError, match="analyst.*<developer>/<model>"):
            CouncilConfig(
                roster={
                    "analyst": "claude-sonnet-5",
                    "strategist": "openrouter/openai/gpt-6-sol",
                    "skeptic": "openrouter/google/gemini-3.8-flash",
                }
            )

    def test_missing_model_is_rejected(self):
        with pytest.raises(ValueError, match="has no model assigned"):
            CouncilConfig(
                roster={
                    "analyst": "  ",
                    "strategist": "openrouter/openai/gpt-6-sol",
                    "skeptic": "openrouter/google/gemini-3.8-flash",
                }
            )

    def test_unreadable_model_never_counts_as_a_developer(self):
        assert read_developer("") is None
        assert read_developer("   ") is None
        assert read_developer("openrouter/") is None
        assert roster_developers({"analyst": "", "strategist": "openai/gpt-6-sol"}) == {
            "strategist": "openai"
        }


class TestDuplicateSeats:
    def test_same_model_in_two_seats_is_rejected(self):
        with pytest.raises(ValueError, match="more than one seat|its own model"):
            CouncilConfig(
                roster={
                    "analyst": "openrouter/anthropic/claude-sonnet-5",
                    "strategist": "openrouter/anthropic/claude-sonnet-5",
                    "skeptic": "openrouter/openai/gpt-6-sol",
                    "contrarian": "openrouter/google/gemini-3.8-flash",
                }
            )

    def test_whitespace_variant_of_a_duplicate_is_still_a_duplicate(self):
        with pytest.raises(ValueError, match="its own model"):
            CouncilConfig(
                roster={
                    "analyst": "openrouter/anthropic/claude-sonnet-5",
                    "strategist": "  openrouter/anthropic/claude-sonnet-5  ",
                    "skeptic": "openrouter/openai/gpt-6-sol",
                    "contrarian": "openrouter/google/gemini-3.8-flash",
                }
            )


class TestShippedRoster:
    def test_default_roster_comes_from_the_loader_not_a_second_copy(self):
        assert default_roster() == load_roster("default")

    def test_default_config_matches_the_shipped_roster(self):
        assert CouncilConfig().roster == load_roster("default")

    def test_default_config_is_validated_against_its_own_diversity_rule(self):
        # validate_default means a roster.yaml that stops being a council fails
        # at construction instead of running as a one-voice deliberation.
        developers = roster_developers(default_roster())
        assert len(set(developers.values())) == len(default_roster())
        assert len(set(developers.values())) >= MIN_COUNCIL_DEVELOPERS

    def test_shipped_seats_plan_one_developer_each(self):
        expected = {
            "analyst": "anthropic",
            "strategist": "openai",
            "skeptic": "google",
            "contrarian": "x-ai",
            "auditor": "z-ai",
        }
        assert roster_developers(load_roster("default")) == expected

    def test_shipped_seat_models_are_in_their_own_developer_namespace(self):
        for role, model_id in load_roster("default").items():
            assert model_id.startswith("openrouter/")
            assert read_developer(model_id) == roster_developers(load_roster("default"))[role]

    def test_lean_preset_still_fields_three_developers(self):
        lean = load_roster("lean")
        assert len(set(roster_developers(lean).values())) >= MIN_COUNCIL_DEVELOPERS
        assert CouncilConfig(roster=lean).roster == lean

    def test_default_roster_is_not_shared_between_configs(self):
        first = CouncilConfig()
        first.roster["analyst"] = "openrouter/anthropic/claude-sonnet-5"
        assert CouncilConfig().roster["analyst"] == load_roster("default")["analyst"]


class TestDiversityErrorContract:
    def test_error_is_a_runtime_error_with_a_user_facing_message(self):
        error = CouncilDiversityError("Council round 1 was served by 2 model developers")
        assert isinstance(error, RuntimeError)
        assert "2 model developers" in str(error)

    def test_engine_exposes_the_same_error_class_for_the_integration_layer(self):
        assert council_engine.CouncilDiversityError is CouncilDiversityError
