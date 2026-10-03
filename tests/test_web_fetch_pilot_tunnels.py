"""Tests for scripts/web_fetch_pilot_tunnels.py (offline ledger only).

Scope guard: these tests exercise ONLY the pure in-memory tunnel ledger —
manifest admission, lifecycle transitions, half-close/terminal semantics and
per-direction credit accounting. No socket, DNS, process, browser, thread or
delayed-asynchrony behaviour is exercised or claimed anywhere: this module
has no I/O, so there is nothing to mock and no network is being simulated
(as can be seen from its imports). Frames are built with the actual core
codec (``encode_frame`` + ``FrameParser``); no duplicate frame class exists
in this suite. RESULT/deadline/shared-byte-budget/pinned-connect semantics
are deliberately deferred to the supervisor (see module docstring).
"""

from __future__ import annotations

import struct

import pytest

from scripts.web_fetch_pilot_core import (
    MAX_PAYLOAD,
    Frame,
    FrameParser,
    FrameType,
    encode_frame,
)
from scripts.web_fetch_pilot_tunnels import (
    INITIAL_CREDIT,
    MAX_OPEN_ATTEMPTS,
    MAX_PENDING_OR_OPEN,
    AttemptRecord,
    LedgerSnapshot,
    opposite,
    Peer,
    TunnelLedger,
    TunnelLedgerError,
    TunnelSnapshot,
    TunnelStatus,
)

MANIFEST = ("example.com", "sub.example.com")


@pytest.mark.parametrize("entry", ["open", "handle", "drain_id", "drain_count"])
@pytest.mark.parametrize("hostile", [False, True])
def test_integer_subclasses_rejected_before_state_change(entry: str, hostile: bool) -> None:
    callbacks: list[str] = []

    class UnhashableID(int):
        __hash__ = None  # type: ignore[assignment]

    class HostileInt(int):
        def __hash__(self) -> int:
            callbacks.append("hash")
            raise AssertionError("must reject before hashing")

        def __eq__(self, other: object) -> bool:
            callbacks.append("equality")
            raise AssertionError("must reject before comparison")

        def __le__(self, other: int) -> bool:
            callbacks.append("comparison")
            raise AssertionError("must reject before comparison")

    value = HostileInt(1) if hostile else UnhashableID(1)
    led = TunnelLedger(MANIFEST)
    if entry.startswith("drain"):
        admitted(led, 1)
        led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"abc"))
    before = led.snapshot()
    with pytest.raises(TunnelLedgerError, match="exact int"):
        if entry == "open":
            led.open(Peer.BROWSER, Frame(FrameType.OPEN, value, b"example.com:443"))
        elif entry == "handle":
            led.handle(Peer.BROWSER, Frame(FrameType.OPEN, value, b"example.com:443"))
        elif entry == "drain_id":
            led.drain(Peer.BROWSER, value, 1)
        else:
            led.drain(Peer.BROWSER, 1, value)
    after = led.snapshot()
    assert led.is_failed
    assert after.attempts == before.attempts
    assert after.tunnels == before.tunnels
    assert callbacks == []
    with pytest.raises(TunnelLedgerError, match="failed earlier"):
        led.open(Peer.BROWSER, open_frame(1))
    with pytest.raises(TunnelLedgerError, match="failed earlier"):
        led.handle(Peer.BROWSER, mk(FrameType.CLOSE, 1))
    with pytest.raises(TunnelLedgerError, match="failed earlier"):
        led.drain(Peer.BROWSER, 1, 1)


def mk(frame_type: FrameType, stream_id: int, payload: bytes = b""):
    """Build one frame through the real core codec (tests duplicate nothing)."""

    parser = FrameParser()
    frames = list(parser.feed(encode_frame(frame_type, stream_id, payload)))
    assert len(frames) == 1
    return frames[0]


def open_frame(stream_id: int, host: str = "example.com") -> Frame:
    return mk(FrameType.OPEN, stream_id, host.encode() + b":443")


def ledger() -> TunnelLedger:
    return TunnelLedger(MANIFEST)


