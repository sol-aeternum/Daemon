"""Offline pure core primitives for the approved web-fetch fallback pilot.

This implements ONLY the offline, dependency-free core of the concrete IPC
gateway design (docs/WEB_FETCH_FALLBACK_PILOT_DESIGN.md, "Concrete IPC gateway
design (2026-10-01)"):

- the fixed 12-byte frame codec with bounded incremental parsing and a
  fail-latched parser state, protocol subset OPEN/OPEN_OK/OPEN_ERROR/DATA/
  WINDOW/HALF_CLOSE/CLOSE/RESULT,
- a strict, complete HTTP/1.1 CONNECT header parser for ``hostname:443``
  that preserves exact post-header bytes,
- a public-IPv4-only destination policy over injected DNS answers against an
  explicitly supplied deployment-owned address inventory, and
- a synchronous shared byte-budget reservation accounting object.

What this module deliberately does NOT do (and no claim of these is made):
no lifecycle multiplexer, no tunnel state machine, no sockets, DNS resolution,
pinned dialing, subprocesses, pipes, deadlines, wallclock or container work.
OPEN allowlist membership, WINDOW credit semantics and tunnel transitions are
validated independently of the codec; the codec enforces only framing and
payload/stream-ID shape. The pinned-dial design (validate every DNS answer
fresh per admission, then dial only a previously approved numeric IPv4
address) is specified by the design document but not implemented here.
API names are internal pilot names, not application or library contracts.
"""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator, Sequence

# ---------------------------------------------------------------------------
# Frame codec
# ---------------------------------------------------------------------------

HEADER_LEN = 12
HEADER_STRUCT = struct.Struct("!BBHII")
MAX_PAYLOAD = 16 * 1024
MAX_STREAM_ID = 40
CONTROL_STREAM_ID = 0
MAX_FRAMES = 4096
CHUNK_LIMIT = 64 * 1024  # per-feed input cap (future pipe-read contract)
OPEN_PAYLOAD_MAX = 255
ERROR_PAYLOAD_MAX = 255
CONNECT_HEADER_LIMIT = 8192


class FrameType(IntEnum):
    """Protocol subset of the pilot IPC framing."""

    OPEN = 1
    OPEN_OK = 2
    OPEN_ERROR = 3
    DATA = 4
    WINDOW = 5
    HALF_CLOSE = 6
    CLOSE = 7
    RESULT = 8


