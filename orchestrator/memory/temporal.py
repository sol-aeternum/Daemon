"""Explicit event time and committed knowledge cutoffs; never inferred from prose."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemporalRefusal(ValueError):
    """A truthful unavailable/indeterminate result, not an empty historical answer."""


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An explicit UTC offset is required; naive times are unresolved")
    return value.astimezone(UTC)


class EventScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime | None = None
    end: datetime | None = None
    start_kind: Literal["exact", "unbounded", "unknown"] = "unknown"
    end_kind: Literal["exact", "unbounded", "unknown"] = "unknown"
    precision: Literal["exact", "date", "month", "year", "approximate", "unknown"] = "unknown"
    expression: str | None = Field(default=None, max_length=2000)
    timezone: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_bounds(self) -> EventScope:
        for name in ("start", "end"):
            value = getattr(self, name)
            kind = getattr(self, name + "_kind")
            if (value is not None) != (kind == "exact"):
                raise ValueError("Only exact bounds have resolved timestamps")
            if value is not None:
                object.__setattr__(self, name, aware(value))
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("Event intervals are nonempty and half-open [start,end)")
        if self.precision != "exact" and (self.start is not None or self.end is not None):
            raise ValueError("Partial/approximate dates cannot be promoted to exact bounds")
        if self.timezone is not None:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Unknown IANA timezone") from None
        return self

    def storage(self) -> dict[str, object]:
        # The original expression is encrypted separately, not stored in JSONB.
        return self.model_dump(mode="json", exclude={"expression"})

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.storage(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class KnowledgeWatermark(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: UUID
    generation: int = Field(ge=0)
    sequence: int = Field(ge=0)


class TemporalReadScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    event_at: datetime | None = None
    known_at: datetime | None = None
    watermark: KnowledgeWatermark | None = None

    @model_validator(mode="after")
    def validate_cutoffs(self) -> TemporalReadScope:
        for name in ("event_at", "known_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, aware(value))
        if self.known_at is not None and self.watermark is not None:
            raise ValueError("Supply known_at or a watermark, not both")
        return self


HISTORICAL_CONTEXT_NOTE = (
    "Historical memory scope: only qualified memory revisions are evidence. "
    "Unversioned preferences, summaries, recent conversation history and derived context "
    "are excluded. The current request is an instruction, not evidence known at the cutoff. "
    "Permissions and deletion restrictions are current, not historical."
)
