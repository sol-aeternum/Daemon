"""Dev2 historical-clock remap for M2/M3 (#601).

M2 and M3 run on a remapped copy, ``daemon_lme_bench_dev2_final_remap``. The
ingest wrote every stored time from the ingest wall clock, so temporal windows
and recency would see the wrong clock; this module maps each user's ingest
window onto that question's haystack session dates, preserving every row's
relative order. The original ``daemon_lme_bench_dev2_final`` stays untouched as
evidence.

Method (carried over unchanged from the owner-approved 2026-10-31 dev-50
script, ``agent-output/remap-src/remap_historical_time.py``): per user, sessions
in haystack order. A timestamp ``t`` in ``[W_k, W_{k+1})`` maps to
``d_k + (t - W_k) * s_k`` where ``W_k`` is that session's first ingest message
time, ``d_k`` its haystack date, and ``s_k = min(1, 0.5 * gap / raw_span)``
shrinks the ingest-time offset to at most half of the gap to the next session's
date (or the question date for the last session). Times before ``W_1`` clamp to
``d_1``. Order within a user is preserved; equal-date sessions collapse to ties.

Subcommands, each writing to an exclusive file outside the repository:

``prepare``  fingerprint the original over all 100 frozen users, create the copy
             with ``CREATE DATABASE ... TEMPLATE``, apply the benchmark marker
             comment, and fingerprint the copy (must equal the original).
``remap``    dry run by default; ``--apply`` commits. Only ever the copy; the
             prepared report proves the copy is still the untouched clone, so a
             second apply refuses instead of mapping timestamps twice.
``verify``   read-only; re-fingerprints both databases, attributes memories
             through the apply report's session map and writes the lineage.

Column discovery is not a fixed list. Every timestamp column of the connected
schema is read from ``information_schema`` and must be either in :data:`COLUMNS`
(remapped) or in :data:`EXCLUDED` with a written reason; an unclassified column
refuses the run. Production code is unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from orchestrator.eval.bench_db import (
    BENCHMARK_DB_MARKER,
    fingerprint_benchmark_users,
    question_user_id,
)
from orchestrator.eval.fact_harness import _parse_haystack_date, question_as_of
from tests.benchmark_longmemeval.dev2_labelling import _write_document_atomic
from tests.benchmark_longmemeval.dev2_pilot import db_name_from_dsn
from tests.benchmark_longmemeval.m2_exposure import (
    FROZEN_IDS_PATH,
    _prepare_output,
    load_frozen_ids,
)

PREPARE_SCHEMA: Final[str] = "dev2-remap-prepare/1"
REMAP_SCHEMA: Final[str] = "dev2-remap-run/1"
LINEAGE_SCHEMA: Final[str] = "dev2-remap-lineage/1"
MAPPING_VERSION: Final[str] = "historical-clock-remap-2026-10-31"

SOURCE_DB_NAME: Final[str] = "daemon_lme_bench_dev2_final"
COPY_DB_NAME: Final[str] = "daemon_lme_bench_dev2_final_remap"

#: table -> timestamp columns to remap. Any other timestamp column on these
#: tables, and any table missing here or from EXCLUDED, refuses the run.
COLUMNS: Final[dict[str, tuple[str, ...]]] = {
    "memories": (
        "created_at",
        "updated_at",
        "valid_from",
        "valid_to",
        "last_accessed_at",
        "last_retrieved_at",
    ),
    "conversations": ("created_at", "updated_at", "last_activity_at", "summary_updated_at"),
    "messages": ("created_at",),
    "memory_extraction_log": ("created_at", "last_message_observed_at"),
    "entities": ("first_mentioned_at", "created_at", "updated_at"),
}

_MONEY: Final[str] = "money record, not user history; spend accounting reads it"
_IDENTITY: Final[str] = (
    "identity or session bookkeeping for the account, not user history; "
    "nothing in retrieval reads it"
)
_JOBS: Final[str] = "job or attempt infrastructure, not user history; retrieval never reads it"
_RESOURCES: Final[str] = (
    "resource or artifact bookkeeping, not user history; retrieval never reads it"
)
_SKILLS: Final[str] = "skill projection bookkeeping, not user history; retrieval never reads it"

#: table -> {timestamp column: reason}. Every timestamp column of the current
#: schema is either remapped (COLUMNS) or listed here with its reason.
EXCLUDED: Final[dict[str, dict[str, str]]] = {
    # Found on the live dev2 schema at the first dry run (2026-11-08): migration
    # bookkeeping, and read-only views over the entitlement ledger.
    "_migrations": {"applied_at": "schema migration bookkeeping, not user history"},
    "v_entitlement_account_summary": {"updated_at": "view over the entitlement ledger"},
    "v_entitlement_limit_encounter_summary": {
        "first_seen_at": "view over the entitlement ledger",
        "last_seen_at": "view over the entitlement ledger",
    },
    "account_deletions": {
        "billing_deadline": "account deletion scheduling, not user history",
        "closed_at": "account deletion scheduling, not user history",
        "drain_deadline": "account deletion scheduling, not user history",
        "requested_at": "account deletion scheduling, not user history",
        "revocation_deadline": "account deletion scheduling, not user history",
    },
    "account_email_ownership": {
        "ended_at": _IDENTITY,
        "started_at": _IDENTITY,
    },
    "artifact_output_intents": {"created_at": _RESOURCES},
    "artifact_output_jobs": {"created_at": _RESOURCES},
    "attempt_lifecycle_commands": {"committed_at": _JOBS},
    "attempt_lifecycle_control": {
        "attempt_started_at": _JOBS,
        "closed_at": _JOBS,
        "created_at": _JOBS,
    },
    "compute_reservation_metadata": {"created_at": _MONEY},
    "compute_scope_metadata": {"created_at": _MONEY},
    "council_sessions": {
        "created_at": "council bookkeeping, not user history; retrieval never reads it",
        "updated_at": "council bookkeeping, not user history; retrieval never reads it",
    },
    "devices": {"created_at": _IDENTITY, "last_seen_at": _IDENTITY, "revoked_at": _IDENTITY},
    "dream_log": {
        "run_completed_at": "background consolidation log, not user history",
        "run_started_at": "background consolidation log, not user history",
    },
    "email_challenges": {
        "consumed_at": _IDENTITY,
        "created_at": _IDENTITY,
        "expires_at": _IDENTITY,
        "locked_at": _IDENTITY,
    },
    "entitlement_accounts": {
        "created_at": _MONEY,
        "plan_changed_at": _MONEY,
        "updated_at": _MONEY,
    },
    "entitlement_limit_encounters": {"created_at": _MONEY},
    "entitlement_reservations": {"created_at": _MONEY, "settled_at": _MONEY, "updated_at": _MONEY},
    "entitlement_subscription_events": {"occurred_at": _MONEY, "received_at": _MONEY},
    "entitlement_usage_periods": {"updated_at": _MONEY, "window_started_at": _MONEY},
    "google_nonce_challenges": {
        "consumed_at": _IDENTITY,
        "created_at": _IDENTITY,
        "expires_at": _IDENTITY,
    },
    "identity_audit_log": {"created_at": _IDENTITY},
    "identity_providers": {"last_used_at": _IDENTITY, "linked_at": _IDENTITY},
    "inference_route_attestations": {
        "checked_at": "route attestation evidence, not user history",
    },
    "job_failures": {"created_at": _JOBS, "last_attempt_at": _JOBS},
    "pending_enrollments": {
        "consumed_at": _IDENTITY,
        "created_at": _IDENTITY,
        "expires_at": _IDENTITY,
    },
    "refresh_rotation_grace": {"created_at": _IDENTITY, "grace_expires_at": _IDENTITY},
    "resource_input_batches": {"created_at": _RESOURCES},
    "resource_input_intents": {"created_at": _RESOURCES},
    "resource_presentations": {"created_at": _RESOURCES},
    "resource_publications": {"committed_at": _RESOURCES},
    "resource_references": {"created_at": _RESOURCES},
    "resource_version_intents": {"created_at": _RESOURCES},
    "resource_versions": {"created_at": _RESOURCES},
    "resources": {"created_at": _RESOURCES},
    "retrieval_log": {
        "created_at": "must hold zero rows for M2; any row refuses the remap",
    },
    "sessions": {
        "access_expires_at": _IDENTITY,
        "created_at": _IDENTITY,
        "refresh_consumed_at": _IDENTITY,
        "refresh_expires_at": _IDENTITY,
        "revoked_at": _IDENTITY,
    },
    "signup_invites": {
        "consumed_at": _IDENTITY,
        "created_at": _IDENTITY,
        "expires_at": _IDENTITY,
        "terminal_at": _IDENTITY,
    },
    "skill_consolidation_log": {"run_at": _SKILLS, "skill_last_used_at": _SKILLS},
    "skill_nudge_user_state": {"last_nudge_at": _SKILLS},
    "skill_projections": {
        "created_at": _SKILLS,
        "last_synced_at": _SKILLS,
        "last_used_at": _SKILLS,
        "updated_at": _SKILLS,
    },
    "system_state": {
        "created_at": "controller bookkeeping, not user history",
        "updated_at": "controller bookkeeping, not user history",
    },
    "task_attempts": {
        "ended_at": _JOBS,
        "execution_started_at": _JOBS,
        "heartbeat_at": _JOBS,
        "started_at": _JOBS,
    },
    "task_events": {"created_at": _JOBS},
    "task_inference_recovery": {"created_at": _JOBS, "first_marked_at": _JOBS},
    "task_operations": {"completed_at": _JOBS, "started_at": _JOBS},
    "tasks": {
        "cancel_requested_at": _JOBS,
        "created_at": _JOBS,
        "finished_at": _JOBS,
        "last_wake_at": _JOBS,
        "lease_expires_at": _JOBS,
        "next_wakeup_at": _JOBS,
        "updated_at": _JOBS,
    },
    "tenant_memberships": {"created_at": "tenancy bookkeeping, not user history"},
    "tenants": {
        "created_at": "tenancy bookkeeping, not user history",
        "updated_at": "tenancy bookkeeping, not user history",
    },
    "users": {
        "created_at": "account row metadata, not user history; retrieval reads identity, not time",
        "email_verified_at": "account row metadata, not user history",
        "updated_at": "account row metadata, not user history",
    },
    "video_credit_balances": {"updated_at": _MONEY},
    "video_credit_transactions": {"created_at": _MONEY},
    "web_snapshots": {
        "created_at": "web capture bookkeeping, not user history; retrieval never reads it",
        "expires_at": "web capture bookkeeping, not user history; retrieval never reads it",
        "retrieved_at": "web capture bookkeeping, not user history; retrieval never reads it",
    },
}


class RemapError(RuntimeError):
    """A refusal: the remap would be unsafe, ambiguous or unverifiable."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Mapper:
    """One user's ingest-time -> historical-time map (2026-10-31 semantics)."""

    windows: tuple[datetime, ...]
    dates: tuple[datetime, ...]
    end: datetime
    question_at: datetime
    scales: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if not self.windows or len(self.windows) != len(self.dates):
            raise RemapError("A mapper needs one window and one date per session")
        scales: list[float] = []
        for index, start in enumerate(self.windows):
            raw_span = (
                self.windows[index + 1] if index + 1 < len(self.windows) else self.end
            ) - start
            target_gap = (
                self.dates[index + 1] if index + 1 < len(self.dates) else self.question_at
            ) - self.dates[index]
            if raw_span <= timedelta(0):
                scales.append(0.0)
            else:
                scales.append(min(1.0, 0.5 * target_gap / raw_span))
        object.__setattr__(self, "scales", tuple(scales))

    def __call__(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        moment = _utc(value)
        index = max(0, bisect.bisect_right(self.windows, moment) - 1)
        offset = max(timedelta(0), moment - self.windows[index])
        return self.dates[index] + offset * self.scales[index]


def assert_copy_database(name: str | None) -> str:
    """Refuse anything but the remapped copy, naming the original explicitly."""
    if name == SOURCE_DB_NAME:
        raise RemapError(f"Refusing to remap {SOURCE_DB_NAME}: the original stays as evidence")
    if name != COPY_DB_NAME:
        raise RemapError(f"Refusing to remap {name!r}; only {COPY_DB_NAME} may be changed")
    return COPY_DB_NAME


def assert_source_database(name: str | None) -> str:
    if name != SOURCE_DB_NAME:
        raise RemapError(f"Refusing source {name!r}; expected exactly {SOURCE_DB_NAME}")
    return SOURCE_DB_NAME


def classify_columns(discovered: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    """Every discovered timestamp column is remapped, excluded with a reason, or refuses."""
    for table, columns in sorted(discovered.items()):
        found = set(columns)
        if table in COLUMNS:
            missing = set(COLUMNS[table]) - found
            if missing:
                raise RemapError(f"{table}: remapped column(s) missing: {sorted(missing)}")
            unexpected = found - set(COLUMNS[table])
            if unexpected:
                raise RemapError(
                    f"{table}: unclassified timestamp column(s) {sorted(unexpected)}; "
                    "add them to COLUMNS or EXCLUDED with a reason"
                )
            continue
        if table in EXCLUDED:
            unexpected = found - set(EXCLUDED[table])
            if unexpected:
                raise RemapError(
                    f"{table}: unclassified timestamp column(s) {sorted(unexpected)}; "
                    "add them to EXCLUDED with a reason"
                )
            continue
        raise RemapError(
            f"{table}: table is neither remapped nor excluded; classify {sorted(found)}"
        )
    absent = sorted(set(COLUMNS) - set(discovered))
    if absent:
        raise RemapError(f"Remapped table(s) absent from the schema: {absent}")
    return {table: tuple(columns) for table, columns in COLUMNS.items()}


async def read_timestamp_columns(conn: Any) -> dict[str, tuple[str, ...]]:
    rows = await conn.fetch(
        """SELECT table_name, column_name FROM information_schema.columns
           WHERE table_schema = 'public'
             AND data_type IN ('timestamp with time zone', 'timestamp without time zone')
           ORDER BY table_name, column_name"""
    )
    discovered: dict[str, list[str]] = {}
    for row in rows:
        discovered.setdefault(str(row["table_name"]), []).append(str(row["column_name"]))
    return {table: tuple(columns) for table, columns in discovered.items()}


def column_inventory(discovered: Mapping[str, Sequence[str]]) -> dict[str, dict[str, str]]:
    """Every discovered column with its disposition; the report cannot omit one."""
    inventory: dict[str, dict[str, str]] = {}
    for table, columns in sorted(discovered.items()):
        for column in columns:
            if table in COLUMNS and column in COLUMNS[table]:
                inventory.setdefault(table, {})[column] = "remapped"
            elif table in EXCLUDED and column in EXCLUDED[table]:
                inventory.setdefault(table, {})[column] = EXCLUDED[table][column]
            else:
                inventory.setdefault(table, {})[column] = "UNCLASSIFIED"
    return inventory


def load_dataset(path: Path | str, frozen: Any) -> list[dict[str, Any]]:
    """The dataset entries for exactly the 100 frozen ids, each with its sessions."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RemapError("Dataset must be a JSON list")
    wanted = set(frozen.ids)
    entries: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise RemapError("Dataset rows must be objects")
        question_id = row.get("question_id")
        if not isinstance(question_id, str) or question_id not in wanted:
            continue
        if question_id in entries:
            raise RemapError(f"Duplicate question in the dataset: {question_id}")
        entries[question_id] = dict(row)
    missing = sorted(wanted - set(entries))
    if missing:
        raise RemapError(f"Dataset is missing {len(missing)} frozen question(s)")
    ordered = [entries[question_id] for question_id in sorted(entries)]
    for entry in ordered:
        _session_schedule(entry)
    return ordered


def _session_schedule(entry: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[datetime, ...]]:
    session_ids = entry.get("haystack_session_ids")
    raw_dates = entry.get("haystack_dates")
    if not isinstance(session_ids, list) or not isinstance(raw_dates, list):
        raise RemapError("Dataset entry has no haystack sessions")
    if not session_ids or len(session_ids) != len(raw_dates):
        raise RemapError("Dataset entry has inconsistent haystack session data")
    if any(not isinstance(value, str) or not value for value in session_ids):
        raise RemapError("Dataset entry has an invalid session id")
    dates: list[datetime] = []
    for raw in raw_dates:
        parsed = _parse_haystack_date(raw)
        if parsed is None:
            raise RemapError("Dataset entry has an unparsable haystack date")
        dates.append(parsed.replace(tzinfo=UTC))
    return tuple(session_ids), tuple(dates)


@dataclass(frozen=True, slots=True)
class SessionWindow:
    """One session's target date range.

    A session's mapped value must be at or after its own date. It must be before
    the next session's date, except when consecutive sessions share a date (a
    tie): a tied session's memories collapse to exactly that date, so the bound
    is inclusive there. The last session is strict against the question date.
    """

    lower: datetime
    upper: datetime
    upper_inclusive: bool

    def contains(self, value: datetime) -> bool:
        if value < self.lower:
            return False
        if self.upper_inclusive:
            return value <= self.upper
        return value < self.upper


def memory_session_bounds(
    session_ids: Sequence[str], dates: Sequence[datetime], question_at: datetime
) -> dict[str, SessionWindow]:
    """session id -> its date range; a tied next date makes the upper bound inclusive."""
    bounds: dict[str, SessionWindow] = {}
    for index, session_id in enumerate(session_ids):
        lower = dates[index]
        if index + 1 < len(dates):
            upper = dates[index + 1]
            inclusive = upper == lower
        else:
            upper = question_at
            inclusive = False
        bounds[session_id] = SessionWindow(lower, upper, inclusive)
    return bounds


def check_memory_windows(
    rows: Sequence[Mapping[str, Any]], bounds: Mapping[str, SessionWindow]
) -> int:
    """Every memory's valid_from is inside its own session's window; refuses otherwise."""
    checked = 0
    for row in rows:
        session_id = row.get("session_id")
        if not isinstance(session_id, str) or session_id not in bounds:
            raise RemapError("A memory cannot be attributed to one of its user's haystack sessions")
        value = row.get("valid_from")
        if not isinstance(value, datetime):
            raise RemapError("A memory has no valid_from")
        if not bounds[session_id].contains(_utc(value)):
            raise RemapError(f"Memory {row.get('id')} has valid_from outside its session's window")
        checked += 1
    return checked


async def build_plans(
    conn: Any, dataset: Sequence[Mapping[str, Any]]
) -> tuple[dict[Any, Mapper], dict[Any, dict[str, Any]]]:
    """One mapper and one report plan per benchmark user, from the copy's own messages."""
    mappers: dict[Any, Mapper] = {}
    plans: dict[Any, dict[str, Any]] = {}
    for entry in dataset:
        question_id = entry["question_id"]
        user_id = question_user_id(question_id)
        session_ids, dates = _session_schedule(entry)
        date_by_session = dict(zip(session_ids, dates, strict=True))
        try:
            question_at = question_as_of(entry)
        except ValueError:
            raise RemapError(f"{question_id}: question_date missing or unparsable") from None
        rows = await conn.fetch(
            """SELECT conversation_id, min(created_at) AS first, max(created_at) AS last,
                      array_agg(DISTINCT metadata->>'session_id') AS sessions,
                      bool_and(metadata->>'source' = 'longmemeval') AS ours
               FROM messages WHERE user_id = $1
               GROUP BY conversation_id ORDER BY min(created_at)""",
            user_id,
        )
        if not rows:
            raise RemapError(f"{question_id}: no messages for its benchmark user")
        windows: list[datetime] = []
        session_dates: list[datetime] = []
        last_raw: list[datetime] = []
        conversations: dict[str, str] = {}
        for row in rows:
            sessions = row["sessions"]
            if (
                not row["ours"]
                or not isinstance(sessions, list)
                or len(sessions) != 1
                or sessions[0] not in date_by_session
            ):
                raise RemapError(
                    f"{question_id}: conversation {row['conversation_id']} is not one session"
                )
            windows.append(_utc(row["first"]))
            session_dates.append(date_by_session[sessions[0]])
            last_raw.append(_utc(row["last"]))
            conversations[str(row["conversation_id"])] = sessions[0]
        for index in range(1, len(windows)):
            if last_raw[index - 1] >= windows[index]:
                raise RemapError(f"{question_id}: session ingest windows overlap")
            if session_dates[index] < session_dates[index - 1]:
                raise RemapError(f"{question_id}: ingest order differs from haystack order")
        if question_at <= session_dates[-1]:
            raise RemapError(f"{question_id}: question_date is not after the last session")
        end = await conn.fetchval(
            """SELECT max(t) FROM (
                 SELECT max(greatest(created_at, updated_at, valid_from,
                                     coalesce(valid_to, created_at))) AS t
                   FROM memories WHERE user_id = $1
                 UNION ALL SELECT max(created_at) FROM memory_extraction_log WHERE user_id = $1
                 UNION ALL SELECT max(greatest(created_at, updated_at)) FROM entities
                  WHERE user_id = $1
                 UNION ALL SELECT max(greatest(created_at, updated_at, last_activity_at))
                   FROM conversations WHERE user_id = $1) x""",
            user_id,
        )
        if not isinstance(end, datetime):
            raise RemapError(f"{question_id}: no stored time to bound its last window")
        end = max(_utc(end), last_raw[-1]) + timedelta(seconds=1)
        mapper = Mapper(tuple(windows), tuple(session_dates), end, question_at)
        if len(windows) != len(session_ids):
            raise RemapError(f"{question_id}: stored sessions do not cover its haystack")
        mappers[user_id] = mapper
        plans[user_id] = {
            "question_id": question_id,
            "sessions": len(windows),
            "question_at": question_at,
            "collapsed_ties": sum(scale == 0.0 for scale in mapper.scales),
            "min_scale": min(mapper.scales),
            "bounds": memory_session_bounds(session_ids, dates, question_at),
            "conversations": conversations,
        }
    return mappers, plans


async def _assert_no_user_triggers(conn: Any) -> None:
    """A trigger could overwrite a remapped value; the 2026-10-31 script refused them."""
    for table in sorted(COLUMNS):
        count = await conn.fetchval(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = $1::regclass AND NOT tgisinternal",
            table,
        )
        if count:
            raise RemapError(f"{table}: has user triggers; a remap could be overwritten")


async def _assert_user_owned(conn: Any) -> None:
    for table in sorted(COLUMNS):
        present = await conn.fetchval(
            """SELECT count(*) FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = $1 AND column_name = 'user_id'""",
            table,
        )
        if not present:
            raise RemapError(f"{table}: has no user_id column")


async def _remap_table(
    conn: Any,
    table: str,
    mappers: Mapping[Any, Mapper],
    expected: dict[str, dict[Any, tuple[datetime | None, ...]]],
) -> dict[str, Any]:
    columns = COLUMNS[table]
    rows = await conn.fetch(
        f"SELECT id, user_id, {', '.join(columns)} FROM {table}"  # nosec B608 - fixed identifiers
    )
    foreign = sum(1 for row in rows if row["user_id"] not in mappers)
    if foreign:
        raise RemapError(f"{table}: {foreign} rows belong to non-benchmark users")
    updates: list[tuple[Any, ...]] = []
    non_null = dict.fromkeys(columns, 0)
    for row in rows:
        mapper = mappers[row["user_id"]]
        mapped = [mapper(row[column]) for column in columns]
        for column, value in zip(columns, mapped, strict=True):
            if value is not None:
                non_null[column] += 1
        updates.append((row["id"], *mapped))
    if updates:
        assignments = ", ".join(f"{column} = ${index + 2}" for index, column in enumerate(columns))
        await conn.executemany(
            f"UPDATE {table} SET {assignments} WHERE id = $1",  # nosec B608 - fixed identifiers
            updates,
        )
    # Order preservation: within a user and column, old order == new order (ties allowed).
    checks = 0
    grouped: dict[Any, list[tuple[Mapping[str, Any], Sequence[datetime | None]]]] = {}
    for row, update in zip(rows, updates, strict=True):
        grouped.setdefault(row["user_id"], []).append((row, update[1:]))
    for pairs in grouped.values():
        for index, column in enumerate(columns):
            values = [
                (_utc(row[column]), update[index])
                for row, update in pairs
                if row[column] is not None
            ]
            values.sort(key=lambda pair: pair[0])
            mapped_only: list[datetime] = []
            for pair in values:
                if not isinstance(pair[1], datetime):
                    raise RemapError(f"{table}.{column}: a remapped value went missing")
                mapped_only.append(pair[1])
            if any(a > b for a, b in zip(mapped_only, mapped_only[1:])):
                raise RemapError(f"{table}.{column}: order was not preserved")
            checks += 1
    expected[table] = {
        row["id"]: tuple(update[1:]) for row, update in zip(rows, updates, strict=True)
    }
    return {
        "rows": len(rows),
        "order_checks": checks,
        "columns": {
            column: {"rows": len(rows), "non_null": non_null[column]} for column in columns
        },
    }


async def _assert_values_stuck(
    conn: Any, expected: Mapping[str, Mapping[Any, tuple[datetime | None, ...]]]
) -> None:
    """Re-read every remapped value: nothing (a trigger, a rule) may have replaced it."""
    for table, rows in expected.items():
        columns = COLUMNS[table]
        found = await conn.fetch(
            f"SELECT id, {', '.join(columns)} FROM {table}"  # nosec B608 - fixed identifiers
        )
        current = {row["id"]: tuple(row[column] for column in columns) for row in found}
        if set(current) != set(rows):
            raise RemapError(f"{table}: row set changed during the remap")
        for row_id, values in rows.items():
            for column, expected_value, actual_value in zip(
                columns, values, current[row_id], strict=True
            ):
                if expected_value is None and actual_value is None:
                    continue
                if (
                    not isinstance(expected_value, datetime)
                    or not isinstance(actual_value, datetime)
                    or _utc(expected_value) != _utc(actual_value)
                ):
                    raise RemapError(
                        f"{table}.{column}: remapped value did not persist for {row_id}"
                    )


async def _prepared_fingerprint_check(
    conn: Any,
    dataset: Sequence[Mapping[str, Any]],
    prepared: Mapping[str, Any],
    name: str,
) -> str:
    """Refuse unless the copy still has the fingerprint ``prepare`` recorded.

    A second ``--apply`` would map already-remapped timestamps again; the
    fingerprint is the only evidence that this copy is still the untouched
    clone, so the check runs before any row is written.
    """
    if prepared.get("schema") != PREPARE_SCHEMA:
        raise RemapError("Prepared report has the wrong schema")
    if prepared.get("copy", {}).get("database") != name:
        raise RemapError("Prepared report names a different copy database")
    expected = prepared.get("copy", {}).get("fingerprint_sha256")
    if not isinstance(expected, str) or not expected:
        raise RemapError("Prepared report has no copy fingerprint")
    user_ids = [question_user_id(entry["question_id"]) for entry in dataset]
    live = await fingerprint_benchmark_users(conn, user_ids)
    if live["sha256"] != expected:
        raise RemapError(
            "The copy no longer matches the prepared fingerprint: it was already remapped "
            "or changed since prepare. Refusing to map timestamps twice."
        )
    return expected


async def run_remap(
    *,
    conn: Any,
    dataset: Sequence[Mapping[str, Any]],
    apply: bool,
    prepared: Mapping[str, Any],
    prepared_path: Path | str,
    output_path: Path | str,
    repo_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Dry run (rolls back) or apply (commits). ``conn`` must already be the copy."""
    output = _prepare_output(output_path, repo_root)
    name = await conn.fetchval("SELECT current_database()")
    assert_copy_database(name)
    document: dict[str, Any] = {
        "schema": REMAP_SCHEMA,
        "mode": "apply" if apply else "dry_run",
        "mapping_version": MAPPING_VERSION,
        "at": (clock() if clock is not None else datetime.now(UTC)).isoformat(),
        "database": name,
    }
    discovered = await read_timestamp_columns(conn)
    classify_columns(discovered)
    document["columns"] = column_inventory(discovered)
    await _assert_user_owned(conn)
    await _assert_no_user_triggers(conn)
    retrieval_rows = await conn.fetchval("SELECT count(*) FROM retrieval_log")
    if retrieval_rows:
        raise RemapError(f"retrieval_log holds {retrieval_rows} row(s); M2 requires it to be empty")
    document["retrieval_log_rows"] = 0
    explicit = await conn.fetchval(
        "SELECT count(*) FROM memories WHERE lower(source_type) = 'user_created'"
    )
    if explicit:
        # Dedup's explicit-memory suppression window compares wall-clock now against
        # user_created rows; a remap cannot reproduce that.
        raise RemapError(f"{explicit} user_created memories; remap fidelity is unproven")
    document["user_created_memories"] = 0
    prepared_fingerprint = await _prepared_fingerprint_check(conn, dataset, prepared, name)
    document["prepared"] = {
        "path": str(prepared_path),
        "sha256": _sha256_file(prepared_path),
        "copy_fingerprint_sha256": prepared_fingerprint,
    }

    transaction = conn.transaction(isolation="serializable")
    plans: dict[Any, dict[str, Any]] = {}
    await transaction.start()
    try:
        mappers, plans = await build_plans(conn, dataset)
        expected: dict[str, dict[Any, tuple[datetime | None, ...]]] = {}
        tables = {
            table: await _remap_table(conn, table, mappers, expected) for table in sorted(COLUMNS)
        }
        await _assert_values_stuck(conn, expected)
        document["tables"] = tables

        invalid = await conn.fetchval(
            "SELECT count(*) FROM memories WHERE valid_to IS NOT NULL AND valid_to < valid_from"
        )
        if invalid:
            raise RemapError(f"{invalid} memories with valid_to < valid_from after the remap")
        late = 0
        windows_checked = 0
        for user_id, plan in plans.items():
            late += await conn.fetchval(
                """SELECT count(*) FROM memories WHERE user_id = $1 AND
                   greatest(created_at, updated_at, valid_from,
                            coalesce(valid_to, created_at)) >= $2""",
                user_id,
                plan["question_at"],
            )
            # Attribute through the plan's message-derived map: ingest records the
            # session on messages, never on conversations (live schema, 2026-11-08).
            sessions_of = plan["conversations"]
            stored = await conn.fetch(
                "SELECT id, valid_from, source_conversation_id FROM memories WHERE user_id = $1",
                user_id,
            )
            rows = [
                {
                    "id": row["id"],
                    "valid_from": row["valid_from"],
                    "session_id": sessions_of.get(str(row["source_conversation_id"]))
                    if row["source_conversation_id"] is not None
                    else None,
                }
                for row in stored
            ]
            windows_checked += check_memory_windows(rows, plan["bounds"])
        if late:
            raise RemapError(f"{late} memory timestamps at or after the question date")
        document["invariants"] = {
            "valid_to_before_valid_from": 0,
            "memory_timestamps_at_or_after_question": 0,
            "memory_windows_checked": windows_checked,
            "values_stuck": True,
        }
        document["users"] = len(plans)
        document["sessions"] = sum(plan["sessions"] for plan in plans.values())
        document["collapsed_tie_sessions"] = sum(plan["collapsed_ties"] for plan in plans.values())
        document["min_scale"] = min(plan["min_scale"] for plan in plans.values())
        document["sessions_per_user"] = {
            str(user_id): plan["sessions"] for user_id, plan in sorted(plans.items(), key=str)
        }
        document["session_map"] = {
            str(user_id): plan["conversations"] for user_id, plan in sorted(plans.items(), key=str)
        }
    except BaseException:
        await transaction.rollback()
        raise

    # The commit ends the transaction block: nothing below may roll it back.
    if apply:
        try:
            await transaction.commit()
        except BaseException:
            try:
                await transaction.rollback()
            except Exception:
                pass
            raise
        document["committed"] = True
    else:
        await transaction.rollback()
        document["committed"] = False

    if apply:
        document["post_commit"] = "committed-unverified"
        try:
            fingerprint = await fingerprint_benchmark_users(conn, list(plans))
        except BaseException as error:
            # The rows are already durable; say so and keep the report.
            _write_document_atomic(output, document)
            raise RemapError(
                "The remap committed but its post-commit verification failed: the copy is "
                "remapped and unverified. Do not re-run the remap; inspect the report."
            ) from error
        document["remapped_fingerprint_sha256"] = fingerprint["sha256"]
        document["post_commit"] = "committed-verified"
    _write_document_atomic(output, document)
    return document


async def prepare_copy(
    *,
    control: Any,
    control_dsn: str,
    connect: Callable[[str], Awaitable[Any]],
    source_dsn: str,
    copy_dsn: str,
    frozen: Any,
    output_path: Path | str,
    repo_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Create and fingerprint the copy; the source is read-only except as TEMPLATE."""
    output = _prepare_output(output_path, repo_root)
    source_name = assert_source_database(db_name_from_dsn(source_dsn))
    copy_name = assert_copy_database(db_name_from_dsn(copy_dsn))
    if source_name == copy_name:
        raise RemapError("Source and copy must be distinct databases")
    control_name = db_name_from_dsn(control_dsn)
    if control_name in (source_name, copy_name):
        raise RemapError("The control connection must not be the source or the copy")

    source = await control.fetchrow(
        "SELECT oid, datallowconn, shobj_description(oid, 'pg_database') AS marker "
        "FROM pg_database WHERE datname = $1",
        source_name,
    )
    if source is None:
        raise RemapError(f"Source database {source_name} does not exist")
    if source["marker"] != BENCHMARK_DB_MARKER:
        raise RemapError("Source database has no benchmark marker")
    sessions = await control.fetchval(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = $1", source_name
    )
    if sessions:
        raise RemapError(f"Source database {source_name} still has {sessions} session(s)")
    exists = await control.fetchval(
        "SELECT count(*) FROM pg_database WHERE datname = $1", copy_name
    )
    if exists:
        raise RemapError(f"Copy database {copy_name} already exists")
    document: dict[str, Any] = {
        "schema": PREPARE_SCHEMA,
        "mapping_version": MAPPING_VERSION,
        "at": (clock() if clock is not None else datetime.now(UTC)).isoformat(),
        "source": {
            "database": source_name,
            "oid": source["oid"],
            "marker": source["marker"],
            "sessions": 0,
        },
        "copy": {"database": copy_name},
        "frozen_ids_sha256": frozen.sha256,
        "users": len(frozen.ids),
    }
    await control.execute(
        f"CREATE DATABASE {copy_name} TEMPLATE {source_name}"  # nosec B608 - fixed identifiers
    )
    marker = await control.fetchval(
        "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = $1",
        copy_name,
    )
    applied = marker != BENCHMARK_DB_MARKER
    if applied:
        await control.execute(
            f"COMMENT ON DATABASE {copy_name} IS '{BENCHMARK_DB_MARKER}'"  # nosec B608 - fixed identifiers
        )
        marker = await control.fetchval(
            "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = $1",
            copy_name,
        )
    if marker != BENCHMARK_DB_MARKER:
        raise RemapError("Copy database did not keep the benchmark marker")
    document["copy"].update(
        {
            "oid": await control.fetchval(
                "SELECT oid FROM pg_database WHERE datname = $1", copy_name
            ),
            "marker": marker,
            "marker_applied": applied,
        }
    )
    user_ids = [question_user_id(question_id) for question_id in frozen.ids]
    source_conn = await connect(source_dsn)
    try:
        source_fingerprint = await fingerprint_benchmark_users(source_conn, user_ids)
    finally:
        await source_conn.close()
    copy_conn = await connect(copy_dsn)
    try:
        copy_fingerprint = await fingerprint_benchmark_users(copy_conn, user_ids)
    finally:
        await copy_conn.close()
    document["source"]["fingerprint_sha256"] = source_fingerprint["sha256"]
    document["source"]["rows"] = source_fingerprint["rows"]
    document["copy"]["fingerprint_sha256"] = copy_fingerprint["sha256"]
    if copy_fingerprint["sha256"] != source_fingerprint["sha256"]:
        raise RemapError("Copy fingerprint differs from the untouched original; stop")
    _write_document_atomic(output, document)
    return document


async def verify_lineage(
    *,
    source_conn: Any,
    copy_conn: Any,
    prepared: Mapping[str, Any],
    apply_report: Mapping[str, Any],
    dataset: Sequence[Mapping[str, Any]],
    dataset_path: Path | str,
    script_path: Path | str,
    prepared_path: Path | str,
    apply_report_path: Path | str,
    output_path: Path | str,
    frozen: Any,
    repo_root: Path | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Read-only: the original is unchanged, the copy holds the remapped state.

    Ingest windows are never re-derived from the remapped copy: its stored times
    are already historical, so re-deriving windows would see the overlaps the
    remap itself created. Target dates come from the dataset, and each memory is
    attributed through the session map the apply report recorded.
    """
    output = _prepare_output(output_path, repo_root)
    if prepared.get("schema") != PREPARE_SCHEMA:
        raise RemapError("Prepared report has the wrong schema")
    if apply_report.get("schema") != REMAP_SCHEMA:
        raise RemapError("Remap report has the wrong schema")
    if apply_report.get("committed") is not True:
        raise RemapError("Remap report is a dry run; there is no applied state to verify")
    recorded_prepared = apply_report.get("prepared")
    if not isinstance(recorded_prepared, Mapping):
        raise RemapError("The apply report does not name a prepared report")
    if recorded_prepared.get("sha256") != _sha256_file(prepared_path):
        raise RemapError("The apply report does not name this prepared report")
    if recorded_prepared.get("copy_fingerprint_sha256") != prepared.get("copy", {}).get(
        "fingerprint_sha256"
    ):
        raise RemapError("The apply report was made against a different prepared fingerprint")
    source_name = assert_source_database(await source_conn.fetchval("SELECT current_database()"))
    copy_name = assert_copy_database(await copy_conn.fetchval("SELECT current_database()"))
    expected_source = prepared.get("source", {}).get("fingerprint_sha256")
    if prepared.get("source", {}).get("database") != source_name:
        raise RemapError("Prepared report names a different source database")
    if prepared.get("copy", {}).get("database") != copy_name:
        raise RemapError("Prepared report names a different copy database")
    user_ids = [question_user_id(question_id) for question_id in frozen.ids]
    source_fingerprint = await fingerprint_benchmark_users(source_conn, user_ids)
    if source_fingerprint["sha256"] != expected_source:
        raise RemapError("The original database changed since it was fingerprinted")
    remapped_fingerprint = await fingerprint_benchmark_users(copy_conn, user_ids)
    recorded = apply_report.get("remapped_fingerprint_sha256")
    if not recorded:
        raise RemapError("Remap report has no post-commit fingerprint; the apply is unverified")
    if recorded != remapped_fingerprint["sha256"]:
        raise RemapError("The copy does not match the remapped state its report recorded")
    session_maps = apply_report.get("session_map")
    if not isinstance(session_maps, Mapping):
        raise RemapError("Remap report has no session map; memories cannot be attributed")
    windows_checked = 0
    for entry in dataset:
        question_id = entry["question_id"]
        user_id = question_user_id(question_id)
        session_ids, dates = _session_schedule(entry)
        try:
            question_at = question_as_of(entry)
        except ValueError:
            raise RemapError(f"{question_id}: question_date missing or unparsable") from None
        recorded_sessions = session_maps.get(str(user_id))
        if not isinstance(recorded_sessions, Mapping):
            raise RemapError(f"{question_id}: the apply report has no session map")
        recorded_values = list(recorded_sessions.values())
        if (
            len(recorded_values) != len(session_ids)
            or any(not isinstance(value, str) for value in recorded_values)
            or set(recorded_values) != set(session_ids)
        ):
            raise RemapError(
                f"{question_id}: the apply report's session map does not match the dataset"
            )
        bounds = memory_session_bounds(session_ids, dates, question_at)
        rows = await copy_conn.fetch(
            """SELECT m.id, m.valid_from, m.source_conversation_id
               FROM memories m WHERE m.user_id = $1""",
            user_id,
        )
        attributed: list[dict[str, Any]] = []
        for row in rows:
            conversation_id = row["source_conversation_id"]
            session_id = (
                recorded_sessions.get(str(conversation_id)) if conversation_id is not None else None
            )
            if not isinstance(session_id, str):
                raise RemapError(f"Memory {row['id']} cannot be attributed to one of its sessions")
            attributed.append(
                {"id": row["id"], "session_id": session_id, "valid_from": row["valid_from"]}
            )
        windows_checked += check_memory_windows(attributed, bounds)
    document = {
        "schema": LINEAGE_SCHEMA,
        "mapping_version": MAPPING_VERSION,
        "at": (clock() if clock is not None else datetime.now(UTC)).isoformat(),
        "source": {
            "database": source_name,
            "fingerprint_sha256": source_fingerprint["sha256"],
            "prepared_report_sha256": _sha256_file(prepared_path),
        },
        "remapped": {
            "database": copy_name,
            "fingerprint_sha256": remapped_fingerprint["sha256"],
            "apply_report_sha256": _sha256_file(apply_report_path),
        },
        "dataset": {"path": str(dataset_path), "sha256": _sha256_file(dataset_path)},
        "mapping_script": {"path": str(script_path), "sha256": _sha256_file(script_path)},
        "frozen_ids_sha256": frozen.sha256,
        "users": len(user_ids),
        "memory_windows_checked": windows_checked,
        "result": "ok",
    }
    _write_document_atomic(output, document)
    return document


def _sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_report(path: Path | str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RemapError(f"Report is unreadable: {path}") from None
    if not isinstance(value, Mapping):
        raise RemapError(f"Report is not an object: {path}")
    return value


# --- CLI ---------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tests.benchmark_longmemeval.dev2_remap",
        description="Benchmark-only historical timestamp remap for the dev2 corpus.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Create and fingerprint the remapped copy.")
    prepare.add_argument("--control-dsn", required=True)
    prepare.add_argument("--source-dsn", required=True)
    prepare.add_argument("--copy-dsn", required=True)
    prepare.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--repo-root", type=Path, default=None, help=argparse.SUPPRESS)

    remap = sub.add_parser("remap", help="Remap the copy; dry run unless --apply.")
    remap.add_argument("--dsn", required=True)
    remap.add_argument("--dataset", type=Path, required=True)
    remap.add_argument("--prepared", type=Path, required=True)
    remap.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    remap.add_argument("--output", type=Path, required=True)
    remap.add_argument("--apply", action="store_true")
    remap.add_argument("--repo-root", type=Path, default=None, help=argparse.SUPPRESS)

    verify = sub.add_parser("verify", help="Read-only lineage verification after --apply.")
    verify.add_argument("--source-dsn", required=True)
    verify.add_argument("--copy-dsn", required=True)
    verify.add_argument("--dataset", type=Path, required=True)
    verify.add_argument("--prepared", type=Path, required=True)
    verify.add_argument("--apply-report", type=Path, required=True)
    verify.add_argument("--frozen-ids", type=Path, default=FROZEN_IDS_PATH)
    verify.add_argument("--output", type=Path, required=True)
    verify.add_argument("--repo-root", type=Path, default=None, help=argparse.SUPPRESS)
    return parser


async def _run_prepare(args: argparse.Namespace, frozen: Any) -> Mapping[str, Any]:
    import asyncpg

    control = await asyncpg.connect(args.control_dsn, timeout=10, command_timeout=600)
    try:
        return await prepare_copy(
            control=control,
            control_dsn=args.control_dsn,
            connect=lambda dsn: asyncpg.connect(dsn, timeout=10, command_timeout=600),
            source_dsn=args.source_dsn,
            copy_dsn=args.copy_dsn,
            frozen=frozen,
            output_path=args.output,
            repo_root=args.repo_root,
        )
    finally:
        await control.close()


async def _run_remap(args: argparse.Namespace, frozen: Any) -> Mapping[str, Any]:
    import asyncpg

    name = db_name_from_dsn(args.dsn)
    assert_copy_database(name)
    dataset = load_dataset(args.dataset, frozen)
    prepared = _load_report(args.prepared)
    conn = await asyncpg.connect(args.dsn, timeout=10, command_timeout=600)
    try:
        return await run_remap(
            conn=conn,
            dataset=dataset,
            apply=bool(args.apply),
            prepared=prepared,
            prepared_path=args.prepared,
            output_path=args.output,
            repo_root=args.repo_root,
        )
    finally:
        await conn.close()


async def _run_verify(args: argparse.Namespace, frozen: Any) -> Mapping[str, Any]:
    import asyncpg

    assert_source_database(db_name_from_dsn(args.source_dsn))
    assert_copy_database(db_name_from_dsn(args.copy_dsn))
    dataset = load_dataset(args.dataset, frozen)
    prepared = _load_report(args.prepared)
    apply_report = _load_report(args.apply_report)
    source_conn = await asyncpg.connect(args.source_dsn, timeout=10, command_timeout=600)
    copy_conn = await asyncpg.connect(args.copy_dsn, timeout=10, command_timeout=600)
    try:
        return await verify_lineage(
            source_conn=source_conn,
            copy_conn=copy_conn,
            prepared=prepared,
            apply_report=apply_report,
            dataset=dataset,
            dataset_path=args.dataset,
            script_path=Path(__file__),
            prepared_path=args.prepared,
            apply_report_path=args.apply_report,
            output_path=args.output,
            frozen=frozen,
            repo_root=args.repo_root,
        )
    finally:
        await source_conn.close()
        await copy_conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    frozen = load_frozen_ids(args.frozen_ids)
    if args.command == "prepare":
        document = asyncio.run(_run_prepare(args, frozen))
    elif args.command == "remap":
        document = asyncio.run(_run_remap(args, frozen))
    elif args.command == "verify":
        document = asyncio.run(_run_verify(args, frozen))
    else:
        parser.error("Unknown command")
        return 2
    print(json.dumps(document, indent=2, sort_keys=True, default=str))
    return 0


__all__ = [
    "COLUMNS",
    "COPY_DB_NAME",
    "EXCLUDED",
    "LINEAGE_SCHEMA",
    "MAPPING_VERSION",
    "Mapper",
    "PREPARE_SCHEMA",
    "REMAP_SCHEMA",
    "RemapError",
    "SessionWindow",
    "SOURCE_DB_NAME",
    "assert_copy_database",
    "assert_source_database",
    "build_parser",
    "check_memory_windows",
    "classify_columns",
    "column_inventory",
    "load_dataset",
    "main",
    "memory_session_bounds",
    "prepare_copy",
    "read_timestamp_columns",
    "run_remap",
    "verify_lineage",
]


if __name__ == "__main__":
    raise SystemExit(main())
