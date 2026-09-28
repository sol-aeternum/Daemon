"""Central workload routing: which model is *acceptable* for a task, and nothing else.

This module answers one question per request: **given this workload profile, which
model fits, and which centrally validated reasoning controls go with it?** It answers
that question with a locally loaded, strictly validated config file
(``config/model_routing.json``). It grants nothing.

The separation that matters:

* ``config/model_routing.json`` (this module) owns **suitability**: ordered
  acceptable groups of provisional candidate models, the comparable output budget
  each workload class needs, whether a profile may auto-consider premium routes, and
  the per-model reasoning parameter presets that may be sent.
* ``config/inference_policy.json`` owns **permission**: dated approval, operator
  review, verified availability, pinned endpoint, pinned ZDR transport block,
  account-side logging state, training opt-out, price ceilings, declared
  ``model_capabilities`` and token limits. The qualification gate there remains the
  *sole* permission to dispatch.
* ``config/commercial.json`` owns **entitlements**: which capabilities, limits and
  budgets an account has.

Nothing here fetches a model catalogue at request time, and no price or plan
decision is taken here. A model name, a free tier or a provider's own capability
list is never evidence of qualification.

**The placements in this file are provisional.** Every ``suitability`` list, group
order, ``min_output_tokens`` floor and reasoning preset is an engineering hypothesis
about which model is good enough for which task, pending real evaluation. A generic
capability flag (``text``/``tools``/``json_schema``) is a protocol fact and never
implies answer quality, so price alone can never promote a model: selection walks the
profile's groups in order and takes the cheapest candidate **within the first group
that has any eligible candidate**.

Explicit selection bypasses the profile's *model* shortlist and nothing else. An
explicitly requested model still has to clear qualification, the required
capabilities for the request, the account's entitlements, its budget and its context
and output bounds, and an explicit request is never silently substituted.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from orchestrator.entitlements.errors import PolicyError

#: repository root: orchestrator/model_routing.py -> parents[1]
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

DEFAULT_MODEL_ROUTING: Final[Path] = REPO_ROOT / "config" / "model_routing.json"
MODEL_ROUTING_ENV: Final[str] = "DAEMON_MODEL_ROUTING"

SUPPORTED_MODEL_ROUTING_VERSION: Final[int] = 1

#: The workload profiles a caller may select. Every one of them must be configured.
ROUTING_PROFILES: Final[frozenset[str]] = frozenset(
    {"routine", "reasoning", "research", "background", "council"}
)

#: Namespace prefix Daemon uses for OpenRouter model ids. Stripped before the
#: developer is derived, so ``openrouter/z-ai/glm-5.3`` and ``z-ai/glm-5.3``
#: resolve to the same developer.
OPENROUTER_PREFIX: Final[str] = "openrouter/"

#: Every reasoning effort any placed model may accept, in increasing order. A
#: caller may request any of these; a per-model ladder in the config decides whether
#: the *selected* model can actually serve it.
KNOWN_REASONING_EFFORTS: Final[frozenset[str]] = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)

#: The only parameters this module is allowed to introduce on a caller's behalf.
#: Deliberately narrow: a preset may set a reasoning control, never a price, a
#: transport flag, a budget or any other completion field.
PRESET_PARAMETERS: Final[frozenset[str]] = frozenset({"reasoning_effort", "include_reasoning"})
SAMPLING_PARAMETERS: Final[frozenset[str]] = frozenset(
    {"temperature", "top_p", "frequency_penalty", "presence_penalty", "seed", "stop"}
)

#: Preset name applied when a profile does not override a model's ``default``.
DEFAULT_PRESET: Final[str] = "default"


class RoutingError(Exception):
    """A routing decision could not be made. Never a dispatch permission."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# --------------------------------------------------------------------------- #
