"""Pure supplied-facts comparison. No DB, repair, readiness or permits."""

from dataclasses import dataclass
from uuid import UUID

from .model import MAX_PAYLOAD, MAX_RECORDS, Entry, HistoryError, HistoryState, validate_chain


@dataclass(frozen=True, slots=True)
class ProjectionFact:
    history_id: UUID
    seq: int
    record_id: UUID
    digest: bytes
    frame: bytes


@dataclass(frozen=True, slots=True)
class ProjectionReport:
    missing: tuple[int, ...]
    extra: tuple[int, ...]
    mismatched: tuple[int, ...]
    restore_required: bool


def compare_projections(
    history_id: UUID, state: HistoryState, facts: tuple[ProjectionFact, ...]
) -> ProjectionReport:
    """Even an empty report cannot certify auth/budget/restore continuity."""
    from .model import encode_entry

    if len(facts) > MAX_RECORDS:
        raise HistoryError("invalid_projection")
    checked = validate_chain(history_id, state.entries)
    expected: dict[int, Entry] = {e.seq: e for e in checked.entries}
    seen = set()
    missing = []
    extra = []
    mismatched = []
    for fact in facts:
        if (
            type(fact.seq) is not int
            or not 1 <= fact.seq <= 2**63 - 1
            or fact.seq in seen
            or not isinstance(fact.history_id, UUID)
            or not fact.history_id.int
            or not isinstance(fact.record_id, UUID)
            or not fact.record_id.int
            or type(fact.digest) is not bytes
            or len(fact.digest) != 32
            or type(fact.frame) is not bytes
            or not 37 <= len(fact.frame) <= MAX_PAYLOAD + 36
        ):
            raise HistoryError("invalid_projection")
        seen.add(fact.seq)
        entry = expected.get(fact.seq)
        if entry is None:
            extra.append(fact.seq)
            continue
        frame = encode_entry(entry.history_id, entry.seq, entry.prev_digest, entry.command)
        if (fact.history_id, fact.record_id, fact.digest, fact.frame) != (
            entry.history_id,
            entry.command.record_id,
            entry.digest,
            frame,
        ):
            mismatched.append(fact.seq)
    missing.extend(set(expected) - seen)
    return ProjectionReport(
        tuple(sorted(missing)),
        tuple(sorted(extra)),
        tuple(sorted(mismatched)),
        checked.open_restore is not None,
    )