class FrameCodecError(Exception):
    """Raised for any framing or payload-shape violation."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Frame:
    """One decoded frame. Immutability prevents post-validation mutation."""

    frame_type: FrameType
    stream_id: int
    payload: bytes


# (min, max) payload length per frame type
_TYPE_PAYLOAD_SPECS: dict[FrameType, tuple[int, int]] = {
    FrameType.OPEN: (1, OPEN_PAYLOAD_MAX),
    FrameType.OPEN_OK: (0, 0),
    FrameType.OPEN_ERROR: (1, ERROR_PAYLOAD_MAX),
    FrameType.DATA: (0, MAX_PAYLOAD),
    FrameType.WINDOW: (4, 4),
    FrameType.HALF_CLOSE: (0, 0),
    FrameType.CLOSE: (0, 0),
    FrameType.RESULT: (1, MAX_PAYLOAD),
}

_HOST_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-")


def require_canonical_hostname(host: str) -> str:
    """Return ``host`` after strict canonical ASCII DNS-hostname validation.

    Accepts lowercase LDH labels only: no non-ASCII/Unicode/IDNA input, no
    whitespace or control characters, no percent/userinfo/path/bracket forms,
    no literal IP addresses (all-digit hosts), no empty/oversized labels, no
    leading/trailing dashes or dots and no uppercase. The caller separately
    checks fixed-allowlist membership; this function knows none.
    """

    if not isinstance(host, str) or host == "":
        raise ValueError("hostname must be a non-empty ASCII string")
    if len(host) > 253:
        raise ValueError("hostname exceeds 253 ASCII bytes")
    if " " in host or "\t" in host:
        raise ValueError("hostname contains whitespace")
    if any(c not in _HOST_CHARS for c in host):
        raise ValueError("hostname contains non-canonical characters")
    if all(c in "0123456789." for c in host):
        raise ValueError("literal IP addresses are not hostnames")
    if host.startswith(".") or host.endswith(".") or ".." in host:
        raise ValueError("empty labels / trailing dot rejected")
    labels = host.split(".")
    if len(labels) < 2:
        raise ValueError("hostname must contain at least two labels")
    for label in labels:
        if not 1 <= len(label) <= 63:
            raise ValueError("label length outside 1..63")
        if label.startswith("-") or label.endswith("-"):
            raise ValueError("label leading/trailing dash rejected")
    return host


def check_open_payload(payload: bytes) -> str:
    """Validate an OPEN payload as canonical ``hostname:443`` ASCII."""

    if len(payload) < 1 or len(payload) > OPEN_PAYLOAD_MAX:
        raise FrameCodecError("OPEN payload length invalid")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FrameCodecError("OPEN payload is not ASCII") from exc
    suffix = ":443"
    if not text.endswith(suffix) or len(text) <= len(suffix):
        raise FrameCodecError("OPEN payload must be hostname:443")
    try:
        require_canonical_hostname(text[: -len(suffix)])
    except ValueError as exc:
        raise FrameCodecError(f"OPEN payload invalid: {exc}") from exc
    return text


class FrameParser:
    """Bounded incremental frame parser (one direction of one pipe).

    Contract notes for the implementer/reviewer:

    - ``feed`` fully parses its chunk synchronously before returning a
      snapshot iterator over the bounded frame results, so re-entrancy
      (feeding again while an earlier iterator is undrained) cannot corrupt
      the parser state it iterates over. Drain an iterator before the next
      ``feed`` when frame order matters.
    - The parser never allocates a buffer sized by untrusted input: feed
      chunks above ``CHUNK_LIMIT`` (64 KiB, the future pipe-read cap) are
      rejected before any memoryview is taken; a declared payload length
      above ``MAX_PAYLOAD`` is rejected before any payload bytes are read;
      and the internal staging buffer is bounded by ``HEADER_LEN +
      MAX_PAYLOAD`` bytes. Parsing walks a memoryview with an offset; only
      each frame slice (<= 16 KiB) or the bounded trailing partial frame is
      copied — never the whole chunk and never a repeated tail.
    - Any malformed frame latches a permanent failure: subsequent ``feed``
      and ``end_of_stream`` calls re-raise. There is no recovery or parser
      reuse after an error.
    - ``end_of_stream`` rejects truncated trailing bytes (EOF mid-frame).
    - The design's per-direction 4096-frame ceiling is enforced as a shared
      total in this parser: accepted frames beyond it latch failure. A chunk
      is accepted whole or the parser fails; results are never emitted from a
      half-failed chunk.
    """

    __slots__ = ("_buf", "_ended", "_failed", "_frames_count", "_decoded_bytes")

    def __init__(self) -> None:
        self._buf: bytearray = bytearray()
        self._ended = False
        self._failed: str | None = None
        self._frames_count = 0
        self._decoded_bytes = 0

    def _fail(self, reason: str) -> FrameCodecError:
        self._failed = reason
        return FrameCodecError(reason)

    def _check_usable(self) -> None:
        if self._failed is not None:
            raise FrameCodecError(self._failed)
        if self._ended:
            raise FrameCodecError("parser ended; no feed reuse after clean EOF")

    def feed(self, data: bytes) -> Iterator[Frame]:
        """Parse one bounded chunk and return an eager bounded frame iterator.

        Pipe-read contract (future consumer): read at most ``CHUNK_LIMIT``
        (64 KiB) per call, and drain the returned iterator before reading
        further. Oversized chunks latch the parser failed before any parsing
        or copying; per-feed decoded payload is also bounded by ``CHUNK_LIMIT``
        and per-frame by ``MAX_PAYLOAD``. This parser does not implement
        queue/credit semantics; bounded eager results are the only contract.
        """

        self._check_usable()
        if len(data) > CHUNK_LIMIT:
            raise self._fail(f"feed chunk {len(data)} exceeds {CHUNK_LIMIT} limit")
        self._decoded_bytes = 0
        out: list[Frame] = []
        src = memoryview(data)
        try:
            pos = self._resume_staged(src, out)
            self._parse_stream(src, pos, out)
        except FrameCodecError as exc:
            self._frames_count += len(out)
            raise self._fail(exc.reason) from exc
        self._frames_count += len(out)
        return iter(out)

    def end_of_stream(self) -> None:
        """Terminate cleanly at EOF; reject truncated trailing bytes.

        Raises if anything is staged (EOF mid-frame/header) or if the parser
        already failed or already ended. This is terminal: ``feed`` is
        permanently rejected afterwards.
        """

        self._check_usable()
        if self._buf:
            raise self._fail(f"truncated stream: {len(self._buf)} bytes incomplete")
        self._ended = True

    # -- internals ---------------------------------------------------------

    def _build_frame(self, raw: bytes) -> Frame:
        frame_type, flags, reserved, stream_id, payload_len = HEADER_STRUCT.unpack(raw[:HEADER_LEN])
        if flags != 0:
            raise FrameCodecError("nonzero flags byte")
        if reserved != 0:
            raise FrameCodecError("nonzero reserved field")
        if payload_len > MAX_PAYLOAD:
            raise FrameCodecError(f"declared payload {payload_len} exceeds {MAX_PAYLOAD}")
        try:
            ftype = FrameType(frame_type)
        except ValueError as exc:
            raise FrameCodecError(f"unknown frame type {frame_type}") from exc
        payload = raw[HEADER_LEN:]
        lo, hi = _TYPE_PAYLOAD_SPECS[ftype]
        if not lo <= len(payload) <= hi:
            raise FrameCodecError(f"payload length {len(payload)} invalid for {ftype!r}")
        if ftype == FrameType.RESULT:
            if stream_id != CONTROL_STREAM_ID:
                raise FrameCodecError("RESULT must be on control stream 0")
        elif not 1 <= stream_id <= MAX_STREAM_ID:
            raise FrameCodecError("stream id outside 1..40")
        if ftype == FrameType.OPEN:
            check_open_payload(payload)
        if ftype == FrameType.OPEN_ERROR and len(payload) > 0 and not payload.isascii():
            raise FrameCodecError("OPEN_ERROR payload is not ASCII")
        return Frame(ftype, stream_id, payload)

    def _resume_staged(self, src: memoryview, out: list[Frame]) -> int:
        """Finish any previously staged partial frame; return consumed offset.

        The staging buffer is bounded by ``HEADER_LEN + MAX_PAYLOAD``; only
        the needed prefix of ``src`` is copied into it.
        """

        pos = 0
        while self._buf:
            if len(self._buf) < HEADER_LEN:
                grab = min(HEADER_LEN - len(self._buf), len(src) - pos)
                if grab == 0:
                    return pos
                self._buf += bytes(src[pos : pos + grab])
                pos += grab
                continue
            payload_len = struct.unpack_from("!I", self._buf, 8)[0]
            if payload_len > MAX_PAYLOAD:
                # Cannot happen for freshly staged bytes (checked below), but
                # stays as a defence for hand-corrupted internal state.
                raise FrameCodecError(f"declared payload {payload_len} exceeds {MAX_PAYLOAD}")
            need = HEADER_LEN + payload_len
            if len(self._buf) < need:
                grab = min(need - len(self._buf), len(src) - pos)
                if grab:
                    self._buf += bytes(src[pos : pos + grab])
                    pos += grab
                if len(self._buf) < need:
                    return pos
            self._append_row(out, bytes(self._buf))
            self._buf = bytearray()
        return pos

    def _parse_stream(self, src: memoryview, pos: int, out: list[Frame]) -> None:
        """Parse complete frames directly from ``src`` from ``pos`` onward.

        No tail slicing: ``pos`` advances by ``need`` and only each bounded
        frame slice (<= 16 KiB) or the bounded trailing partial frame is ever
        copied.
        """

        n = len(src)
        while True:
            remaining = n - pos
            if remaining < HEADER_LEN:
                if remaining:
                    self._buf = bytearray(src[pos:])
                return
            payload_len = struct.unpack_from("!I", src, pos + 8)[0]
            if payload_len > MAX_PAYLOAD:
                raise FrameCodecError(f"declared payload {payload_len} exceeds {MAX_PAYLOAD}")
            need = HEADER_LEN + payload_len
            if remaining < need:
                self._buf = bytearray(src[pos:])
                return
            self._append_row(out, bytes(src[pos : pos + need]))
            pos += need

    def _append_row(self, out: list[Frame], raw: bytes) -> None:
        if len(out) + self._frames_count >= MAX_FRAMES:
            raise FrameCodecError("frame count ceiling exceeded")
        # Include completed payload bytes staged by a previous feed, not just
        # bytes in this call's input. Reject the whole batch before emitting it.
        payload_bytes = len(raw) - HEADER_LEN
        if self._decoded_bytes + payload_bytes > CHUNK_LIMIT:
            raise FrameCodecError("decoded payload ceiling exceeded")
        out.append(self._build_frame(raw))
        self._decoded_bytes += payload_bytes


def encode_frame(frame_type: FrameType, stream_id: int, payload: bytes = b"") -> bytes:
    """Encode a frame (test/tooling helper; supervisor-side use only)."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    if frame_type == FrameType.RESULT:
        if stream_id != CONTROL_STREAM_ID:
            raise ValueError("RESULT stream must be 0")
    elif not 1 <= stream_id <= MAX_STREAM_ID:
        raise ValueError("stream id outside 1..40")
    return HEADER_STRUCT.pack(int(frame_type), 0, 0, stream_id, len(payload)) + payload