# Parsed configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RoutingModel:
    """Provisional metadata about one candidate model.

    ``suitability`` and the parameter presets are **unmeasured placements pending
    real evaluation**. They describe what Daemon is willing to try for a workload,
    not what the model has been shown to do. ``reasoning_efforts`` is the ladder the
    provider is understood to accept; it is not a quality statement.
    """

    model: str
    developer: str
    suitability: frozenset[str]
    reasoning_efforts: frozenset[str]
    sampling_parameters: frozenset[str]
    presets: Mapping[str, Mapping[str, Any]]
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RoutingGroup:
    """One ordered set of models that are all acceptable for the same job."""

    name: str
    index: int
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkloadProfile:
    """A workload class and the ordered groups of models acceptable for it.

    ``min_output_tokens`` is the **comparable output budget**: the floor every
    candidate in this profile must be able to return, so two answers to the same
    workload are comparable in length. A cheaper model that cannot meet the floor is
    not more suitable, it is not a candidate, and the request escalates or fails
    rather than silently shortening the answer.
    """

    name: str
    min_output_tokens: int
    allow_premium: bool
    groups: tuple[RoutingGroup, ...]
    distinct_developers: bool
    notes: str = ""

    def group_index(self, group_name: str) -> int:
        for group in self.groups:
            if group.name == group_name:
                return group.index
        raise RoutingError("profile_unknown_group", f"unknown group {group_name!r}")

    def group_names(self) -> tuple[str, ...]:
        return tuple(group.name for group in self.groups)

    def placement(self) -> dict[str, int]:
        """``model -> (group index, position in group)`` for stable ordering."""
        placement: dict[str, int] = {}
        for group in self.groups:
            for position, model in enumerate(group.models):
                placement[model] = group.index * 1000 + position
        return placement

    def ranked_models(self) -> tuple[str, ...]:
        """All models in group order then configuration order within a group."""
        return tuple(model for group in self.groups for model in group.models)


@dataclass(frozen=True, slots=True)
class ModelRouting:
    """Validated ``config/model_routing.json``."""

    version: int
    provisional: bool
    models: Mapping[str, RoutingModel]
    profiles: Mapping[str, WorkloadProfile]
    source_path: str

    def profile(self, name: str) -> WorkloadProfile:
        """Look up a profile. Unknown names fail closed, they do not fall back."""
        found = self.profiles.get(name)
        if found is None:
            raise RoutingError(
                "profile_unknown",
                f"unknown workload profile {name!r}; known: {', '.join(sorted(self.profiles))}",
            )
        return found

    def has_profile(self, name: str) -> bool:
        return name in self.profiles

    def model(self, model: str) -> RoutingModel | None:
        return self.models.get(model)

    def model_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.models))

    def profiles_for_model(self, model: str) -> tuple[str, ...]:
        found = self.models.get(model)
        if found is None:
            return ()
        return tuple(sorted(found.suitability))


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def _require_mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{field_name} must be an object")
    return value


