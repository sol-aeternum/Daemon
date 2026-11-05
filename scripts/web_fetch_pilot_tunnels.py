"""Offline pure tunnel-lifecycle ledger for the approved web-fetch fallback pilot.

This implements ONLY the offline, in-memory, fail-latched ledger of the
concrete IPC design (docs/WEB_FETCH_FALLBACK_PILOT_DESIGN.md, "IPC framing,
lifecycle and bounds"): admission against a fixed canonical host manifest,
tunnel state transitions, half-close semantics and per-direction
flow-control credit. Runtime ownership is gateway-authoritative only: the
gateway records both incoming and locally generated outgoing transitions.
The browser relay needs separate endpoint-local state, not a mirrored ledger.
This object has no peers of its own and all transitions are
validated with the existing core frame codec
(``scripts.web_fetch_pilot_core``) — no alternate codec is added.

What this module deliberately does NOT do (no claim of any of these is
made): no socket, process, browser, DNS, thread or subprocess activity and
no imports enabling them; no actual forwarding, mux/pipe scheduling or
fairness policy. RESULT frames are flatly rejected here — result,
deadline and shared byte-budget accounting are deferred to the supervisor.
``drain`` is a trusted-LOCAL-consumer method (permission hygiene, not a
revocable capability): remote peers can never self-replenish credit.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Sequence

from scripts.web_fetch_pilot_core import (
    Frame,
    FrameCodecError,
    FrameParser,
    FrameType,
    MAX_PAYLOAD,
    check_open_payload,
    encode_frame,
    require_canonical_hostname,
)

INITIAL_CREDIT = 65536  # fixed 64 KiB receive credit per opened direction
MAX_OPEN_ATTEMPTS = 40  # includes refused attempts; never a runtime override
MAX_PENDING_OR_OPEN = 4  # pending + open tunnels combined


class Peer(Enum):
    """The two fixed ledger sides. Frames must state which side sent them."""

    BROWSER = "browser"
    GATEWAY = "gateway"


def opposite(peer: Peer) -> Peer:
    """Return the fixed opposite side of the two-peer ledger.

    Raises ``ValueError`` for any non-``Peer`` argument (including direct
    calls); public ledger entry points latch such misuse instead.
    """

    if not isinstance(peer, Peer):
        raise ValueError(f"opposite() requires a Peer, got {peer!r}")
    return Peer.GATEWAY if peer is Peer.BROWSER else Peer.BROWSER


class TunnelLedgerError(Exception):
    """Fatal, fail-latched ledger violation: the whole run must terminate."""

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class TunnelStatus(IntEnum):
    """Tunnel status. ``OPEN_ERROR``/``CLOSED`` are terminal (retained to
    detect stale IDs; terminal identity is never recycled)."""

    PENDING = 1
    OPEN = 2
    OPEN_ERROR = 3  # terminal: gateway OPEN_ERROR refusal
    CLOSED = 4  # terminal: CLOSE by either side


_OPEN_STATES = (TunnelStatus.PENDING, TunnelStatus.OPEN)


# ---------------------------------------------------------------------------
# Immutable read-only snapshot records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One recorded OPEN attempt, accepted or refused (refusal reason only)."""

    stream_id: int
    host: str | None
    accepted: bool
    reason: str


@dataclass(frozen=True, slots=True)
class DirectionSnapshot:
    """Read-only view of one sending direction's credit/queue accounting."""

    send_credit: int
    queued_bytes: int
    queued_chunks: tuple[bytes, ...]
    drained: int
    credited: int
    half_closed: bool


@dataclass(frozen=True, slots=True)
class TunnelSnapshot:
    """Read-only tunnel view; direction state is ``None`` once terminal."""

    stream_id: int
    host: str
    status: TunnelStatus
    browser: DirectionSnapshot | None
    gateway: DirectionSnapshot | None


@dataclass(frozen=True, slots=True)
class LedgerSnapshot:
    """Read-only whole-ledger view (never exposes mutable internal state)."""

    failed: bool
    failure_reason: str | None
    attempts: tuple[AttemptRecord, ...]
    tunnels: tuple[TunnelSnapshot, ...]


