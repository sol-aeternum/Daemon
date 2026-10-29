"""Candidate namespace-intent codec and mixed v1/v2 structural replay validator.

UNCALLED CANDIDATE — N0 option A, see ``docs/NAMESPACE_JOURNAL_CANDIDATE.md``.
Nothing in the runtime imports this module: it performs no storage I/O, reads no
clock, allocates no random name, loads no settings and adds no dependency. It
adds exactly one v2 command shape, ``namespace_create_intended``, beside the
unchanged canonical v1 frames. It writes no journal, database or filesystem.

Framing keeps the frozen v1 shape — four-byte big-endian payload length,
canonical JSON payload and a 32-byte SHA-256 footer over length prefix plus
payload — with the existing 64 KiB total frame cap. The production decoder keeps
rejecting v2; only this candidate reader understands it.

The v1 closed transition grammar is reused rather than reimplemented: the mixed
validator drives ``model.JournalState``'s transition and commit routines and owns
only the guards that must count both frame versions (journal identity, contiguous
sequence, digest chain, event uniqueness, both-head CAS). ``model.py`` is
byte-frozen for this increment, so its canonical JSON, UUID, timestamp and hex
primitives are imported rather than copied; a later approved refactor may publish
them.

Structural replay is not authority. Shape checks cannot prove namespace entropy,
ownership, current generation or permission, and an accepted birth is evidence of
intent only. An exact duplicate frame is never a second accepted event; the
lookup helper returns the original recorded entry for an identical immutable
command.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn
from uuid import UUID

from .errors import JournalError
from .model import (
    _INT64_MAX,
    _AccountState,
    _canonical_json,
    _iso,
    _parse_canonical_utc,
    _parse_hex_digest,
    _parse_str_uuid,
    _reject_constant,
    _reject_duplicates,
    _reject_float,
    _require_aware,
    Entry,
    JournalState,
    RequestFields,
    TerminalFields,
    command_digest,
    decode_entry,
    encode_entry,
)

__all__ = [
    "MixedNamespaceJournal",
    "NamespaceCommand",
    "NamespaceCreateFields",
    "NamespaceEntry",
    "decode_candidate_entry",
    "decode_candidate_frame",
    "decode_frame",
    "decode_namespace_entry",
    "encode_entry_frame",
    "encode_frame",
    "encode_namespace_entry",
    "encode_namespace_frame",
    "namespace_command_digest",
]

_V1_VERSION = 1
_V2_VERSION = 2
_NAMESPACE_EVENT = "namespace_create_intended"
_NAMESPACE_KIND = "namespace_create"
_NAMESPACE_DOMAIN = "daemon-namespace-command-v2"
_GENERATION_MIN = 0
_LENGTH_PREFIX_SIZE = 4
_DIGEST_SIZE = 32
_MAX_FRAME_SIZE = 64 * 1024

_ENVELOPE_KEYS = frozenset(
    {
        "version",
        "journal_id",
        "seq",
        "event_id",
        "account_prev",
        "operation_prev",
        "previous_digest",
        "command_digest",
        "command",
    }
)
_COMMAND_KEYS = frozenset({"account_id", "operation_id", "event_type", "decision_at", "fields"})
_FIELDS_KEYS = frozenset({"kind", "generation", "namespace"})
_NAMESPACE_HEX = re.compile(r"[0-9a-f]{32}")


# ---------------------------------------------------------------------------
# closed diagnostics and small closed-shape helpers
# ---------------------------------------------------------------------------


def _invalid() -> NoReturn:
    raise JournalError("invalid_entry")


def _conflict() -> NoReturn:
    raise JournalError("conflict")


def _parse_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid()
    return value


def _require_seq(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid()
    if not 0 < value <= _INT64_MAX:
        _invalid()
    return value


def _require_generation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _invalid()
    if not _GENERATION_MIN <= value <= _INT64_MAX:
        _invalid()
    return value


def _parse_optional_seq(value: object) -> int | None:
    if value is None:
        return None
    return _require_seq(value)


def _deny_extra_keys(obj: dict[str, Any], expected: frozenset[str]) -> None:
    if set(obj) != expected:
        _invalid()


def _require_digest(value: object) -> bytes:
    if not isinstance(value, bytes) or len(value) != _DIGEST_SIZE:
        _invalid()
    return value


# ---------------------------------------------------------------------------
# framing: length prefix, canonical payload, SHA-256 footer
# ---------------------------------------------------------------------------


def _frame_digest(payload: bytes) -> bytes:
    prefix = len(payload).to_bytes(_LENGTH_PREFIX_SIZE, "big")
    return hashlib.sha256(prefix + payload).digest()


def encode_frame(payload: bytes) -> bytes:
    """Seal one canonical payload: length prefix, payload, digest footer."""
    if not isinstance(payload, bytes) or not payload:
        _invalid()
    payload_length = len(payload)
    if _LENGTH_PREFIX_SIZE + payload_length + _DIGEST_SIZE > _MAX_FRAME_SIZE:
        _invalid()
    return payload_length.to_bytes(_LENGTH_PREFIX_SIZE, "big") + payload + _frame_digest(payload)


def decode_frame(frame: bytes) -> tuple[bytes, bytes]:
    """Verify one framed record and return ``(payload, footer)``.

    Rejects an empty, oversized, truncated or trailing-byte frame, a length
    prefix that disagrees with the record and any footer that is not
    ``SHA256(prefix + payload)``.
    """
    if not isinstance(frame, bytes) or not frame:
        _invalid()
    if len(frame) > _MAX_FRAME_SIZE:
        _invalid()
    payload_length = int.from_bytes(frame[:_LENGTH_PREFIX_SIZE], "big")
    body = _LENGTH_PREFIX_SIZE + payload_length
    if payload_length == 0 or body + _DIGEST_SIZE != len(frame):
        _invalid()
    payload = frame[_LENGTH_PREFIX_SIZE:body]
    footer = frame[body:]
    if _frame_digest(payload) != footer:
        _invalid()
    return payload, footer


# ---------------------------------------------------------------------------
# canonical parsing, shared with the frozen v1 primitives
# ---------------------------------------------------------------------------


def _decode_utf8(payload: bytes) -> str:
    if not isinstance(payload, bytes):
        _invalid()
    if _LENGTH_PREFIX_SIZE + len(payload) + _DIGEST_SIZE > _MAX_FRAME_SIZE:
        _invalid()  # direct payload decoders enforce the same pre-parse cap
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        _invalid()


def _loads_canonical_json(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (ValueError, UnicodeError, RecursionError):
        # Includes the interpreter's oversized-integer limit and escaped lone
        # surrogates; neither may escape as payload-bearing diagnostics.
        _invalid()


def _parse_canonical_object(payload: bytes) -> dict[str, Any]:
    text = _decode_utf8(payload)
    raw = _loads_canonical_json(text)
    if not isinstance(raw, dict):
        _invalid()
    obj: dict[str, Any] = raw
    try:
        if _canonical_json(obj) != payload:
            _invalid()  # canonical strictness: no whitespace, escapes or reordering
    except (ValueError, UnicodeError, RecursionError):
        _invalid()  # escaped lone surrogates and encoding failures stay closed
    return obj


# ---------------------------------------------------------------------------
# closed v2 types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NamespaceCreateFields:
    """Typed ``fields`` for ``namespace_create_intended``.

    ``kind`` is structurally fixed by this type, so no other action can be
    represented. The namespace is a 32-character lowercase hex identity; this
    layer cannot prove how a producer obtained it.
    """

    generation: int
    namespace: str

    def __post_init__(self) -> None:
        _require_generation(self.generation)
        if not isinstance(self.namespace, str):
            _invalid()
        if _NAMESPACE_HEX.fullmatch(self.namespace) is None:
            _invalid()


@dataclass(frozen=True, slots=True)
class NamespaceCommand:
    """Immutable v2 namespace-intent command; no operation identity exists."""

    account_id: UUID
    event_type: str
    decision_at: datetime
    fields: NamespaceCreateFields

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, UUID):
            _invalid()
        if self.event_type != _NAMESPACE_EVENT:
            _invalid()
        if not isinstance(self.fields, NamespaceCreateFields):
            _invalid()
        object.__setattr__(self, "decision_at", _require_aware(self.decision_at))


@dataclass(frozen=True, slots=True)
class NamespaceEntry:
    """Immutable v2 envelope; ``digest`` is the supplied frame footer."""

    journal_id: UUID
    seq: int
    event_id: UUID
    command: NamespaceCommand
    account_prev: int | None
    previous_digest: bytes
    command_digest: bytes
    digest: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.journal_id, UUID) or not isinstance(self.event_id, UUID):
            _invalid()
        if not isinstance(self.command, NamespaceCommand):
            _invalid()
        _require_seq(self.seq)
        if self.account_prev is not None:
            _require_seq(self.account_prev)
        _require_digest(self.previous_digest)
        _require_digest(self.command_digest)
        _require_digest(self.digest)


# ---------------------------------------------------------------------------
# v2 digests and canonical payloads
# ---------------------------------------------------------------------------


def _namespace_fields_payload(fields: NamespaceCreateFields) -> dict[str, Any]:
    return {
        "kind": _NAMESPACE_KIND,
        "generation": fields.generation,
        "namespace": fields.namespace,
    }


def namespace_command_digest(command: NamespaceCommand) -> bytes:
    """SHA-256 over the v2 domain-separated immutable-argument payload.

    Binds the domain, account, the null operation, the event type and all three
    fields. Excludes decision time, sequence, predecessors and the global digest,
    so an exact retry keeps its identity while a changed generation or namespace
    never does.
    """
    if not isinstance(command, NamespaceCommand):
        _invalid()
    payload = {
        "domain": _NAMESPACE_DOMAIN,
        "account_id": str(command.account_id),
        "operation_id": None,
        "event_type": command.event_type,
        "fields": _namespace_fields_payload(command.fields),
    }
    return hashlib.sha256(_canonical_json(payload)).digest()


def encode_namespace_entry(entry: NamespaceEntry) -> bytes:
    """Canonical v2 payload for the envelope, without the digest footer."""
    if not isinstance(entry, NamespaceEntry):
        _invalid()
    command = entry.command
    payload: dict[str, Any] = {
        "version": _V2_VERSION,
        "journal_id": str(entry.journal_id),
        "seq": entry.seq,
        "event_id": str(entry.event_id),
        "account_prev": entry.account_prev,
        "operation_prev": None,
        "previous_digest": entry.previous_digest.hex(),
        "command_digest": entry.command_digest.hex(),
        "command": {
            "account_id": str(command.account_id),
            "operation_id": None,
            "event_type": command.event_type,
            "decision_at": _iso(command.decision_at),
            "fields": _namespace_fields_payload(command.fields),
        },
    }
    return _canonical_json(payload)


def decode_namespace_entry(payload: bytes, digest: bytes) -> NamespaceEntry:
    """Strict canonical parser for one v2 ``namespace_create_intended`` payload."""
    _require_digest(digest)
    obj = _parse_canonical_object(payload)
    _deny_extra_keys(obj, _ENVELOPE_KEYS)
    if _parse_int(obj["version"]) != _V2_VERSION:
        _invalid()
    journal_id = _parse_str_uuid(obj["journal_id"])
    seq = _require_seq(obj["seq"])
    event_id = _parse_str_uuid(obj["event_id"])
    account_prev = _parse_optional_seq(obj["account_prev"])
    if obj["operation_prev"] is not None:
        _invalid()  # v2 never invents a deletion or reset operation
    previous_digest = _parse_hex_digest(obj["previous_digest"])
    recorded_digest = _parse_hex_digest(obj["command_digest"])
    raw_command = obj["command"]
    if not isinstance(raw_command, dict):
        _invalid()
    command_obj: dict[str, Any] = raw_command
    _deny_extra_keys(command_obj, _COMMAND_KEYS)
    account_id = _parse_str_uuid(command_obj["account_id"])
    if command_obj["operation_id"] is not None:
        _invalid()  # operation identity is exactly null for a birth
    if command_obj["event_type"] != _NAMESPACE_EVENT:
        _invalid()
    decision_at = _parse_canonical_utc(command_obj["decision_at"])
    raw_fields = command_obj["fields"]
    if not isinstance(raw_fields, dict):
        _invalid()
    fields_obj: dict[str, Any] = raw_fields
    _deny_extra_keys(fields_obj, _FIELDS_KEYS)
    if fields_obj["kind"] != _NAMESPACE_KIND:
        _invalid()
    command = NamespaceCommand(
        account_id=account_id,
        event_type=_NAMESPACE_EVENT,
        decision_at=decision_at,
        fields=NamespaceCreateFields(
            generation=_require_generation(fields_obj["generation"]),
            namespace=fields_obj["namespace"],
        ),
    )
    if namespace_command_digest(command) != recorded_digest:
        _invalid()
    return NamespaceEntry(
        journal_id=journal_id,
        seq=seq,
        event_id=event_id,
        command=command,
        account_prev=account_prev,
        previous_digest=previous_digest,
        command_digest=recorded_digest,
        digest=digest,
    )


# ---------------------------------------------------------------------------
# version dispatch
# ---------------------------------------------------------------------------


def decode_candidate_entry(payload: bytes, digest: bytes) -> Entry | NamespaceEntry:
    """Decode one canonical payload of either supported version.

    Version 1 is delegated to the unchanged production decoder, so existing v1
    bytes keep their exact interpretation. Unknown versions fail closed here and
    the production decoder still rejects v2.
    """
    if not isinstance(payload, bytes):
        _invalid()
    obj = _parse_canonical_object(payload)
    if "version" not in obj:
        _invalid()
    version = _parse_int(obj["version"])
    if version == _V1_VERSION:
        return decode_entry(payload, digest)
    if version == _V2_VERSION:
        return decode_namespace_entry(payload, digest)
    _invalid()


def decode_candidate_frame(frame: bytes) -> Entry | NamespaceEntry:
    """Verify framing and footer, then decode the enclosed canonical payload."""
    payload, digest = decode_frame(frame)
    return decode_candidate_entry(payload, digest)


def encode_entry_frame(entry: Entry) -> bytes:
    """Seal an unchanged canonical v1 entry into a framed record."""
    return encode_frame(encode_entry(entry))


def encode_namespace_frame(entry: NamespaceEntry) -> bytes:
    """Seal a canonical v2 birth entry into a framed record."""
    return encode_frame(encode_namespace_entry(entry))


# ---------------------------------------------------------------------------
# mixed structural replay validator
# ---------------------------------------------------------------------------


class MixedNamespaceJournal:
    """Mixed v1/v2 structural replay validator for a single journal chain.

    Owns the guards that span both frame versions and reuses
    ``model.JournalState`` for the closed v1 operation grammar. It never reads a
    clock, persists nothing and applies no effect: acceptance proves structure
    only. The v2 birth advances the account head and never an operation head.
    """

    def __init__(self) -> None:
        # The engine supplies the closed v1 grammar and the authoritative
        # account/operation heads. Its entries list, head digest and event map are
        # incidental here: this validator sequences both frame versions and
        # proves event uniqueness itself.
        self._grammar = JournalState()
        self._journal_id: UUID | None = None
        self._seq = 0
        self._head: bytes = bytes(_DIGEST_SIZE)
        self._events: dict[UUID, Entry | NamespaceEntry] = {}
        self._namespaces: list[NamespaceEntry] = []
        self._by_namespace: dict[str, NamespaceEntry] = {}
        self._by_birth: dict[tuple[UUID, int], NamespaceEntry] = {}
        self._by_command_digest: dict[bytes, NamespaceEntry] = {}
        self._generated_accounts: set[UUID] = set()

    @property
    def journal_id(self) -> UUID | None:
        """Journal identity fixed by the first accepted frame."""
        return self._journal_id

    @property
    def frame_count(self) -> int:
        """Accepted frames across both versions."""
        return self._seq

    @property
    def head_digest(self) -> bytes:
        """Global chain digest: 32 zero bytes before the first accepted frame."""
        return self._head

    @property
    def namespace_births(self) -> tuple[NamespaceEntry, ...]:
        """Accepted namespace births in acceptance order."""
        return tuple(self._namespaces)

    def account_head(self, account_id: UUID) -> int | None:
        """Last accepted sequence for an account, or ``None``."""
        return self._grammar.account_heads.get(account_id)

    def operation_head(self, operation_id: UUID) -> int | None:
        """Last accepted sequence for an operation, or ``None``."""
        return self._grammar.operation_heads.get(operation_id)

    def lookup_namespace(self, namespace: str) -> NamespaceEntry | None:
        """Original birth recorded for a namespace name, if any."""
        return self._by_namespace.get(namespace)

    def lookup_namespace_create(self, command: NamespaceCommand) -> NamespaceEntry | None:
        """Original birth recorded for an identical immutable command.

        Identity excludes decision time, sequence, predecessors and the global
        digest, so a retry with a new decision time resolves to the original
        entry, while a different account, generation or namespace resolves to
        nothing. This is identity evidence only, never authority to act.
        """
        if not isinstance(command, NamespaceCommand):
            _invalid()
        return self._by_command_digest.get(namespace_command_digest(command))

    def accept_frame(self, frame: bytes) -> Entry | NamespaceEntry:
        """Verify one framed record end to end and accept it."""
        entry = decode_candidate_frame(frame)
        if isinstance(entry, NamespaceEntry):
            self.accept_namespace_entry(entry)
        else:
            self.accept_entry(entry)
        return entry

    def accept_entry(self, entry: Entry) -> None:
        """Accept one decoded v1 frame after re-proving its recorded footer."""
        if not isinstance(entry, Entry):
            _invalid()
        self._verify_footer(encode_entry(entry), entry.digest)
        self._guard(entry.journal_id, entry.seq, entry.event_id, entry.previous_digest)
        self._accept_v1(entry)
        self._note(entry)

    def accept_namespace_entry(self, entry: NamespaceEntry) -> None:
        """Accept one decoded v2 birth after re-proving its recorded footer."""
        if not isinstance(entry, NamespaceEntry):
            _invalid()
        self._verify_footer(encode_namespace_entry(entry), entry.digest)
        self._guard(entry.journal_id, entry.seq, entry.event_id, entry.previous_digest)
        if namespace_command_digest(entry.command) != entry.command_digest:
            _invalid()  # recomputed immutable-argument digest must match
        grammar = self._grammar
        command = entry.command
        fields = command.fields
        account_id = command.account_id
        account = grammar._accounts.get(account_id)
        if account is None:
            account = _AccountState()
        if entry.account_prev != grammar.account_heads.get(account_id):
            _conflict()  # account predecessor; a birth has no operation head
        self._validate_birth(entry, account)
        grammar._accounts[account_id] = account
        grammar.account_heads[account_id] = entry.seq
        account.note_generation(fields.generation)
        self._generated_accounts.add(account_id)
        self._namespaces.append(entry)
        self._by_namespace[fields.namespace] = entry
        self._by_birth[(account_id, fields.generation)] = entry
        self._by_command_digest[entry.command_digest] = entry
        self._note(entry)

    def _verify_footer(self, payload: bytes, digest: bytes) -> None:
        _require_digest(digest)
        if _LENGTH_PREFIX_SIZE + len(payload) + _DIGEST_SIZE > _MAX_FRAME_SIZE:
            _invalid()
        if _frame_digest(payload) != digest:
            _invalid()  # a supplied footer is never trusted

    def _guard(self, journal_id: UUID, seq: int, event_id: UUID, previous_digest: bytes) -> None:
        if self._journal_id is not None and journal_id != self._journal_id:
            _invalid()  # one journal identity per chain
        if seq != self._seq + 1:
            _invalid()  # contiguous global sequence across both versions
        if previous_digest != self._head:
            _invalid()  # global digest chain link
        if event_id in self._events:
            _invalid()  # event identity is unique across both versions

    def _accept_v1(self, entry: Entry) -> None:
        if command_digest(entry.command) != entry.command_digest:
            _invalid()  # recomputed caller-argument digest must match
        grammar = self._grammar
        command = entry.command
        account_id = command.account_id
        operation_id = command.operation_id
        account = grammar._accounts.get(account_id)
        if account is None:
            account = _AccountState()
        if account.purged_final:
            _invalid()  # purged accounts never receive further entries
        bound_account = grammar._operation_accounts.get(operation_id)
        if bound_account is not None and bound_account != account_id:
            _conflict()  # an operation stays bound to its original account
        if entry.account_prev != grammar.account_heads.get(account_id):
            _conflict()  # both-head CAS: stale account predecessor
        if entry.operation_prev != grammar.operation_heads.get(operation_id):
            _conflict()  # both-head CAS: stale operation predecessor
        op_state = grammar._operations.get(operation_id)
        grammar._validate_transition(entry, op_state, account)
        grammar._commit(entry, op_state, account, account_id, operation_id)
        grammar._accounts[account_id] = account
        if isinstance(command.fields, (RequestFields, TerminalFields)):
            self._generated_accounts.add(account_id)

    def _validate_birth(self, entry: NamespaceEntry, account: _AccountState) -> None:
        command = entry.command
        fields = command.fields
        account_id = command.account_id
        recorded = self._by_birth.get((account_id, fields.generation))
        if recorded is not None:
            if recorded.command.fields.namespace == fields.namespace:
                _invalid()  # an exact intent is never a second accepted event
            _conflict()  # one birth per account generation
        if fields.namespace in self._by_namespace:
            _conflict()  # a namespace name is never reused
        if account.open_operation is not None:
            _invalid()  # no birth during an open deletion or reset operation
        if account.purged_final:
            _invalid()  # no birth after a final purge
        if account_id in self._generated_accounts and fields.generation != account.max_generation:
            _conflict()  # must reuse the recorded resulting generation verbatim

    def _note(self, entry: Entry | NamespaceEntry) -> None:
        if self._journal_id is None:
            self._journal_id = entry.journal_id
        self._seq = entry.seq
        self._head = entry.digest
        self._events[entry.event_id] = entry