def _require_str(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_positive_int(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PolicyError(f"{field_name} must be a positive integer")
    return value


def _require_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise PolicyError(f"{field_name} must be a boolean")
    return value


def developer_for_model(model: str) -> str:
    """The developer slug that owns ``model``, lowercased.

    Daemon namespaces OpenRouter model ids with an ``openrouter/`` prefix, which is
    stripped before the developer is derived, so the namespaced and bare forms of the
    same model resolve identically::

        developer_for_model("openrouter/z-ai/glm-5.3") == "z-ai"
        developer_for_model("z-ai/glm-5.3") == "z-ai"
        developer_for_model("openrouter/x-ai/grok-4.7") == "x-ai"
        developer_for_model("openrouter/openai/gpt-6-luna") == "openai"

    A bare id with no vendor segment is returned as-is; callers that need a real
    vendor slug should not rely on that case.
    """
    if not isinstance(model, str) or not model.strip():
        raise RoutingError("model_unknown", "model must be a non-empty string")
    trimmed = model.strip()
    if trimmed.startswith(OPENROUTER_PREFIX):
        trimmed = trimmed[len(OPENROUTER_PREFIX) :]
    trimmed = trimmed.strip("/")
    if not trimmed:
        raise RoutingError("model_unknown", f"model has no developer segment: {model!r}")
    # Drop an OpenRouter variant suffix (":free", ":nitro") so the slug is stable.
    vendor, _, _rest = trimmed.partition("/")
    vendor = vendor.split(":", 1)[0]
    if not vendor:
        raise RoutingError("model_unknown", f"model has no developer segment: {model!r}")
    return vendor.lower()


def _normalise_model_id(raw: object, *, field_name: str) -> str:
    """Accept only the namespaced id form the dispatch layer can actually route.

    ``config/inference_policy.json`` records route models as ``openrouter/<vendor>/<id>``
    and the dispatch layer refuses anything else, so a placement is written in that
    same form. Writing the bare id here would be a placement that could never be
    dispatched.
    """
    model = _require_str(raw, field_name=field_name)
    if not model.startswith(OPENROUTER_PREFIX):
        raise PolicyError(
            f"{field_name} must be a namespaced OpenRouter model id beginning with "
            f"{OPENROUTER_PREFIX!r} (the form config/inference_policy.json records and the "
            f"dispatch layer accepts), got {model!r}"
        )
    remainder = model[len(OPENROUTER_PREFIX) :]
    if "/" not in remainder or any(
        not part or part.strip() != part for part in remainder.split("/")
    ):
        raise PolicyError(f"{field_name} must name a vendor and a model, got {model!r}")
    return model


def _parse_presets(
    raw: object,
    *,
    model: str,
    efforts: frozenset[str],
) -> dict[str, Mapping[str, Any]]:
    mapping = _require_mapping(raw, field_name=f"models.{model}.parameter_presets")
    presets: dict[str, Mapping[str, Any]] = {}
    for name, body in mapping.items():
        preset_name = _require_str(name, field_name=f"models.{model}.parameter_presets key")
        if preset_name not in ROUTING_PROFILES | {DEFAULT_PRESET}:
            raise PolicyError(f"unknown parameter preset profile: {preset_name}")
        fields = _require_mapping(
            body, field_name=f"models.{model}.parameter_presets.{preset_name}"
        )
        values: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in PRESET_PARAMETERS:
                raise PolicyError(
                    f"models.{model}.parameter_presets.{preset_name}.{key} is not a centrally "
                    f"validated parameter; only {', '.join(sorted(PRESET_PARAMETERS))} may be preset"
                )
            if key == "reasoning_effort":
                effort = _require_str(
                    value, field_name=f"models.{model}.parameter_presets.{preset_name}.{key}"
                )
                if effort not in efforts:
                    raise PolicyError(
                        f"models.{model}.parameter_presets.{preset_name}.reasoning_effort "
                        f"{effort!r} is not in that model's declared ladder "
                        f"({', '.join(sorted(efforts))}); a generic effort such as medium must "
                        f"never be applied to a model that does not support it"
                    )
                values[key] = effort
            else:
                values[key] = _require_bool(
                    value, field_name=f"models.{model}.parameter_presets.{preset_name}.{key}"
                )
        presets[preset_name] = MappingProxyType(values)
    if DEFAULT_PRESET not in presets:
        raise PolicyError(
            f"models.{model}.parameter_presets must define a {DEFAULT_PRESET!r} preset"
        )
    return presets


def _parse_model(raw: object) -> RoutingModel:
    mapping = _require_mapping(raw, field_name="models entry")
    model = _normalise_model_id(mapping.get("model"), field_name="models[].model")
    where = f"models.{model}"
    declared_developer = _require_str(mapping.get("developer"), field_name=f"{where}.developer")
    if declared_developer.lower() != developer_for_model(model):
        raise PolicyError(
            f"{where}.developer {declared_developer!r} does not match the model id's own "
            f"developer segment {developer_for_model(model)!r}"
        )
    raw_efforts = mapping.get("reasoning_efforts")
    if not isinstance(raw_efforts, list):
        raise PolicyError(f"{where}.reasoning_efforts must be a list of strings")
    efforts = frozenset(
        _require_str(item, field_name=f"{where}.reasoning_efforts") for item in raw_efforts
    )
    unknown_efforts = efforts - KNOWN_REASONING_EFFORTS
    if unknown_efforts:
        raise PolicyError(
            f"{where}.reasoning_efforts contains unknown effort(s): "
            f"{', '.join(sorted(unknown_efforts))}; known: "
            f"{', '.join(sorted(KNOWN_REASONING_EFFORTS))}"
        )
    raw_suitability = mapping.get("suitability")
    if not isinstance(raw_suitability, list) or not raw_suitability:
        raise PolicyError(f"{where}.suitability must be a non-empty list of profile names")
    suitability = frozenset(
        _require_str(item, field_name=f"{where}.suitability") for item in raw_suitability
    )
    unknown_profiles = suitability - ROUTING_PROFILES
    if unknown_profiles:
        raise PolicyError(
            f"{where}.suitability names unknown profile(s): {', '.join(sorted(unknown_profiles))}"
        )
    raw_sampling = mapping.get("sampling_parameters", [])
    if not isinstance(raw_sampling, list) or any(
        not isinstance(value, str) or value not in SAMPLING_PARAMETERS for value in raw_sampling
    ):
        raise PolicyError(f"{where}.sampling_parameters contains unsupported parameters")
    return RoutingModel(
        model=model,
        developer=developer_for_model(model),
        suitability=suitability,
        reasoning_efforts=efforts,
        sampling_parameters=frozenset(raw_sampling),
        presets=_parse_presets(mapping.get("parameter_presets", {}), model=model, efforts=efforts),
        notes=str(mapping.get("notes", "")),
    )


def _parse_group(
    raw: object,
    *,
    profile_name: str,
    index: int,
    seen: set[str],
) -> RoutingGroup:
    mapping = _require_mapping(raw, field_name=f"profiles.{profile_name}.groups[{index}]")
    name = _require_str(mapping.get("group"), field_name=f"profiles.{profile_name}.groups[].group")
    if name in seen:
        raise PolicyError(f"profiles.{profile_name} repeats group name {name!r}")
    seen.add(name)
    raw_models = mapping.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise PolicyError(f"profiles.{profile_name}.groups.{name}.models must be a non-empty list")
    models: list[str] = []
    for entry in raw_models:
        model = _normalise_model_id(
            entry, field_name=f"profiles.{profile_name}.groups.{name}.models"
        )
        if model in models:
            raise PolicyError(
                f"profiles.{profile_name}.groups.{name} lists {model!r} more than once"
            )
        models.append(model)
    return RoutingGroup(name=name, index=index, models=tuple(models))


def _parse_profile(raw: object) -> WorkloadProfile:
    mapping = _require_mapping(raw, field_name="profiles entry")
    name = _require_str(mapping.get("profile"), field_name="profiles[].profile")
    if name not in ROUTING_PROFILES:
        raise PolicyError(
            f"profiles[].profile {name!r} is not one of {', '.join(sorted(ROUTING_PROFILES))}"
        )
    where = f"profiles.{name}"
    raw_groups = mapping.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise PolicyError(f"{where}.groups must be a non-empty list")
    seen: set[str] = set()
    groups = tuple(
        _parse_group(entry, profile_name=name, index=index, seen=seen)
        for index, entry in enumerate(raw_groups)
    )
    diversity = _require_mapping(mapping.get("diversity", {}), field_name=f"{where}.diversity")
    distinct = _require_bool(
        diversity.get("distinct_developers", False),
        field_name=f"{where}.diversity.distinct_developers",
    )
    return WorkloadProfile(
        name=name,
        min_output_tokens=_require_positive_int(
            mapping.get("min_output_tokens"), field_name=f"{where}.min_output_tokens"
        ),
        allow_premium=_require_bool(
            mapping.get("allow_premium"), field_name=f"{where}.allow_premium"
        ),
        groups=groups,
        distinct_developers=distinct,
        notes=str(mapping.get("notes", "")),
    )


def parse_model_routing(data: object, *, source_path: str = "<memory>") -> ModelRouting:
    """Validate a routing mapping. Exposed for tests and deployments."""
    mapping = _require_mapping(data, field_name="model routing policy")
    version = mapping.get("version")
    if type(version) is not int or version != SUPPORTED_MODEL_ROUTING_VERSION:
        raise PolicyError(
            f"model routing version must be {SUPPORTED_MODEL_ROUTING_VERSION}, got {version!r}"
        )

    raw_models = mapping.get("models")
    if not isinstance(raw_models, list) or not raw_models:
        raise PolicyError("models must be a non-empty list")
    models: dict[str, RoutingModel] = {}
    for raw in raw_models:
        parsed = _parse_model(raw)
        if parsed.model in models:
            raise PolicyError(f"duplicate model entry: {parsed.model}")
        models[parsed.model] = parsed

    raw_profiles = mapping.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise PolicyError("profiles must be a non-empty list")
    profiles: dict[str, WorkloadProfile] = {}
    for raw in raw_profiles:
        parsed_profile = _parse_profile(raw)
        if parsed_profile.name in profiles:
            raise PolicyError(f"duplicate profile entry: {parsed_profile.name}")
        profiles[parsed_profile.name] = parsed_profile
    missing_profiles = ROUTING_PROFILES - set(profiles)
    if missing_profiles:
        raise PolicyError(
            f"profiles is missing required profile(s): {', '.join(sorted(missing_profiles))}"
        )

    # Cross-checks. A group may only name a model that is declared *and* placed in
    # this profile, and a placement that no profile uses is dead configuration that
    # would let an operator believe a model is routable when it is not.
    referenced: set[str] = set()
    for profile_name, profile in profiles.items():
        placed_in_profile: set[str] = set()
        for group in profile.groups:
            for model in group.models:
                if model in placed_in_profile:
                    raise PolicyError(
                        f"profiles.{profile_name} repeats model {model!r} across groups"
                    )
                placed_in_profile.add(model)
                referenced.add(model)
                declared = models.get(model)
                if declared is None:
                    raise PolicyError(
                        f"profiles.{profile_name}.groups.{group.name} names {model!r}, which is "
                        f"not declared in models"
                    )
                if profile_name not in declared.suitability:
                    raise PolicyError(
                        f"profiles.{profile_name}.groups.{group.name} names {model!r}, whose "
                        f"suitability is {sorted(declared.suitability)}; a model may only be "
                        f"placed in a profile that declares it suitable"
                    )
    unplaced = sorted(set(models) - referenced)
    if unplaced:
        raise PolicyError(
            f"models are declared but no profile group uses them: {', '.join(unplaced)}; "
            f"remove them or place them"
        )

    return ModelRouting(
        version=version,
        provisional=_require_bool(mapping.get("provisional", True), field_name="provisional"),
        models=MappingProxyType(models),
        profiles=MappingProxyType(profiles),
        source_path=source_path,
    )


def load_model_routing(path: str | Path | None = None) -> ModelRouting:
    """Load and validate the routing metadata. Cached per resolved path.

    The path may be overridden with ``DAEMON_MODEL_ROUTING`` so a deployment can
    ship its own placements without a code change. Nothing is fetched: this reads a
    local file and never contacts a model catalogue.
    """
    resolved = _resolve_path(path, DEFAULT_MODEL_ROUTING)
    return _load_model_routing_cached(str(resolved))


@lru_cache(maxsize=8)
def _load_model_routing_cached(resolved: str) -> ModelRouting:
    try:
        raw = Path(resolved).read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyError(f"cannot read routing file {resolved}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{resolved} is not valid JSON: {exc}") from exc
    return parse_model_routing(data, source_path=resolved)


def profile(name: str) -> WorkloadProfile:
    """Look up a validated workload profile by name. Unknown names fail closed."""
    return load_model_routing().profile(name)


def _resolve_path(path: str | Path | None, default: Path) -> Path:
    if path is not None:
        return Path(path)
    from orchestrator.config import get_settings

    configured = get_settings().daemon_model_routing
    if configured:
        return Path(configured)
    return default


# --------------------------------------------------------------------------- #
# Per-context routing state
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class RoutingState:
    """The routing decision scope for one unit of work.

    ``selected_model`` and ``selected_route_id`` are the **actual** route that was
    dispatched, written after selection, and are what telemetry and callers must
    read. They are deliberately mutable: a fallback rewrites them, so they always
    describe what really went out, never what was requested.
    """

    profile: str
    preferred_model: str | None = None
    excluded_models: frozenset[str] = frozenset()
    excluded_developers: frozenset[str] = frozenset()
    selected_model: str | None = None
    selected_route_id: str | None = None
    selected_group: str | None = None
    explicit: bool = False
    resolution: ModelRouting | None = field(default=None, repr=False, compare=False)

    def record_selection(
        self, *, model: str, route_id: str, group: str | None, explicit: bool
    ) -> None:
        self.selected_model = model
        self.selected_route_id = route_id
        self.selected_group = group
        self.explicit = explicit


_routing: ContextVar[RoutingState | None] = ContextVar("model_routing_state", default=None)


@contextmanager
def routing_context(
    profile: str,
    *,
    preferred_model: str | None = None,
    excluded_models: frozenset[str] = frozenset(),
    excluded_developers: frozenset[str] = frozenset(),
) -> Iterator[RoutingState]:
    """Bind a fresh, isolated :class:`RoutingState` for this profile.

    Synchronous on purpose: routing metadata is local, so entering a decision scope
    must never introduce an await point into a streaming or settlement path.

    * ``profile`` must be a configured workload profile. An unknown name raises
      :class:`RoutingError`; it never falls back to ``routine``, because a silent
      fallback would quietly downgrade a reasoning or council workload.
    * ``preferred_model`` is a *soft* preference. It reorders candidates only inside
      the group the profile already selected, and it cannot move a request to an
      earlier or later group, bypass the shortlist, or bypass capabilities,
      entitlements or bounds.
    * ``excluded_models`` / ``excluded_developers`` remove candidates outright. A
      council round uses ``excluded_developers`` to keep two perspectives off the
      same developer.

    Contexts nest safely: each entry creates its own state object, the enclosing
    state is restored on exit, and no state is ever shared between contexts.
    """
    resolution = load_model_routing()
    resolution.profile(profile)  # fail closed on an unknown profile
    state = RoutingState(
        profile=profile,
        preferred_model=preferred_model,
        excluded_models=frozenset(excluded_models),
        excluded_developers=frozenset(excluded_developers),
        resolution=resolution,
    )
    token: Token[RoutingState | None] = _routing.set(state)
    try:
        yield state
    finally:
        _routing.reset(token)


def current_routing() -> RoutingState:
    """The active routing state, or a fresh default routine state.

    The default is constructed per call on purpose. A shared mutable default would
    let one task's selection leak into another's attribution.
    """
    state = _routing.get()
    if state is None:
        return RoutingState(profile="routine")
    return state


def active_routing() -> RoutingState | None:
    """Return the bound state, distinguishing an empty child from no context."""
    return _routing.get()


# --------------------------------------------------------------------------- #
# Candidate matching and ranking
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RoutedModel:
    """One model the profile considers acceptable, with its position and ladder."""

    model: str
    developer: str
    group: str
    group_index: int
    position: int
    placement: int
    reasoning_efforts: frozenset[str]


def is_model_routable(
    profile_name: str,
    model: str,
    *,
    excluded_models: frozenset[str] = frozenset(),
    excluded_developers: frozenset[str] = frozenset(),
) -> bool:
    """Whether ``profile_name`` lists ``model`` as acceptable and it is not excluded."""
    return (
        profile_candidates(
            profile_name,
            excluded_models=excluded_models,
            excluded_developers=excluded_developers,
            model=model,
        )
        is not None
    )


def profile_candidate(
    profile_name: str,
    model: str,
    *,
    excluded_models: frozenset[str] = frozenset(),
    excluded_developers: frozenset[str] = frozenset(),
) -> RoutedModel | None:
    """The profile's entry for ``model``, or ``None`` when it is not acceptable.

    This is the whole profile shortlist test: membership in a group, matching the
    declared suitability, and not excluded by the caller.
    """
    routing = load_model_routing()
    return _lookup(routing, profile_name, model, excluded_models, excluded_developers)


def profile_candidates(
    profile_name: str,
    *,
    excluded_models: frozenset[str] = frozenset(),
    excluded_developers: frozenset[str] = frozenset(),
    model: str | None = None,
) -> tuple[RoutedModel, ...] | None:
    """The profile's acceptable models, in group order then configuration order.

    Returns a one-element tuple when ``model`` is supplied, or ``None`` when that one
    model is not acceptable, so an explicit selection can use the same exclusion
    rules as an automatic one.
    """
    routing = load_model_routing()
    if model is not None:
        found = _lookup(routing, profile_name, model, excluded_models, excluded_developers)
        return (found,) if found is not None else None
    profile = routing.profile(profile_name)
    placement = profile.placement()
    found_models: list[RoutedModel] = []
    for candidate in profile.ranked_models():
        entry = _lookup(routing, profile_name, candidate, excluded_models, excluded_developers)
        if entry is not None:
            found_models.append(entry)
    if not found_models:
        return None
    return tuple(sorted(found_models, key=lambda entry: placement[entry.model]))


def _lookup(
    routing: ModelRouting,
    profile_name: str,
    model: str,
    excluded_models: frozenset[str],
    excluded_developers: frozenset[str],
) -> RoutedModel | None:
    profile = routing.profile(profile_name)
    placement = profile.placement()
    if model not in placement:
        return None
    declared = routing.model(model)
    if declared is None or profile_name not in declared.suitability:
        return None
    if model in excluded_models or declared.developer in excluded_developers:
        return None
    group_name, position = _group_of(profile, model)
    return RoutedModel(
        model=model,
        developer=declared.developer,
        group=group_name,
        group_index=profile.group_index(group_name),
        position=position,
        placement=placement[model],
        reasoning_efforts=declared.reasoning_efforts,
    )


def _group_of(profile: WorkloadProfile, model: str) -> tuple[str, int]:
    for group in profile.groups:
        if model in group.models:
            return group.name, group.models.index(model)
    raise RoutingError("profile_unknown_group", f"{profile.name} does not place {model!r}")


def supports_reasoning_effort(model: str, effort: str) -> bool:
    """Whether ``model`` may be sent ``effort``.

    A model the routing config does not place has no declared ladder, so only the
    global allowlist applies. That is not a gap in qualification: an unplaced model
    still has to clear the inference policy gate before it can be dispatched.
    """
    if effort not in KNOWN_REASONING_EFFORTS:
        return False
    try:
        routing = load_model_routing()
    except PolicyError:
        return False
    declared = routing.model(model)
    if declared is None:
        return True
    return effort in declared.reasoning_efforts


def supports_sampling_parameters(
    model: str, params: Mapping[str, Any], *, allow_unknown: bool = False
) -> bool:
    """Do not spend on a candidate known to reject the caller's sampling controls."""
    required = SAMPLING_PARAMETERS & params.keys()
    if not required:
        return True
    declared = load_model_routing().model(model)
    # Manual/benchmark pins may be absent from workload metadata. Their approved
    # endpoint still enforces require_parameters; known incompatibilities never pass.
    if declared is None:
        return allow_unknown
    return required <= declared.sampling_parameters


# --------------------------------------------------------------------------- #
# Model-specific parameters
# --------------------------------------------------------------------------- #
def model_parameter_presets(model: str, profile_name: str | None = None) -> dict[str, Any]:
    """The centrally validated reasoning preset for ``model`` in ``profile_name``.

    The profile-specific preset is layered over ``default``. An unplaced model, or a
    profile with no override, yields the ``default`` preset; an unknown model yields
    nothing, because Daemon has no reviewed opinion about a model it does not place.
    """
    try:
        routing = load_model_routing()
    except PolicyError:
        return {}
    declared = routing.model(model)
    if declared is None:
        return {}
    preset: dict[str, Any] = dict(declared.presets.get(DEFAULT_PRESET, {}))
    if profile_name is not None and routing.has_profile(profile_name):
        preset.update(declared.presets.get(profile_name, {}))
    return preset


def apply_model_parameter_presets(
    params: Mapping[str, Any], model: str, profile_name: str | None = None
) -> dict[str, Any]:
    """Return ``params`` plus ``model``'s preset for values the caller did not set.

    Applied **after** selection, so the reasoning control matches the model that
    actually answered. A caller-supplied value is never overwritten; it is only
    validated, so an explicit request stays exact.

    Raises :class:`RoutingError` when the resulting reasoning control is not valid
    for this model, which is a hard failure rather than a silent rewrite.
    """
    merged = dict(params)
    for key, value in model_parameter_presets(model, profile_name).items():
        if key not in merged:
            merged[key] = value
    effort = merged.get("reasoning_effort")
    if effort is not None:
        if not isinstance(effort, str) or effort not in KNOWN_REASONING_EFFORTS:
            raise RoutingError(
                "reasoning_effort_unknown", f"unsupported reasoning effort {effort!r}"
            )
        if not supports_reasoning_effort(model, effort):
            raise RoutingError(
                "reasoning_effort_unsupported",
                f"{model} does not accept reasoning effort {effort!r}",
            )
    include = merged.get("include_reasoning")
    if include is not None and not isinstance(include, bool):
        raise RoutingError("reasoning_option_invalid", "include_reasoning must be a boolean")
    return merged


__all__ = [
    "DEFAULT_MODEL_ROUTING",
    "DEFAULT_PRESET",
    "KNOWN_REASONING_EFFORTS",
    "MODEL_ROUTING_ENV",
    "ModelRouting",
    "OPENROUTER_PREFIX",
    "PRESET_PARAMETERS",
    "ROUTING_PROFILES",
    "RoutedModel",
    "RoutingError",
    "RoutingGroup",
    "RoutingModel",
    "RoutingState",
    "SUPPORTED_MODEL_ROUTING_VERSION",
    "WorkloadProfile",
    "apply_model_parameter_presets",
    "current_routing",
    "developer_for_model",
    "is_model_routable",
    "load_model_routing",
    "model_parameter_presets",
    "parse_model_routing",
    "profile",
    "profile_candidate",
    "profile_candidates",
    "routing_context",
    "supports_reasoning_effort",
]