# ---------------------------------------------------------------------------
# CONNECT header parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectRequest:
    """A validated CONNECT preface plus exact preserved post-header bytes."""

    host: str
    port: int
    leftover: bytes  # bytes after the header terminator (tunnel data; never discard)


_HEADER_NAME_CHARS = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
_FORBIDDEN_HEADER_NAMES = (
    "proxy-authorization",
    "proxy-authenticate",
    "authorization",
    "content-length",
    "transfer-encoding",
    "upgrade",
    "expect",
    "te",
    "trailer",
)
_MAX_HEADER_LINES = 64


class ConnectHeaderError(Exception):
    """Raised when a CONNECT preface violates the pilot CONNECT rules."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _reject(reason: str) -> ConnectHeaderError:
    return ConnectHeaderError(reason)


def _parse_authority(target: bytes) -> str:
    """Parse a strict ``hostname:443`` authority and return the hostname."""

    try:
        text = target.decode("ascii")
    except UnicodeDecodeError as exc:
        raise _reject("authority is not ASCII") from exc
    host, sep, port = text.rpartition(":")
    if not sep or not host:
        raise _reject("authority must be hostname:443")
    if ":" in host:
        raise _reject("authority has multiple colons")
    if port != "443":
        raise _reject("CONNECT port must be 443")
    for ch in "@%\\/?#[] ":
        if ch in host:
            raise _reject("authority contains userinfo/encoding/bracket forms")
    try:
        return require_canonical_hostname(host)
    except ValueError as exc:
        raise _reject(str(exc)) from exc


def parse_connect_headers(data: bytes) -> ConnectRequest:
    """Parse a complete, already-buffered HTTP/1.1 CONNECT preface.

    Input contract (caller responsibility, re-verified here): the caller
    buffers at most 8 KiB before calling; the 2-second header deadline and any
    listener/relay are out of scope for this pure function. Returns the
    canonical host, fixed port 443 and the exact leftover bytes after the
    ``\\r\\n\\r\\n`` terminator, which must later be forwarded as tunnel
    data only after admission (never silently discarded). Rejects non-CONNECT
    methods/targets/versions, non-canonical or reserved-character authorities,
    malformed framing, forbidden framing headers and duplicates/missing Host.
    """

    if len(data) > CONNECT_HEADER_LIMIT:
        raise _reject("CONNECT preface exceeds 8 KiB")
    term = data.find(b"\r\n\r\n")
    if term < 0:
        raise _reject("complete header terminator not found in buffered input")
    try:
        text = data[:term].decode("ascii")  # exclude the final CRLFCRLF terminator
    except UnicodeDecodeError as exc:
        raise _reject("CONNECT preface is not ASCII") from exc
    if "\x00" in text:
        raise _reject("NUL byte in CONNECT preface")
    if text.count("\n") != text.count("\r\n"):
        raise _reject("bare LF in CONNECT preface")
    if text.count("\r") != text.count("\r\n"):
        raise _reject("bare CR in CONNECT preface")
    if "\x7f" in text:
        raise _reject("DEL character in CONNECT preface")

    lines = text.split("\r\n")
    if lines and lines[-1] == "":
        lines.pop()  # the trailing CRLF before the terminator is not a line
    if not lines or lines[0] == "":
        raise _reject("CONNECT preface is empty")
    request_line = lines[0]
    if not request_line.startswith("CONNECT "):
        raise _reject("method is not CONNECT")
    rest = request_line[len("CONNECT ") :]
    suffix = " HTTP/1.1"
    if not rest.endswith(suffix):
        raise _reject("protocol is not HTTP/1.1")
    target = rest[: -len(suffix)]
    if " " in target or "\t" in target:
        raise _reject("whitespace in CONNECT target")
    host = _parse_authority(target.encode("ascii", "strict"))

    header_lines = lines[1:]
    if len(header_lines) > _MAX_HEADER_LINES:
        raise _reject("too many header lines")
    host_seen = False
    seen_names: set[str] = set()
    for line in header_lines:
        if line == "":
            raise _reject("empty header line")
        if line[0] in " \t":
            raise _reject("obs-fold continuation line rejected")
        name, sep, value = line.partition(":")
        if not sep or not name:
            raise _reject("header line without colon")
        if name != name.strip(" \t"):
            raise _reject("whitespace before header colon")
        if any(c not in _HEADER_NAME_CHARS for c in name):
            raise _reject("invalid characters in header name")
        # Field values: no bare control characters other than HTAB, incl. DEL.
        for c in value:
            if c != "\t" and (ord(c) < 0x20 or c == "\x7f"):
                raise _reject(f"control character in header value {name!r}")
        low = name.lower()
        if low in _FORBIDDEN_HEADER_NAMES:
            raise _reject(f"forbidden header {low!r}")
        if low in seen_names:
            raise _reject(f"duplicate header {low!r}")
        seen_names.add(low)
        if low == "host":
            # Trim SP/HTAB OWS only; broader stripping is not applied.
            host_value = value.strip(" \t")
            if " " in host_value or "\t" in host_value:
                raise _reject("whitespace inside Host header value")
            parsed = _parse_authority(host_value.encode("ascii", "strict"))
            if parsed != host:
                raise _reject("Host header does not match CONNECT authority")
            host_seen = True
    if not host_seen:
        raise _reject("missing Host header")
    return ConnectRequest(host=host, port=443, leftover=data[term + 4 :])


# ---------------------------------------------------------------------------
# Destination policy (injected DNS answers, IPv4-only)
# ---------------------------------------------------------------------------


class DestinationPolicyError(Exception):
    """Raised when any DNS answer or inventory input is not acceptable."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