def admitted(ledger_obj: TunnelLedger, stream_id: int, host: str = "example.com") -> None:
    """OPEN from the browser plus gateway OPEN_OK (tunnel is open)."""

    assert ledger_obj.open(Peer.BROWSER, open_frame(stream_id, host)) is True
    ledger_obj.handle(Peer.GATEWAY, mk(FrameType.OPEN_OK, stream_id))


# ---------------------------------------------------------------------------
# Admission: manifest, monotonic IDs, refusal vs fatal
# ---------------------------------------------------------------------------


class TestAdmission:
    def test_manifest_construction_requires_canonical_hosts(self) -> None:
        for bad in (
            "Example.com",
            "example.com.",
            "*.example.com",
            "ex ample.com",
            "1.2.3.4",
            "openai:443",
        ):
            with pytest.raises(ValueError):
                TunnelLedger((bad,))
        ok = TunnelLedger(MANIFEST)
        assert ok.manifest == MANIFEST

    def test_open_only_from_browser_and_validated_independently(self) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError, match="non-browser"):
            led.open(Peer.GATEWAY, open_frame(1))
        assert led.is_failed

        led2 = ledger()
        # open() returns True and creates a PENDING tunnel awaiting OPEN_OK
        assert led2.open(Peer.BROWSER, open_frame(1)) is True
        assert led2.snapshot().tunnels[0].status is TunnelStatus.PENDING

    def test_exact_membership_only_refusal_not_fatal(self) -> None:
        led = ledger()
        assert led.open(Peer.BROWSER, open_frame(1, "not-in-manifest.net")) is False
        assert not led.is_failed
        attempt: AttemptRecord = led.snapshot().attempts[0]
        assert attempt.accepted is False and attempt.host == "not-in-manifest.net"

    def test_open_attempt_ids_strictly_monotonic_no_reuse(self) -> None:
        led = ledger()
        assert led.open(Peer.BROWSER, open_frame(1, "not-in-manifest.net")) is False
        with pytest.raises(TunnelLedgerError, match="monotonic"):
            led.open(Peer.BROWSER, open_frame(1))
        # stale/gap/out-of-order IDs are fatal, never a free retry
        assert led.is_failed
        with pytest.raises(TunnelLedgerError, match="monotonic"):
            led.open(Peer.BROWSER, open_frame(2))

    def test_malformed_open_payload_is_fatal_not_refusal(self) -> None:
        from scripts.web_fetch_pilot_core import Frame

        led = ledger()
        # A frame shape the codec itself would reject cannot be built through
        # the codec; the ledger still re-parses/admits independently, so this
        # uses a directly constructed frame to reach its own validation.
        forge = Frame(FrameType.OPEN, 1, b"bad-host:8443")
        with pytest.raises(TunnelLedgerError, match="OPEN payload must be hostname:443"):
            led.open(Peer.BROWSER, forge)
        assert led.is_failed

    def test_capacity_refusal_leaves_no_state_and_next_id_required(self) -> None:
        led = ledger()
        for sid in range(1, MAX_PENDING_OR_OPEN + 1):
            assert led.open(Peer.BROWSER, open_frame(sid)) is True
        assert led.open(Peer.BROWSER, open_frame(5)) is False  # capacity refused
        refusal = led.snapshot().attempts[-1]
        assert refusal.reason == "pending/open capacity reached"
        assert len(led.snapshot().tunnels) == MAX_PENDING_OR_OPEN  # no pending refusal state
        # refused id 5 was consumed; reusing it is fatal, never a free retry
        with pytest.raises(TunnelLedgerError, match="monotonic"):
            led.open(Peer.BROWSER, open_frame(5, "sub.example.com"))

    def test_refused_attempts_count_against_40_ceiling(self) -> None:
        led = ledger()
        bad = "not-in-manifest.net"
        for sid in range(1, MAX_OPEN_ATTEMPTS + 1):
            assert led.open(Peer.BROWSER, open_frame(sid, bad)) is False
        assert not led.is_failed
        assert len(led.snapshot().attempts) == MAX_OPEN_ATTEMPTS
        # id 41 is outside the codec's 1..40 range, and the codec enforces it;
        # a directly constructed frame (bypassing the codec) hits the ledger's
        # attempt-ceiling defense here, which is a fatal violation too
        from scripts.web_fetch_pilot_core import Frame

        forge = Frame(FrameType.OPEN, MAX_OPEN_ATTEMPTS + 1, b"example.com:443")
        # the codec layer rejects an out-of-range id even before the ledger's
        # monotonic/ceiling rules; either way this is a fatal violation
        with pytest.raises(TunnelLedgerError, match="outside 1..40|monotonic|ceiling"):
            led.handle(Peer.BROWSER, forge)
        assert led.is_failed


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_happy_flow_both_directions(self) -> None:
        led = ledger()
        admitted(led, 1)
        browser_data = mk(FrameType.DATA, 1, b"browser-bytes")
        gateway_data = mk(FrameType.DATA, 1, b"gateway-bytes")
        led.handle(Peer.BROWSER, browser_data)
        led.handle(Peer.GATEWAY, gateway_data)
        assert led.drain(Peer.BROWSER, 1, len(b"browser-bytes")) == len(b"browser-bytes")
        led.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", len(b"browser-bytes"))))
        assert led.drain(Peer.GATEWAY, 1, len(b"gateway-bytes")) == len(b"gateway-bytes")
        led.handle(Peer.BROWSER, mk(FrameType.WINDOW, 1, struct.pack("!I", len(b"gateway-bytes"))))
        snap = led.snapshot()
        b, g = snap.tunnels[0].browser, snap.tunnels[0].gateway
        assert b is not None and g is not None
        assert b.send_credit == INITIAL_CREDIT and g.send_credit == INITIAL_CREDIT
        assert not b.half_closed and not g.half_closed
        led.handle(Peer.BROWSER, mk(FrameType.HALF_CLOSE, 1))
        browser = led.snapshot().tunnels[0].browser
        assert browser is not None
        assert browser.half_closed is True

    def test_half_close_and_terminal_semantics(self) -> None:
        led = ledger()
        admitted(led, 1)
        led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"queued"))
        led.handle(Peer.BROWSER, mk(FrameType.HALF_CLOSE, 1))
        # opposite side may still send after this side's half-close
        led.handle(Peer.GATEWAY, mk(FrameType.DATA, 1, b"g"))
        # WINDOW for prior drained debt is allowed after HALF_CLOSE, but new
        # DATA from the closed side is rejected
        with pytest.raises(TunnelLedgerError, match="HALF_CLOSE"):
            led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"x"))

    def test_close_is_terminal_and_retains_identity(self) -> None:
        led = ledger()
        admitted(led, 1)
        led.open(Peer.BROWSER, open_frame(2))  # pending tunnel
        led.handle(Peer.BROWSER, mk(FrameType.CLOSE, 2))
        led.handle(Peer.GATEWAY, mk(FrameType.CLOSE, 1))
        snap = led.snapshot()
        statuses = {t.stream_id: t.status for t in snap.tunnels}
        assert statuses == {1: TunnelStatus.CLOSED, 2: TunnelStatus.CLOSED}
        snap = led.snapshot()
        assert snap.tunnels[0].browser is None and snap.tunnels[0].gateway is None
        with pytest.raises(TunnelLedgerError):
            led.handle(Peer.GATEWAY, mk(FrameType.DATA, 1, b"x"))
        assert led.is_failed

    def test_duplicate_and_invalid_transitions_fail_latch(self) -> None:
        led = ledger()
        led.open(Peer.BROWSER, open_frame(1))
        led.handle(Peer.GATEWAY, mk(FrameType.OPEN_OK, 1))
        with pytest.raises(TunnelLedgerError):
            led.handle(Peer.GATEWAY, mk(FrameType.OPEN_OK, 1))
        assert led.is_failed

        led2 = ledger()
        led2.open(Peer.BROWSER, open_frame(1))
        led2.handle(Peer.GATEWAY, mk(FrameType.OPEN_ERROR, 1, b"refused"))
        assert led2.snapshot().tunnels[0].status is TunnelStatus.OPEN_ERROR
        assert led2.snapshot().tunnels[0].browser is None  # no credit state retained
        with pytest.raises(TunnelLedgerError, match="OPEN_OK"):
            led2.handle(Peer.GATEWAY, mk(FrameType.OPEN_OK, 1))
        assert led2.is_failed

        led3 = ledger()
        admitted(led3, 1)
        led3.handle(Peer.BROWSER, mk(FrameType.CLOSE, 1))
        with pytest.raises(TunnelLedgerError):
            led3.handle(Peer.GATEWAY, mk(FrameType.CLOSE, 1))
        assert led3.is_failed

    def test_reply_and_data_side_rules(self) -> None:
        led = ledger()
        led.open(Peer.BROWSER, open_frame(1))
        with pytest.raises(TunnelLedgerError, match="non-gateway"):
            led.handle(Peer.BROWSER, mk(FrameType.OPEN_OK, 1))
        assert led.is_failed

        led2 = ledger()
        led2.open(Peer.BROWSER, open_frame(1))
        with pytest.raises(TunnelLedgerError, match="non-gateway"):
            led2.handle(Peer.BROWSER, mk(FrameType.OPEN_ERROR, 1, b"no"))
        assert led2.is_failed

        led4 = ledger()
        led4.open(Peer.BROWSER, open_frame(1))
        with pytest.raises(TunnelLedgerError, match="before OPEN_OK"):
            led4.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"x"))
        assert led4.is_failed

    def test_data_for_unknown_or_deleted_stream_id(self) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError, match="unknown/deleted"):
            led.handle(Peer.BROWSER, mk(FrameType.DATA, 7, b"x"))
        assert led.is_failed

    def test_result_frame_is_rejected_supervisor_owned(self) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError, match="supervisor-owned"):
            led.handle(Peer.BROWSER, mk(FrameType.RESULT, 0, b"text"))
        assert led.is_failed


