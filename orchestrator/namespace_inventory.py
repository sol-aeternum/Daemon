"""Closed read-only inventory observations and existing C1 namespace parity.

No journal/file I/O, initialization, repair, transaction/pool ownership, H
installation or execution authority. A verified view must come from the trusted
owner's actual independent journal scan, never an RPC-supplied view/receipt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import re
from typing import Any
from uuid import UUID

from orchestrator.services.account_control.store import Connection
from orchestrator.services.deletion_journal.errors import JournalError
from orchestrator.services.deletion_journal.mixed_store import MixedView
from orchestrator.services.deletion_journal.model import Entry
from orchestrator.services.deletion_journal.namespace_candidate import NamespaceEntry

MAX_INVENTORY_RECORDS = 10000  # Same qualification ceiling as the C1 catalog scan.
_INT64_MAX = 2**63 - 1


def _integer(value: Any, minimum: int) -> None:
    if type(value) is not int or not minimum <= value <= _INT64_MAX:
        raise JournalError("marker_mismatch")


def _uuid(value: Any) -> UUID:
    if type(value) is not str or len(value) != 36:
        raise JournalError("marker_mismatch")
    try:
        result = UUID(value)
    except (ValueError, AttributeError):
        raise JournalError("marker_mismatch") from None
    if str(result) != value:
        raise JournalError("marker_mismatch")
    return result


def _digest(value: Any) -> bytes:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise JournalError("marker_mismatch")
    return bytes.fromhex(value)


def _row_digest(value: Any) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise JournalError("marker_mismatch")
    size = value.nbytes if isinstance(value, memoryview) else len(value)
    if size != 32:
        raise JournalError("marker_mismatch")
    return bytes(value)


@dataclass(frozen=True, slots=True)
class AppliedMarker:
    journal_id: UUID
    seq: int
    event_id: UUID
    account_id: UUID
    operation_id: UUID | None
    entry_digest: bytes

    def __post_init__(self) -> None:
        if any(
            not isinstance(v, UUID) for v in (self.journal_id, self.event_id, self.account_id)
        ) or (self.operation_id is not None and not isinstance(self.operation_id, UUID)):
            raise JournalError("marker_mismatch")
        _integer(self.seq, 1)
        if type(self.entry_digest) is not bytes or len(self.entry_digest) != 32:
            raise JournalError("marker_mismatch")


@dataclass(frozen=True, slots=True)
class NamespaceProjection:
    namespace: str
    account_id: UUID
    generation: int
    journal_id: UUID
    birth_seq: int
    birth_event_id: UUID
    birth_digest: bytes
    command_digest: bytes
    state: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(v, UUID) for v in (self.journal_id, self.birth_event_id, self.account_id)
        ):
            raise JournalError("marker_mismatch")
        _integer(self.generation, 0)
        _integer(self.birth_seq, 1)
        if (
            type(self.namespace) is not str
            or re.fullmatch(r"[0-9a-f]{32}", self.namespace) is None
            or type(self.state) is not str
            or self.state not in ("provisioning", "ready", "uncertain", "retired")
            or any(
                type(v) is not bytes or len(v) != 32
                for v in (self.birth_digest, self.command_digest)
            )
        ):
            raise JournalError("marker_mismatch")


@dataclass(frozen=True, slots=True)
class NamespaceInventory:
    journal_id: UUID
    markers: tuple[AppliedMarker, ...]
    namespaces: tuple[NamespaceProjection, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.journal_id, UUID)
            or type(self.markers) is not tuple
            or type(self.namespaces) is not tuple
            or any(type(v) is not AppliedMarker for v in self.markers)
            or any(type(v) is not NamespaceProjection for v in self.namespaces)
        ):
            raise JournalError("marker_mismatch")


def inventory_from_rows(
    journal_id: UUID,
    markers: Sequence[Mapping[str, Any]],
    namespaces: Sequence[Mapping[str, Any]],
) -> NamespaceInventory:
    """Normalize existing trusted direct-row reads without adding a legacy cap."""
    try:
        return NamespaceInventory(
            journal_id,
            tuple(
                AppliedMarker(
                    r["journal_id"],
                    r["seq"],
                    r["event_id"],
                    r["account_id"],
                    r["operation_id"],
                    _row_digest(r["entry_digest"]),
                )
                for r in markers
            ),
            tuple(
                NamespaceProjection(
                    r["namespace"],
                    r["account_id"],
                    r["generation"],
                    r["journal_id"],
                    r["birth_seq"],
                    r["birth_event_id"],
                    _row_digest(r["birth_digest"]),
                    _row_digest(r["command_digest"]),
                    r["state"],
                )
                for r in namespaces
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise JournalError("marker_mismatch") from None


def namespace_states(
    expected_journal: UUID, view: MixedView, inventory: NamespaceInventory
) -> dict[str, str]:
    """Shared legacy parity, not a transferable namespace/effect permit.

    Preserve known unapplied births as unapplied: only SQL-projected states are
    returned. This never projects one, and a publisher still needs its own ready
    namespace plus current H/native/physical qualification under owned lifetimes.
    """
    if not isinstance(expected_journal, UUID) or inventory.journal_id != expected_journal:
        raise JournalError("marker_mismatch")
    if view.container_version != 2:
        raise JournalError("recovery_required")
    entries = {entry.seq: entry for entry in view.entries}
    if len(entries) != len(view.entries) or any(
        e.journal_id != expected_journal for e in view.entries
    ):
        raise JournalError("marker_mismatch")
    marked: set[int] = set()
    for row in inventory.markers:
        entry = entries.get(row.seq)
        operation = entry.command.operation_id if isinstance(entry, Entry) else None
        if (
            entry is None
            or row.seq in marked
            or row.journal_id != entry.journal_id
            or row.event_id != entry.event_id
            or row.account_id != entry.command.account_id
            or row.operation_id != operation
            or row.entry_digest != entry.digest
        ):
            raise JournalError("marker_mismatch")
        marked.add(row.seq)
    if any(not isinstance(entry, NamespaceEntry) for entry in view.entries):
        raise JournalError("recovery_required")
    represented: set[int] = set()
    states: dict[str, str] = {}
    for row in inventory.namespaces:
        birth = entries.get(row.birth_seq)
        if (
            not isinstance(birth, NamespaceEntry)
            or row.birth_seq in represented
            or row.namespace in states
            or row.journal_id != birth.journal_id
            or row.birth_event_id != birth.event_id
            or row.account_id != birth.command.account_id
            or row.generation != birth.command.fields.generation
            or row.namespace != birth.command.fields.namespace
            or row.birth_digest != birth.digest
            or row.command_digest != birth.command_digest
            or birth.seq not in marked
        ):
            raise JournalError("marker_mismatch")
        represented.add(birth.seq)
        states[row.namespace] = row.state
    if represented != marked:
        raise JournalError("marker_mismatch")
    return states


def _closed_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise JournalError("marker_mismatch")
        result[key] = value
    return result


def decode_inventory(payload: str, expected_journal: UUID, record_limit: int) -> NamespaceInventory:
    if (
        not isinstance(expected_journal, UUID)
        or type(record_limit) is not int
        or not 1 <= record_limit <= MAX_INVENTORY_RECORDS
    ):
        raise JournalError("invalid_config")
    if (
        type(payload) is not str
        or len(payload) > record_limit * 2048 + 512
        or not payload.isascii()
    ):
        raise JournalError("marker_mismatch")
    try:
        value = json.loads(payload, object_pairs_hook=_closed_pairs)
        if type(value) is not dict or set(value) != {
            "schema_version",
            "journal_id",
            "markers",
            "namespaces",
        }:
            raise JournalError("marker_mismatch")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 1
            or _uuid(value["journal_id"]) != expected_journal
        ):
            raise JournalError("marker_mismatch")
        if any(
            type(value[k]) is not list or len(value[k]) > record_limit
            for k in ("markers", "namespaces")
        ):
            raise JournalError("marker_mismatch")
        markers, namespaces = [], []
        for row in value["markers"]:
            if type(row) is not dict or set(row) != {
                "journal_id",
                "seq",
                "event_id",
                "account_id",
                "operation_id",
                "entry_digest",
            }:
                raise JournalError("marker_mismatch")
            markers.append(
                AppliedMarker(
                    _uuid(row["journal_id"]),
                    row["seq"],
                    _uuid(row["event_id"]),
                    _uuid(row["account_id"]),
                    None if row["operation_id"] is None else _uuid(row["operation_id"]),
                    _digest(row["entry_digest"]),
                )
            )
        for row in value["namespaces"]:
            if type(row) is not dict or set(row) != {
                "namespace",
                "account_id",
                "generation",
                "journal_id",
                "birth_seq",
                "birth_event_id",
                "birth_digest",
                "command_digest",
                "state",
            }:
                raise JournalError("marker_mismatch")
            namespaces.append(
                NamespaceProjection(
                    row["namespace"],
                    _uuid(row["account_id"]),
                    row["generation"],
                    _uuid(row["journal_id"]),
                    row["birth_seq"],
                    _uuid(row["birth_event_id"]),
                    _digest(row["birth_digest"]),
                    _digest(row["command_digest"]),
                    row["state"],
                )
            )
        return NamespaceInventory(expected_journal, tuple(markers), tuple(namespaces))
    except (ValueError, TypeError, KeyError, RecursionError):
        raise JournalError("marker_mismatch") from None


async def read_output_inventory(
    conn: Connection, expected_journal: UUID, *, record_limit: int
) -> NamespaceInventory:
    """Uncalled read adapter: no pool, retries, transaction control or file I/O."""
    if (
        not isinstance(expected_journal, UUID)
        or type(record_limit) is not int
        or not 1 <= record_limit <= MAX_INVENTORY_RECORDS
    ):
        raise JournalError("invalid_config")
    payload = await conn.fetchval(
        "SELECT public.resource_output_namespace_inventory($1::pg_catalog.uuid,$2::pg_catalog.int4)",
        expected_journal,
        record_limit,
    )
    if not isinstance(payload, str):
        raise JournalError("marker_mismatch")
    return decode_inventory(payload, expected_journal, record_limit)