MAX_DNS_ANSWERS = 8


@dataclass(frozen=True)
class _OwnedInventory:
    """Explicit deployment-owned IPv4 addresses and networks (deny list)."""

    addresses: frozenset[ipaddress.IPv4Address]
    networks: tuple[ipaddress.IPv4Network, ...]


def parse_owned_inventory(inventory: Sequence[str] | None) -> _OwnedInventory:
    """Parse the explicitly supplied deployment-owned address inventory.

    ``None`` is prohibited by the design (missing/incomplete inventory fails
    closed); an explicitly empty list is permissible for unit tests but makes
    no live-readiness claim. IPv4 entries are single addresses or ``addr/prefix``
    networks; IPv6 entries are validated as well-formed but never match,
    because IPv6 dialing is denied outright rather than filtered by inventory.
    """

    if inventory is None:
        raise DestinationPolicyError("owned inventory must be explicitly supplied")
    addrs: set[ipaddress.IPv4Address] = set()
    nets: list[ipaddress.IPv4Network] = []
    for item in inventory:
        if not isinstance(item, str) or not item:
            raise DestinationPolicyError("inventory entries must be non-empty strings")
        if "/" in item:
            try:
                net = ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                raise DestinationPolicyError(f"invalid inventory network {item!r}") from exc
            if isinstance(net, ipaddress.IPv4Network):
                nets.append(net)
            continue
        try:
            addr = ipaddress.ip_address(item)
        except ValueError as exc:
            raise DestinationPolicyError(f"invalid inventory address {item!r}") from exc
        if isinstance(addr, ipaddress.IPv4Address):
            addrs.add(addr)
    return _OwnedInventory(frozenset(addrs), tuple(nets))