class _Half:
    """Internal mutable per-direction state (never exposed directly)."""

    __slots__ = ("credit", "queued_chunks", "drained", "credited", "half_closed")

    def __init__(self) -> None:
        self.credit = INITIAL_CREDIT
        self.queued_chunks: list[bytes] = []
        self.drained = 0
        self.credited = 0
        self.half_closed = False

    def queued_bytes(self) -> int:
        return sum(len(c) for c in self.queued_chunks)

    def snapshot(self) -> DirectionSnapshot:
        return DirectionSnapshot(
            send_credit=self.credit,
            queued_bytes=self.queued_bytes(),
            queued_chunks=tuple(self.queued_chunks),
            drained=self.drained,
            credited=self.credited,
            half_closed=self.half_closed,
        )


class _Tunnel:
    """Internal mutable tunnel record; queues are discarded at close."""

    __slots__ = ("stream_id", "host", "status", "halves")

    def __init__(self, stream_id: int, host: str) -> None:
        self.stream_id = stream_id
        self.host = host
        self.status = TunnelStatus.PENDING
        self.halves: dict[Peer, _Half] | None = {
            Peer.BROWSER: _Half(),
            Peer.GATEWAY: _Half(),
        }


class TunnelLedger:
    """Fail-latched offline ledger of tunnel lifecycle and flow credit.

    Gateway-authoritative state: every handler takes an explicit ``Peer``
    sender and enforces per-message sender rules — OPEN only from the
    browser, OPEN_OK/OPEN_ERROR only from the gateway on a pending tunnel,
    DATA/WINDOW/HALF_CLOSE/CLOSE from either side. Shapes re-verify the
    core codec contract (DATA <= 16 KiB, WINDOW one 4-byte uint).

    Admission: a fixed set of exact canonical manifest hostnames (no
    wildcard/suffix/IDNA/URL forms, no runtime auto-add); ``open`` parses
    each OPEN payload independently (``check_open_payload``, no DNS) and
    requires exact membership. At most 40 OPEN attempts total including
    refused ones, strictly monotonic IDs 1..40 never reused, at most 4
    pending+open tunnels. Refusals are typed outcomes recorded as attempts
    (``open`` returns ``False``, no pending state); protocol violations
    (wrong side, stale/malformed/out-of-order ID, invalid transition,
    forged WINDOW, over-credit data, post-failure reuse, RESULT frames)
    latch the whole ledger failed: every later method then raises
    :class:`TunnelLedgerError` with no recovery.

    Credit: each direction starts with ``INITIAL_CREDIT``; DATA consumes
    sender credit and queues payload (queued per direction never exceeds
    ``INITIAL_CREDIT``). ``drain`` is a trusted local-consumer method (see
    module docstring) recording that queued bytes were actually processed.
    WINDOW replenishes only the OPPOSITE side's sending credit, bounded by
    drained-but-not-yet-credited amounts, so credit never exceeds
    ``INITIAL_CREDIT``. After a HALF_CLOSE the side may not send DATA but
    WINDOW for prior drained debt stays allowed until whole CLOSE, which
    disposes queues/credit and retains terminal identity; all post-
    terminal handling (including drain and WINDOW) latches failure.
    """

    __slots__ = ("_manifest", "_attempts", "_tunnels", "_failed", "_failure_reason")

    def __init__(self, manifest: Sequence[str]) -> None:
        seen: list[str] = []
        for host in manifest:
            require_canonical_hostname(host)  # exact canonical host only
            if host not in seen:
                seen.append(host)
        self._manifest: tuple[str, ...] = tuple(seen)
        self._attempts: list[AttemptRecord] = []
        self._tunnels: dict[int, _Tunnel] = {}
        self._failed = False
        self._failure_reason: str | None = None

    # -- read-only surface -------------------------------------------------

    @property
    def manifest(self) -> tuple[str, ...]:
        return self._manifest

    @property
    def is_failed(self) -> bool:
        return self._failed

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    def snapshot(self) -> LedgerSnapshot:
        """Bind an immutable read-only view of the current ledger state."""

        return LedgerSnapshot(
            failed=self._failed,
            failure_reason=self._failure_reason,
            attempts=tuple(self._attempts),
            tunnels=tuple(
                TunnelSnapshot(
                    stream_id=t.stream_id,
                    host=t.host,
                    status=t.status,
                    browser=t.halves[Peer.BROWSER].snapshot() if t.halves else None,
                    gateway=t.halves[Peer.GATEWAY].snapshot() if t.halves else None,
                )
                for t in self._tunnels.values()
            ),
        )

    # -- fatal handling ----------------------------------------------------

    def _fail(self, reason: str) -> TunnelLedgerError:
        if not self._failed:
            self._failed = True
            self._failure_reason = reason
        return TunnelLedgerError(reason)

    def _check_usable(self) -> None:
        if self._failed:
            raise TunnelLedgerError(f"ledger failed earlier: {self._failure_reason}")

    _CODEC_ERRORS = (FrameCodecError, ValueError, TypeError, struct.error)

    def _validate_entry(self, peer: Peer, frame: Frame) -> None:
        """Latch-fail on ANY malformed input before dispatch.

        Verifies exact types (no int aliasing a FrameType, no bool stream
        id, immutable ``bytes`` payload, exact ``Peer``/``Frame`` types)
        and then re-validates per-type shape through the actual core codec:
        ``encode_frame`` + a fresh ``FrameParser`` (both directions of the
        core do per-type payload/stream validation; the encode helper alone
        does not). A shape a fresh parser would reject can never enter the
        state machine, so a mutable ``bytearray`` payload or an
        unvalidated type-specific payload cannot bypass credit/shape rules.
        Any codec error is converted to a latched :class:`TunnelLedgerError`;
        no raw exception escapes.
        """

        if type(peer) is not Peer:
            raise self._fail(f"sender must be an exact Peer, got {peer!r}")
        if type(frame) is not Frame:
            raise self._fail(f"frame must be an exact core Frame, got {type(frame)!r}")
        if type(frame.frame_type) is not FrameType:
            raise self._fail("frame_type must be an exact FrameType member")
        if type(frame.stream_id) is not int:
            raise self._fail("stream_id must be an exact int")
        if type(frame.payload) is not bytes:
            raise self._fail("payload must be immutable bytes")
        try:
            raw = encode_frame(frame.frame_type, frame.stream_id, frame.payload)
            parser = FrameParser()
            frames = list(parser.feed(raw))
            parser.end_of_stream()
            if len(frames) != 1:
                raise FrameCodecError("reparse did not yield exactly one frame")
        except self._CODEC_ERRORS as exc:
            raise self._fail(f"frame shape invalid: {exc}") from exc

    def _tunnel(self, reason_prefix: str, stream_id: int) -> _Tunnel:
        tunnel = self._tunnels.get(stream_id)
        if tunnel is None:
            raise self._fail(f"{reason_prefix} for unknown/deleted stream id {stream_id}")
        return tunnel

    def _half(self, tunnel: _Tunnel, peer: Peer) -> _Half:
        if tunnel.halves is None:
            raise self._fail(f"tunnel {tunnel.stream_id} is terminal")
        half = tunnel.halves.get(peer) if isinstance(peer, Peer) else None
        if half is None:
            raise self._fail(f"unknown ledger sender side {peer!r}")
        return half

    # -- frame handlers ----------------------------------------------------

    def _open(self, peer: Peer, frame: Frame) -> bool:
        if peer is not Peer.BROWSER:
            raise self._fail("OPEN sent by non-browser peer")
        expected = len(self._attempts) + 1
        if frame.stream_id != expected:
            raise self._fail(
                f"OPEN stream id {frame.stream_id} is not the next monotonic id {expected}"
            )
        if len(self._attempts) >= MAX_OPEN_ATTEMPTS:
            raise self._fail("OPEN attempt ceiling exceeded (40 attempts total)")
        try:
            text = check_open_payload(frame.payload)
        except FrameCodecError as exc:
            raise self._fail(f"malformed OPEN payload: {exc.reason}") from exc
        host = text[: -len(":443")]
        tunnel = _Tunnel(frame.stream_id, host)
        # Capacity counts pending/open tunnels only: terminal identities are
        # retained for stale-ID detection and never count against capacity.
        active = sum(tunnel.status in _OPEN_STATES for tunnel in self._tunnels.values())
        if active + 1 > MAX_PENDING_OR_OPEN:
            self._attempts.append(
                AttemptRecord(frame.stream_id, host, False, "pending/open capacity reached")
            )
            return False
        if host not in self._manifest:
            self._attempts.append(
                AttemptRecord(frame.stream_id, host, False, "host not in fixed manifest")
            )
            return False
        self._attempts.append(AttemptRecord(frame.stream_id, host, True, "admitted"))
        self._tunnels[frame.stream_id] = tunnel
        return True

    def _open_reply(self, frame_type: FrameType, peer: Peer, frame: Frame) -> None:
        if peer is Peer.BROWSER:
            raise self._fail(f"{frame_type.name} sent by non-gateway peer")
        tunnel = self._tunnel(frame_type.name, frame.stream_id)
        if tunnel.status is not TunnelStatus.PENDING:
            raise self._fail(
                f"{frame_type.name} for tunnel {frame.stream_id} in status {tunnel.status.name}"
            )
        tunnel.status = (
            TunnelStatus.OPEN if frame_type is FrameType.OPEN_OK else TunnelStatus.OPEN_ERROR
        )
        if tunnel.status is TunnelStatus.OPEN_ERROR:
            tunnel.halves = None

    def _data(self, peer: Peer, frame: Frame) -> None:
        tunnel = self._tunnel("DATA", frame.stream_id)
        if tunnel.status is TunnelStatus.PENDING:
            raise self._fail(f"DATA before OPEN_OK on tunnel {tunnel.stream_id}")
        if tunnel.halves is None:
            raise self._fail(f"DATA after terminal tunnel {tunnel.stream_id}")
        half = self._half(tunnel, peer)
        if half.half_closed:
            raise self._fail(f"DATA after this side's HALF_CLOSE on tunnel {tunnel.stream_id}")
        n = len(frame.payload)
        if n == 0:
            return  # empty DATA carries no bytes: nothing to queue or charge
        if n > MAX_PAYLOAD:
            raise self._fail(f"DATA payload {n} exceeds codec ceiling {MAX_PAYLOAD}")
        if n > half.credit:
            raise self._fail(f"DATA exceeds sender credit ({n} > {half.credit})")
        if half.queued_bytes() + n > INITIAL_CREDIT:
            raise self._fail(
                f"queued bytes would exceed {INITIAL_CREDIT} window on tunnel {tunnel.stream_id}"
            )
        half.credit -= n
        half.queued_chunks.append(frame.payload)

    def _half_close(self, peer: Peer, frame: Frame) -> None:
        tunnel = self._tunnel("HALF_CLOSE", frame.stream_id)
        if tunnel.status is not TunnelStatus.OPEN:
            raise self._fail(
                f"HALF_CLOSE for tunnel {tunnel.stream_id} in status {tunnel.status.name}"
                " (no half-close before OPEN_OK)"
            )
        half = self._half(tunnel, peer)
        if half.half_closed:
            raise self._fail(f"duplicate HALF_CLOSE from {peer.value} on tunnel {tunnel.stream_id}")
        half.half_closed = True

    def _close(self, peer: Peer, frame: Frame) -> None:
        if peer is not Peer.BROWSER and peer is not Peer.GATEWAY:  # exhaustive enum guard
            raise self._fail("CLOSE from unknown peer")
        tunnel = self._tunnel("CLOSE", frame.stream_id)
        if tunnel.halves is None:
            raise self._fail(f"CLOSE/duplicate for terminal tunnel {tunnel.stream_id}")
        tunnel.status = TunnelStatus.CLOSED
        tunnel.halves = None  # discard queues/credit; retain terminal identity

    def _window(self, peer: Peer, frame: Frame) -> None:
        tunnel = self._tunnel("WINDOW", frame.stream_id)
        if tunnel.status is not TunnelStatus.OPEN:
            raise self._fail(f"WINDOW for tunnel {tunnel.stream_id} in status {tunnel.status.name}")
        if len(frame.payload) != 4:
            raise self._fail("WINDOW payload must be exactly 4 bytes")
        amount = struct.unpack("!I", frame.payload)[0]
        if amount == 0:
            raise self._fail("forged zero WINDOW amount")
        half = self._half(tunnel, opposite(peer))
        replenishable = half.drained - half.credited
        if amount > replenishable:
            raise self._fail(
                f"WINDOW amount {amount} exceeds drained-not-yet-credited {replenishable}"
            )
        half.credited += amount
        half.credit += amount

    # -- public API --------------------------------------------------------

    def open(self, peer: Peer, frame: Frame) -> bool:
        """Admit an OPEN attempt (browser side only).

        Returns ``True`` when admitted as a pending tunnel, ``False`` for a
        typed refused attempt (recorded, never failing the run, no pending
        state). Any protocol violation raises and latches failure.
        """

        self._check_usable()
        self._validate_entry(peer, frame)
        if frame.frame_type is not FrameType.OPEN:
            raise self._fail("open() called with a non-OPEN frame")
        return self._open(peer, frame)

    def handle(self, peer: Peer, frame: Frame) -> bool | None:
        """Dispatch one framed frame from ``peer``; returns ``open()``'s bool.

        Any frame type outside the tunnel subset latches failure: RESULT is
        supervisor-owned (control stream semantics are deliberately deferred)
        and must never reach this ledger.
        """

        self._check_usable()
        self._validate_entry(peer, frame)
        match frame.frame_type:
            case FrameType.OPEN:
                return self._open(peer, frame)
            case FrameType.OPEN_OK:
                self._open_reply(FrameType.OPEN_OK, peer, frame)
            case FrameType.OPEN_ERROR:
                self._open_reply(FrameType.OPEN_ERROR, peer, frame)
            case FrameType.DATA:
                self._data(peer, frame)
            case FrameType.WINDOW:
                self._window(peer, frame)
            case FrameType.HALF_CLOSE:
                self._half_close(peer, frame)
            case FrameType.CLOSE:
                self._close(peer, frame)
            case _:
                raise self._fail(
                    f"frame type {frame.frame_type} is not a tunnel frame (RESULT is supervisor-owned)"
                )
        return None

    def drain(self, sender: Peer, stream_id: int, count: int) -> int:
        """Record ``count`` queued bytes actually processed by the trusted
        LOCAL consumer of ``sender``'s direction (returns ``count``).

        Trusted-local contract only: the caller holds this in-memory ledger
        object, not a wire peer; a network peer can never invoke this.
        ``0 < count <=`` the currently queued bytes, exactly once per byte;
        this accounting is what later bounds WINDOW amounts, so blindly
        acknowledging undrained data cannot over-credit a side.
        """

        self._check_usable()
        if type(stream_id) is not int:
            raise self._fail("drain stream_id must be an exact int")
        if not isinstance(sender, Peer):
            raise self._fail("drain sender must be a Peer")
        if type(count) is not int:
            raise self._fail("drain count must be an exact integer")
        tunnel = self._tunnel("drain", stream_id)
        if tunnel.halves is None or tunnel.status is TunnelStatus.PENDING:
            raise self._fail(
                f"drain for tunnel {stream_id} has no queued contract ({tunnel.status.name})"
            )
        half = tunnel.halves[sender]
        if count <= 0 or count > half.queued_bytes():
            raise self._fail(
                f"drain count {count} outside queued bytes {half.queued_bytes()} on tunnel {stream_id}"
            )
        remaining = count
        chunks = half.queued_chunks
        while remaining:
            if remaining >= len(chunks[0]):
                remaining -= len(chunks[0])
                chunks.pop(0)
            else:
                chunks[0] = chunks[0][remaining:]
                remaining = 0
        half.drained += count
        return count
