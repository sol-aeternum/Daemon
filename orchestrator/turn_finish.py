"""Versioned terminal evidence; neither a dispatch permit nor a usage receipt."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Outcome: TypeAlias = Literal["complete", "incomplete", "failed", "cancelled", "unknown"]
ProviderReason: TypeAlias = Literal["stop", "length", "content_filter", "tool_calls"]


def _absent(value: object) -> bool:
    return value is None


class ProviderFinish(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    state: Literal["confirmed", "missing", "conflicting", "not_attempted"]
    reason: ProviderReason | None = Field(default=None, exclude_if=_absent)
    provenance: Literal["provider_wire"] | None = Field(default=None, exclude_if=_absent)
    dispatch_id: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$", exclude_if=_absent
    )

    @model_validator(mode="after")
    def _consistent(self) -> ProviderFinish:
        if self.state == "confirmed":
            if self.reason is None or self.provenance is None:
                raise ValueError("confirmed finish requires wire evidence")
        elif self.reason is not None or self.provenance is not None:
            raise ValueError("unconfirmed finish cannot claim a provider reason")
        if self.state == "not_attempted" and self.dispatch_id is not None:
            raise ValueError("an unattempted answer has no dispatch")
        return self


class LocalLimit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: int | float | None = Field(default=None, ge=0, exclude_if=_absent)
    unit: Literal["tokens", "microusd", "seconds", "attempts", "rounds", "requests"] | None = Field(
        default=None, exclude_if=_absent
    )
    binding: bool

    @model_validator(mode="after")
    def _consistent(self) -> LocalLimit:
        if (self.value is None) != (self.unit is None):
            raise ValueError("a numeric limit needs a unit")
        return self


class TurnFinish(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal[1] = 1
    outcome: Outcome
    reason: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    provider: ProviderFinish
    limits: list[LocalLimit] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def _consistent(self) -> TurnFinish:
        if self.outcome == "complete" and (
            self.reason != "provider_stop"
            or self.provider.reason != "stop"
            or any(limit.binding for limit in self.limits)
        ):
            raise ValueError("completion requires confirmed provider stop without a binding limit")
        return self

    @property
    def finish_reason(self) -> ProviderReason | None:
        """Standard protocol reason, not the underlying provider evidence."""
        if self.outcome == "complete":
            return "stop"
        if self.provider.reason == "length" and self.outcome == "incomplete":
            return "length"
        if self.provider.reason == "content_filter" and self.outcome == "failed":
            return "content_filter"
        return None


def parse_turn_finish(value: object) -> TurnFinish | None:
    # Old or malformed records are not a completion receipt.
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        return None
    try:
        finish = TurnFinish.model_validate(value)
    except ValidationError:
        return None
    return finish if finish.model_dump(exclude_none=True) == value else None


def answer_finish(evidence: object, limits: list[LocalLimit] | None = None) -> TurnFinish:
    try:
        provider = ProviderFinish.model_validate(evidence)
    except ValidationError:
        provider = ProviderFinish(state="missing")
    limits = limits or []
    match provider.reason:
        case "stop":
            return TurnFinish(
                outcome="complete", reason="provider_stop", provider=provider, limits=limits
            )
        case "length":
            return TurnFinish(
                outcome="incomplete", reason="provider_length", provider=provider, limits=limits
            )
        case "content_filter":
            return TurnFinish(
                outcome="failed", reason="provider_content_filter", provider=provider, limits=limits
            )
        case _:
            reason = (
                "finish_evidence_conflict"
                if provider.state == "conflicting" or provider.reason == "tool_calls"
                else "finish_evidence_missing"
            )
            return TurnFinish(outcome="unknown", reason=reason, provider=provider, limits=limits)


def unknown_finish(
    reason: str = "finish_evidence_missing", *, observed: TurnFinish | None = None
) -> TurnFinish:
    return TurnFinish(
        outcome="unknown",
        reason=reason,
        provider=observed.provider if observed is not None else ProviderFinish(state="missing"),
        limits=observed.limits if observed is not None else [],
    )


def refused_finish(code: str, *, category: str | None = None) -> TurnFinish:
    """Describe existing terminal refusals without changing their classifiers."""
    limit = {
        "budget_exceeded": "compute_budget",
        "extended_budget_exceeded": "extended_compute_budget",
        "extended_agents_exceeded": "extended_agent_runs",
        "trial_exhausted": "trial_budget",
        "trial_extended_agents_exhausted": "trial_extended_agent_runs",
        "context_limit": "context",
        "tool_price_exceeded": "tool_price",
        "embedding_price_exceeded": "embedding_price",
        "rate_limited": "rate",
        "concurrency_exceeded": "concurrency",
        "deadline_exceeded": "time",
    }.get("deadline_exceeded" if category == "deadline_exceeded" else code)
    return TurnFinish(
        outcome="incomplete" if limit else "failed",
        reason="deadline_exceeded" if category == "deadline_exceeded" else code,
        provider=ProviderFinish(state="missing"),
        limits=[LocalLimit(kind=limit, binding=True)] if limit else [],
    )