# Special IPv4 answer classes that strict numeric parsing may not cover alone.
# 100.64.0.0/10 (CGNAT/shared address space) already fails the is_global check
# (is_private is False for it in CPython); 192.88.99.0/24 (deprecated 6to4
# relay) is denied explicitly as a translation/relay range.
_EXPLICIT_DENY_V4 = (ipaddress.ip_network("192.88.99.0/24", strict=False),)


def _validate_single_answer(text: str) -> ipaddress.IPv4Address:
    """Strictly parse one numeric IPv4 answer; every other form is rejected."""

    if not isinstance(text, str) or not text:
        raise DestinationPolicyError("DNS answer entries must be non-empty strings")
    try:
        addr = ipaddress.ip_address(text)
    except ValueError as exc:
        raise DestinationPolicyError(f"invalid DNS answer {text!r}") from exc
    if not isinstance(addr, ipaddress.IPv4Address):
        # Includes IPv4-mapped/translated/NAT64 forms: all dialing must be
        # IPv4-only; even answers numerically mapping to public IPv4 are
        # rejected at the policy boundary.
        raise DestinationPolicyError("IPv6 answer rejected: gateway egress is IPv4-only")
    addr4 = addr
    checks = (
        ("unspecified", addr4.is_unspecified),  # 0.0.0.0
        ("loopback", addr4.is_loopback),
        ("link-local", addr4.is_link_local),
        ("multicast", addr4.is_multicast),
        ("reserved", addr4.is_reserved),
        ("private/non-global", addr4.is_private),
        ("metadata/non-global", not addr4.is_global),
    )
    for name, bad in checks:
        if bad:
            raise DestinationPolicyError(f"dialable-range check failed: {name} ({text})")
    for net in _EXPLICIT_DENY_V4:
        if addr4 in net:
            raise DestinationPolicyError(f"translation/tunnelling range rejected: {text}")
    return addr4