# ---------------------------------------------------------------------------
# Credit / drain / window accounting
# ---------------------------------------------------------------------------


class TestCredit:
    def test_data_rejected_by_credit_and_queue_bound(self) -> None:
        led = ledger()
        admitted(led, 1)
        # exact one-direction bound: split INITIAL_CREDIT over max chunks
        chunk = b"x" * MAX_PAYLOAD
        for _ in range(INITIAL_CREDIT // MAX_PAYLOAD):
            led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, chunk))
        browser_after = led.snapshot().tunnels[0].browser
        assert browser_after is not None
        assert browser_after.queued_bytes == INITIAL_CREDIT
        with pytest.raises(TunnelLedgerError, match="sender credit|would exceed"):
            led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"y"))
        assert led.is_failed

    def test_drain_may_not_exceed_queued_or_preack(self) -> None:
        led = ledger()
        admitted(led, 1)
        with pytest.raises(TunnelLedgerError, match="outside queued"):
            led.drain(Peer.BROWSER, 1, 1)
        assert led.is_failed

        led2 = ledger()
        admitted(led2, 1)
        led2.handle(Peer.GATEWAY, mk(FrameType.DATA, 1, b"ab"))
        with pytest.raises(TunnelLedgerError, match="outside queued"):
            led2.drain(Peer.GATEWAY, 1, 3)
        assert led2.is_failed

        led3 = ledger()
        admitted(led3, 1)
        led3.handle(Peer.GATEWAY, mk(FrameType.DATA, 1, b"ab"))
        with pytest.raises(TunnelLedgerError, match="outside queued"):
            led3.drain(Peer.GATEWAY, 1, 0)
        assert led3.is_failed

        led4 = ledger()
        with pytest.raises(TunnelLedgerError, match="unknown/deleted"):
            led4.drain(Peer.BROWSER, 3, 0)
        assert led4.is_failed

    def test_window_requires_drained_not_yet_credited(self) -> None:
        led = ledger()
        admitted(led, 1)
        led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"12345"))
        led.drain(Peer.BROWSER, 1, 2)
        led.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", 2)))
        # cannot blindly acknowledge undrained data
        with pytest.raises(TunnelLedgerError, match="drained-not-yet-credited"):
            led.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", 1)))
        window_view = led.snapshot().tunnels[0].browser
        assert window_view is not None
        assert window_view.send_credit == INITIAL_CREDIT - 5 + 2
        assert window_view.credited == 2

        led2 = ledger()
        admitted(led2, 1)
        led2.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"12345"))
        # a forged zero WINDOW amount latches the run
        with pytest.raises(TunnelLedgerError, match="zero WINDOW"):
            led2.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", 0)))
        assert led2.is_failed

        led3 = ledger()
        admitted(led3, 1)
        led3.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"12345"))
        led3.drain(Peer.BROWSER, 1, 2)
        # over-credit: more than the 2 drained bytes, and self-rising credit
        with pytest.raises(TunnelLedgerError, match="drained-not-yet-credited"):
            led3.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", 3)))
        assert led3.is_failed

    def test_window_wrong_side_and_payload_shape(self) -> None:
        led = ledger()
        admitted(led, 1)
        # wrong side: a WINDOW from the browser replenishes the *gateway*
        # sending direction, which has zero drained -> over-credit fatal
        with pytest.raises(TunnelLedgerError, match="drained-not-yet-credited"):
            led.handle(Peer.BROWSER, mk(FrameType.WINDOW, 1, struct.pack("!I", 1)))
        assert led.is_failed

        led2 = ledger()
        admitted(led2, 1)
        # WINDOW payload shape is codec-enforced; reach the ledger's entry
        # re-validation with a directly constructed frame
        from scripts.web_fetch_pilot_core import Frame

        forge = Frame(FrameType.WINDOW, 1, b"tiny1")
        with pytest.raises(TunnelLedgerError, match="shape invalid"):
            led2.handle(Peer.GATEWAY, forge)
        assert led2.is_failed

    def test_window_in_pending_and_terminal(self) -> None:
        led = ledger()
        led.open(Peer.BROWSER, open_frame(1))
        with pytest.raises(TunnelLedgerError, match="WINDOW for tunnel"):
            led.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", 1)))
        led2 = ledger()
        admitted(led2, 1)
        led2.handle(Peer.GATEWAY, mk(FrameType.CLOSE, 1))
        with pytest.raises(TunnelLedgerError):
            led2.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", 1)))
        assert led2.is_failed

    def test_drain_after_halfclose_terminal_and_recv_side_ok(self) -> None:
        led = ledger()
        admitted(led, 1)
        led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"abc"))
        led.handle(Peer.BROWSER, mk(FrameType.HALF_CLOSE, 1))
        lead = led.drain(Peer.BROWSER, 1, 3)  # still allowed for prior data
        assert lead == 3
        led.handle(Peer.GATEWAY, mk(FrameType.CLOSE, 1))
        with pytest.raises(TunnelLedgerError, match="no queued contract"):
            led.drain(Peer.BROWSER, 1, 0)

    def test_both_directions_symmetric_fully_independent(self) -> None:
        led = ledger()
        admitted(led, 1)
        payload = b"y" * 100
        led.handle(Peer.GATEWAY, mk(FrameType.DATA, 1, payload))
        led.handle(Peer.GATEWAY, mk(FrameType.HALF_CLOSE, 1))
        # browser direction unaffected by gateway's half-close
        led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, payload))
        lid = led.drain(Peer.GATEWAY, 1, 50)
        assert lid == 50
        snap = led.snapshot()
        gateway_view = snap.tunnels[0].gateway
        browser_view = snap.tunnels[0].browser
        assert gateway_view is not None and browser_view is not None
        assert gateway_view.queued_bytes == 50
        assert browser_view.queued_bytes == 100