def validate_dns_answers(
    answers: Sequence[str],
    owned_inventory: _OwnedInventory,
) -> list[str]:
    """Validate every injected DNS answer for one admission.

    All answers must be strictly numeric, IPv4-only, globally dialable and not
    owned/contained by the supplied inventory; any single bad answer rejects
    the whole admission (mixed public/private answers never partially pass).
    More than ``MAX_DNS_ANSWERS`` answers fails closed. Returns the approved
    canonical numeric IPv4 strings for the caller to later pin a dial to,
    duplicates collapsed preserving order. No DNS query and no socket activity
    happens here; DNS-rebinding protection comes from re-validating each
    admission's answers before any dial uses them — the pinned dial itself is
    still unimplemented (see module docstring).
    """

    if not answers:
        raise DestinationPolicyError("admission requires at least one DNS answer")
    if len(answers) > MAX_DNS_ANSWERS:
        raise DestinationPolicyError(f"more than {MAX_DNS_ANSWERS} DNS answers")
    seen: list[ipaddress.IPv4Address] = []
    out: list[str] = []
    for text in answers:
        addr = _validate_single_answer(text)
        if addr in owned_inventory.addresses or any(
            addr in net for net in owned_inventory.networks
        ):
            raise DestinationPolicyError(f"deployment-owned address rejected: {text}")
        if addr not in seen:
            seen.append(addr)
            out.append(str(addr))
    return out


# ---------------------------------------------------------------------------
# Byte budget
# ---------------------------------------------------------------------------


class BudgetError(Exception):
    """Raised for budget exhaustion, closed-budget use or invalid accounting."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


TOTAL_NETWORK_LIMIT = 32 * 1024 * 1024  # 32 MiB aggregate per run

DIRECTION_READ = "read"
DIRECTION_WRITE = "write"
_DIRECTIONS = (DIRECTION_READ, DIRECTION_WRITE)


@dataclass(frozen=True, slots=True)
class _ReservationState:
    """Internal budget-authoritative reservation record.

    Callable callers never construct or read this; the owning ``ByteBudget``
    registry is the only accessor. It is immutable after construction (the
    ``spent`` flag lives in the registry bookkeeping, replaced by registry
    removal), so a token cannot carry or mutate its amount/direction.
    """

    amount: int
    direction: str


class Reservation:
    """Opaque zero-data token for one reserved byte amount.

    Identity only: no attributes, no state, and instances carry nothing the
    budget consults. Only tokens issued by ``ByteBudget.reserve`` (and
    registered internally by that budget) reconcile; any other instance,
    subclass or caller-constructed token fails closed. Note: opaque tokens
    and private-attribute avoidance are design hygiene, not a true security
    boundary — Python allows private introspection. The budget never reads
    any field from the token, so token mutation or subclassing cannot forge
    reservation state.
    """

    __slots__ = ()


class ByteBudget:
    """Shared synchronous transfer-byte accounting (no awaits, no locks).

    Invariant maintained at every mutation:

        charged_actual + outstanding <= limit        (default limit: 32 MiB)

    ``reserve`` immediately counts the planned amount against the limit ;
    ``reconcile`` charges the actually consumed bytes (<= reserved, exactly
    once) and frees the difference. Cancelled operations are reconciled with
    zero bytes; a planned-but-unused amount frees without being charged.
    Exhaustion (reserving beyond what remains), invalid accounting, unissued
    or cross-budget tokens, double reconciliation and over-commit each latch
    the budget closed: every later operation then raises ``BudgetError``.
    When charged bytes reach the limit the budget latches closed
    automatically (a limit failure is terminal, never a new accounting
    epoch). Overlapping sequential reservations can therefore never jointly
    overspend the limit. Directional counters are kept separately for
    read/write reconciliation. Tokens are tracked in a registry keyed by
    token identity; consumed tokens are removed so the registry keeps only
    active reservations. If the budget closes while reservations are still
    outstanding, those reserved bytes are never refunded or accounted: any
    future run must conservatively treat them as unresolved, and closure is
    never presented as successful completion. Amounts, actuals and limits
    must be exact positive/zero integers (``bool`` excluded, no floats).
    """

    __slots__ = (
        "_limit",
        "_actual",
        "_outstanding",
        "_actual_read",
        "_actual_write",
        "_closed",
        "_closed_reason",
        "_active",
    )

    def __init__(self, limit: int = TOTAL_NETWORK_LIMIT) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self._limit = limit
        self._actual = 0
        self._outstanding = 0
        self._actual_read = 0
        self._actual_write = 0
        self._closed = False
        self._closed_reason: str | None = None
        self._active: dict[Reservation, _ReservationState] = {}

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def charged_actual(self) -> int:
        return self._actual

    @property
    def outstanding(self) -> int:
        return self._outstanding

    @property
    def remaining(self) -> int:
        """Bytes still reservable under the invariant."""

        return self._limit - self._actual - self._outstanding

    @property
    def charged_read(self) -> int:
        return self._actual_read

    @property
    def charged_write(self) -> int:
        return self._actual_write

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def closed_reason(self) -> str | None:
        return self._closed_reason

    def reserve(self, amount: int, direction: str) -> Reservation:
        """Reserve planned bytes before an I/O operation (synchronous).

        Reserving beyond what remains is the design's exhaustion case and
        latches the budget closed for the rest of the run. Amounts must be
        positive integers. Only the capped socket operation itself may await
        afterwards; reservation and reconciliation are synchronous critical
        sections with exactly-once tokens.
        """

        self._check_open()
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            self._fail_internal("invalid reservation amount")
            raise BudgetError("invalid reservation amount: must be a positive integer")
        if direction not in _DIRECTIONS:
            self._fail_internal("invalid reservation direction")
            raise BudgetError("invalid reservation direction")
        if amount > self.remaining:
            self._fail_internal("byte budget exhausted")
            raise BudgetError("byte budget exhausted; run must fail closed")
        self._outstanding += amount
        token = Reservation()
        self._active[token] = _ReservationState(amount, direction)
        return token

    def reconcile(self, token: Reservation, actual: int) -> None:
        """Commit a reservation: charge ``actual`` (<= reserved), refund rest.

        Exactly-once: the token is validated against the issuing budget's
        registry, then removed, so reuse fails as an unknown token from then
        on. ``actual`` must be an exact non-negative integer with
        ``actual <= reserved``.
        """

        self._check_open()
        inner = self._check_token(token)
        if (
            isinstance(actual, bool)
            or not isinstance(actual, int)
            or not 0 <= actual <= inner.amount
        ):
            self._fail_internal("reconcile actual outside 0..reserved")
            raise BudgetError("reconcile actual invalid or exceeds reservation")
        del self._active[token]  # remove before accounting; consumed tokens are gone
        self._outstanding -= inner.amount
        if self._outstanding < 0:
            self._fail_internal("outstanding accounting underflow")
            raise BudgetError("outstanding accounting underflow")
        self._actual += actual
        if inner.direction == DIRECTION_READ:
            self._actual_read += actual
        else:
            self._actual_write += actual
        if self._actual > self._limit:
            self._fail_internal("charged bytes exceed limit")
            raise BudgetError("charged bytes exceed limit")
        if self._actual == self._limit:
            self.close("byte budget fully consumed")

    def cancel(self, token: Reservation) -> None:
        """Terminate a reservation with a verified zero-byte invoice.

        Caller contract: this is for operations abandoned before any bytes
        were transferred (it asserts a zero-byte invoice via ``reconcile``).
        It makes no claim that every real-world I/O cancellation transfers
        nothing; partial transfers must be reconciled with the true count.
        """

        self.reconcile(token, 0)

    def close(self, reason: str) -> None:
        """Latch the budget closed (e.g. limit exhaustion or run teardown).

        Outstanding reservations at close time are not refunded or charged:
        they are left unresolved, and any future run must treat the run as
        failed/incomplete, never as successful completion.
        """

        if self._closed:
            return
        self._closed = True
        self._closed_reason = reason
        # Registry entries may linger if reservations were outstanding at
        # close; they can no longer be reconciled (closed-budget rejection)
        # and will be garbage-collected with the budget.

    fail_run = close  # semantic alias: a terminal limit failure ends the run

    def _check_open(self) -> None:
        if self._closed:
            raise BudgetError(f"budget closed ({self._closed_reason}); no reuse")

    def _check_token(self, token: Reservation) -> _ReservationState:
        # Registry lookup is the only authority; token identity must belong to
        # this budget exactly once. Nothing about the token itself is trusted:
        # caller-constructed instances, subclasses and mutated tokens are
        # simply absent from the registry and fail closed. Double use fails
        # generically because consumed tokens are removed from the registry.
        # Reject subclasses before hashing/comparing: user-defined equality
        # must never impersonate an issued identity in the registry.
        inner = self._active.get(token) if type(token) is Reservation else None
        if inner is None:
            self._fail_internal("unknown reservation token")
            raise BudgetError("unknown reservation token (unissued, foreign or consumed)")
        return inner

    def _fail_internal(self, reason: str) -> None:
        if not self._closed:
            self._closed = True
            self._closed_reason = reason