# ---------------------------------------------------------------------------
# Aggregate ceilings, snapshots, fail-latch
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Entry-shape validation (fresh ledger per case; never reused after a latch)
# ---------------------------------------------------------------------------


class TestEntryShapeValidation:
    """Frames below bypass the codec and exercise the ledger's own entry
    re-validation, which must latch every malformed input before dispatch.
    Every case constructs a fresh ledger; nothing is retried after latching."""

    def test_each_message_type_with_invalid_shape_latches(self) -> None:
        cases = [
            (FrameType.OPEN, 1, b"no-suffix"),
            (FrameType.OPEN_OK, 1, b"payload-not-allowed"),
            (FrameType.OPEN_ERROR, 1, b""),
            (FrameType.DATA, 1, b"x" * (MAX_PAYLOAD + 1)),
            (FrameType.WINDOW, 1, b"win"),
            (FrameType.HALF_CLOSE, 1, b"payload-not-allowed"),
            (FrameType.CLOSE, 1, b"payload-not-allowed"),
            (FrameType.RESULT, 0, b""),
        ]
        for frame_type, stream_id, payload in cases:
            led = ledger()
            with pytest.raises(TunnelLedgerError, match="shape invalid"):
                led.handle(Peer.BROWSER, Frame(frame_type, stream_id, payload))
            assert led.is_failed

    @pytest.mark.parametrize(
        ("peer_arg", "frame_type", "payload"),
        [
            ("gateway", FrameType.OPEN_OK, b""),
            ("browser", FrameType.OPEN, b"example.com:443"),
            (Peer.BROWSER, FrameType.OPEN, bytearray(b"example.com:443")),
            ("gateway", FrameType.WINDOW, b"\0\0\0\1"),
            (42, FrameType.DATA, b"x"),
        ],
    )
    def test_invalid_peer_or_mutable_payload_never_enters(
        self, peer_arg: object, frame_type: FrameType, payload: object
    ) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError, match="exact Peer|immutable bytes"):
            led.handle(peer_arg, Frame(frame_type, 1, payload))  # type: ignore[arg-type]
        assert led.is_failed

    @pytest.mark.parametrize("stream_id", [[], True, 1.5, "1", None])
    def test_invalid_stream_ids_latch_not_typeerror(self, stream_id: object) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError, match="exact int"):
            led.handle(Peer.BROWSER, Frame(FrameType.OPEN_OK, stream_id, b""))  # type: ignore[call-overload]
        assert led.is_failed

    @pytest.mark.parametrize(
        ("stream_id", "count"),
        [([], 1), (True, 1), (1, True), (1, 1.5), (1.5, 1)],
    )
    def test_public_drain_validates_before_tunnel(self, stream_id: object, count: object) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError):
            led.drain(Peer.BROWSER, stream_id, count)  # type: ignore[call-overload]
        assert led.is_failed

    def test_half_close_in_pending_is_rejected(self) -> None:
        led = ledger()
        assert led.open(Peer.BROWSER, open_frame(1)) is True
        with pytest.raises(TunnelLedgerError, match="HALF_CLOSE.*before OPEN_OK|no half-close"):
            led.handle(Peer.BROWSER, mk(FrameType.HALF_CLOSE, 1))
        assert led.is_failed

    def test_maximum_shaped_data_passes_entry_validation(self) -> None:
        led = ledger()
        admitted(led, 1)
        # maximum codec-shaped frames validate cleanly on entry
        assert led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"x" * MAX_PAYLOAD)) is None
        assert not led.is_failed

    def test_maximum_length_open_payload_shape_validates(self) -> None:
        long_host = "a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 59
        assert len(long_host) == 251
        big_open = mk(FrameType.OPEN, 1, long_host.encode() + b":443")
        assert len(big_open.payload) == 255
        # through `open`'s entry re-validation: canonical at the 255-byte
        # codec ceiling, refused only by exact manifest membership
        accepted_ledger = ledger()
        assert accepted_ledger.open(Peer.BROWSER, big_open) is False
        assert not accepted_ledger.is_failed
        assert accepted_ledger.snapshot().attempts[0].reason == "host not in fixed manifest"

    def test_valid_window_and_halfclose_after_prior_debt_still_work(self) -> None:
        led = ledger()
        admitted(led, 1)
        led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"xy"))
        led.drain(Peer.BROWSER, 1, 1)
        led.handle(Peer.BROWSER, mk(FrameType.HALF_CLOSE, 1))
        # replenishment of prior drained debt is allowed after half-close
        led.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", 1)))
        led.drain(Peer.BROWSER, 1, 1)
        led.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, struct.pack("!I", 1)))
        view = led.snapshot().tunnels[0].browser
        assert view is not None and view.send_credit == INITIAL_CREDIT

    def test_result_shape_valid_then_supervisor_rejection(self) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError, match="supervisor-owned"):
            led.handle(Peer.BROWSER, mk(FrameType.RESULT, 0, b"text"))
        assert led.is_failed

    def test_opposite_helper_raises_valueerror_on_invalid(self) -> None:
        with pytest.raises(ValueError, match="requires a Peer"):
            opposite("gateway")  # type: ignore[arg-type]
        assert opposite(Peer.BROWSER) is Peer.GATEWAY
        assert opposite(Peer.GATEWAY) is Peer.BROWSER


class TestBoundsAndLatching:
    def test_aggregate_bound_four_tunnels_both_directions(self) -> None:
        led = ledger()  # aggregate inherent cap: 4 * 2 * 64 KiB per run
        chunk = b"x" * MAX_PAYLOAD
        full = INITIAL_CREDIT // MAX_PAYLOAD
        for sid in range(1, MAX_PENDING_OR_OPEN + 1):
            admitted(led, sid)
            for _ in range(full):
                led.handle(Peer.BROWSER, mk(FrameType.DATA, sid, chunk))
                led.handle(Peer.GATEWAY, mk(FrameType.DATA, sid, chunk))
        snap = led.snapshot()
        sides = [side for tunnel in snap.tunnels for side in (tunnel.browser, tunnel.gateway)]
        assert all(side is not None for side in sides)
        total = sum((side.queued_bytes for side in sides if side is not None), 0)
        assert total == MAX_PENDING_OR_OPEN * 2 * INITIAL_CREDIT
        # one extra chunk anywhere breaches the per-direction window
        with pytest.raises(TunnelLedgerError, match="would exceed|credit"):
            led.handle(Peer.GATEWAY, mk(FrameType.DATA, 1, chunk))
        assert led.is_failed

    def test_fail_latch_blocks_every_method(self) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError):
            led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"x"))
        for call in (
            lambda: led.open(Peer.BROWSER, open_frame(1)),
            lambda: led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"x")),
            lambda: led.handle(Peer.GATEWAY, mk(FrameType.OPEN_OK, 1)),
            lambda: led.handle(Peer.GATEWAY, mk(FrameType.OPEN_ERROR, 1, b"e")),
            lambda: led.handle(Peer.GATEWAY, mk(FrameType.WINDOW, 1, b"\0\0\0\1")),
            lambda: led.handle(Peer.BROWSER, mk(FrameType.HALF_CLOSE, 1)),
            lambda: led.handle(Peer.BROWSER, mk(FrameType.CLOSE, 1)),
            lambda: led.drain(Peer.BROWSER, 1, 1),
        ):
            with pytest.raises(TunnelLedgerError, match="failed earlier"):
                call()
        assert led.is_failed
        assert led.failure_reason is not None

    def test_open_rejects_non_open_frame_shape(self) -> None:
        led = ledger()
        with pytest.raises(TunnelLedgerError, match="non-OPEN frame"):
            led.open(Peer.BROWSER, mk(FrameType.DATA, 1, b"x"))
        assert led.is_failed

        led2 = ledger()
        with pytest.raises(TunnelLedgerError, match="unknown/deleted"):
            led2.drain(Peer.BROWSER, 1, 1)  # nonexistent id -> unknown/deleted
        assert led2.is_failed

        led3 = ledger()
        with pytest.raises(TunnelLedgerError, match="Peer"):
            led3.drain("browser", 1, 1)  # type: ignore[arg-type]  # latched, not KeyError
        assert led3.is_failed

    def test_dispatch_does_not_confuse_sender_side(self) -> None:
        # handle() dispatches by frame type; sender-side rules still apply,
        # so a browser OPEN_OK latches with the gateway-only reason
        led = ledger()
        led.open(Peer.BROWSER, open_frame(1))
        with pytest.raises(TunnelLedgerError, match="non-gateway"):
            led.handle(Peer.BROWSER, mk(FrameType.OPEN_OK, 1))
        assert led.is_failed

    def test_snapshots_are_immutable_read_only_views(self) -> None:
        led = ledger()
        admitted(led, 1)
        led.handle(Peer.BROWSER, mk(FrameType.DATA, 1, b"frozen bytes"))
        snap = led.snapshot()
        assert isinstance(snap, LedgerSnapshot)
        assert isinstance(snap.tunnels[0], TunnelSnapshot)
        with pytest.raises(AttributeError):
            snap.tunnels = ()  # type: ignore[misc]
        with pytest.raises(AttributeError):
            snap.tunnels[0].status = TunnelStatus.CLOSED  # type: ignore[misc]
        with pytest.raises(AttributeError):
            snap.tunnels[0].browser.queued_chunks = ()  # type: ignore[misc]
        with pytest.raises(AttributeError):
            led.limit = 1  # type: ignore[attr-defined]  # no runtime overrides exist
        assert led.snapshot() == snap
