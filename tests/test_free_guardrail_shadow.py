"""Provider-free comparison of current Free sizing and a shadow spending policy.

Run: uv run pytest -qs tests/test_free_guardrail_shadow.py
Durations, scripted usage, user authority and useful-answer milestones are fixtures.
Prices, eligibility, output sizing, admission arithmetic and finish validation use
the pinned repository. The serialized scheduler is not a distributed lock test.
"""

from dataclasses import dataclass, field, replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from orchestrator import compute_runtime as rt
from orchestrator.entitlements import ledger
from orchestrator.entitlements.models import AccountRecord
from orchestrator.entitlements.plans import AccountStatus, ChargeKind, Plan, TrialState
from orchestrator.entitlements.policy import load_policy
from orchestrator.entitlements.resolver import resolve_account_policy
from orchestrator.turn_finish import (
    LocalLimit,
    ProviderFinish,
    TurnFinish,
    answer_finish,
    refused_finish,
)
from tests.test_compute_runtime import _route
from tests.test_production_inference_policy import (
    REVIEWED,
    _attested as _attested,
    production_policy as production_policy,
)

BASE = resolve_account_policy(
    AccountRecord(
        UUID(int=1),
        Plan.FREE,
        "fixture",
        AccountStatus.ACTIVE,
        False,
        TrialState.EXHAUSTED,
        0,
        0,
        0,
    ),
    load_policy("config/commercial.json"),
    period="2026-11",
)
MONTH = BASE.recurring_budget_microusd
LUNA = "luna-azure"
GLM = "glm-flash-inceptron-fp8"
DS = "deepseek-flash-coreweave-fp8"
# These signals are experimental knobs, not commercial defaults.
DEADLINE, FINAL_SECONDS, RETRIES, REPEAT = 90, 8, 1, 2
PARAMS = {"messages": [{"role": "user", "content": "x" * 600}]}
SIZE = rt._request_bound(PARAMS)


def wire(reason):
    return {"state": "confirmed", "reason": reason, "provenance": "provider_wire"}


@dataclass
class Book:
    period: ledger.PeriodState
    admitted: set = field(default_factory=set)

    @classmethod
    def with_balance(cls, balance):
        assert 0 <= balance <= MONTH
        return cls(ledger.PeriodState(BASE.period_key, spent_microusd=MONTH - balance))

    def reserve(self, root, quote, draw=0):
        # One transition: release only this root's F into the call, or change nothing.
        assert 0 <= draw <= min(root.escrow, quote)
        if root.permission is not None and quote - draw > root.unallocated():
            return "permission_exceeded"
        p = replace(self.period, reserved_microusd=self.period.reserved_microusd - draw)
        now = REVIEWED + timedelta(seconds=root.t)
        req = ledger.ReservationRequest(quote, shares_rate_slot=root.key in self.admitted)
        decision = ledger.admit(
            p, req, ledger.AdmissionContext(BASE.limits, MONTH, ChargeKind.PLAN, now)
        )
        if not decision:
            return decision.denial_code
        self.period = ledger.apply_reservation(p, req, now)
        self.admitted.add(root.key)
        root.escrow -= draw
        root.held += quote
        return None

    def settle(self, root, quote, charge):
        assert 0 <= charge <= quote  # only the scripted bounded-usage cases
        self.period = ledger.settle_state(
            self.period,
            reserved_microusd=quote,
            actual_microusd=charge,
            extended=False,
            now=REVIEWED + timedelta(seconds=root.t),
        )
        root.settled += charge
        root.held -= quote


@dataclass
class Turn:
    book: Book
    shadow: bool
    key: str = "a"
    t: int = 0
    calls: int = 0
    quotes: int = 0
    settled: int = 0
    escrow: int = 0
    rounds: int = 0
    retries: int = 0
    seen: dict = field(default_factory=dict)
    notices: list = field(default_factory=list)
    offers: list = field(default_factory=list)  # pre-dispatch full/economical choices
    finish: TurnFinish | None = None
    useful: bool = False
    truncated: bool = False
    deadline: int = DEADLINE
    permission: int | None = None
    held: int = 0

    def unallocated(self):
        assert self.permission is not None
        return self.permission - self.held - self.settled - self.escrow

    def preflight(self, quote, consent):
        free = self.book.period.remaining(MONTH)
        if self.shadow and quote and quote * 2 >= free:
            self.notices.append((self.calls, quote, free, consent))
            if not consent:
                self.local("await_spend_choice")
                return False
        if self.shadow:
            assert self.permission is None
            self.permission = min(quote, free)
        return True

    def candidates(self, *, final=False, ask=None, size=SIZE, model=None, offer_budget=None):
        p = self.book.period
        policy = replace(
            BASE,
            limits=replace(BASE.limits, max_output_tokens=None) if self.shadow else BASE.limits,
            period_spent_microusd=p.spent_microusd,
            period_reserved_microusd=p.reserved_microusd - (self.escrow if final else 0),
        )
        if self.permission is not None or offer_budget is not None:
            available = policy.remaining_for(False)
            if self.permission is not None:
                available = min(available, self.unallocated() + (self.escrow if final else 0))
            if offer_budget is not None:
                available = min(available, offer_budget)
            # Synthetic effective pool for the pure sizer; never mutate the ledger.
            policy = replace(
                policy,
                recurring_budget_microusd=available,
                period_spent_microusd=0,
                period_reserved_microusd=0,
            )
        args = dict(PARAMS, max_tokens=ask) if ask is not None else PARAMS
        return rt._priced_candidates(policy, size, args, model, fit_to_budget=True)

    def protect(self, quote):
        if not self.shadow:
            return True
        p = self.book.period
        if quote > p.remaining(MONTH) or (
            self.permission is not None and quote > self.unallocated()
        ):
            return False
        self.book.period = replace(p, reserved_microusd=p.reserved_microusd + quote)
        self.escrow += quote
        return True

    def observe(self):
        # Reconnect only sees the original root. No start/resume/dispatch method here.
        return (
            self.key,
            self.t,
            self.calls,
            self.rounds,
            self.retries,
            tuple(self.seen.items()),
            self.book.period,
            self.escrow,
            self.finish,
            self.permission,
            self.held,
            self.deadline,
        )

    def call(self, entry, *, tokens, seconds, final=False, rejected=False, reconnect=None):
        assert self.finish is None
        cutoff = self.t + 90  # baseline per-completion deadline, seconds
        if self.shadow:
            cutoff = min(cutoff, self.deadline if final else self.deadline - FINAL_SECONDS)
        if self.t >= cutoff:
            return "deadline_before_dispatch"
        quote, sent, route, _, _ = entry
        draw = min(quote, self.escrow) if final else 0
        assert not self.book.reserve(self, quote, draw)
        self.calls += 1
        self.quotes += quote
        # Scripted provider output starts immediately except for proven 429s.
        end = self.t + seconds
        if reconnect:
            self.t += 2
            # A new observer attaches via the server's existing turn key; the
            # scheduler and in-flight call are not reconstructed by the client.
            snapshot = reconnect(self.key)
            assert snapshot == self.observe() and snapshot[6].open_reservations == 1
        self.t = min(cutoff, end)
        if end > cutoff:
            self.book.settle(self, quote, quote)  # partial output; unknown usage
            return "deadline_exceeded"
        n = min(tokens, sent)
        charge = 0 if rejected else route.estimate_microusd(SIZE.estimate, n)
        self.book.settle(self, quote, charge)
        if rejected:
            return "429"
        if final:
            self.truncated = n < tokens
            self.finish = answer_finish(wire("length" if self.truncated else "stop"))
        return "delivered"

    def local(self, reason, *, attempted=False):
        provider = (
            self.finish.provider
            if self.finish
            else ProviderFinish(state="missing" if attempted else "not_attempted")
        )
        self.finish = TurnFinish(
            outcome="incomplete",
            reason=reason,
            provider=provider,
            limits=[LocalLimit(kind=reason, binding=True)],
        )

    def seal(self):
        assert self.held == 0
        p = self.book.period
        self.book.period = replace(p, reserved_microusd=p.reserved_microusd - self.escrow)
        self.escrow = 0
        assert self.finish and (not self.useful or self.finish.finish_reason == "stop")
        if any(limit.binding for limit in self.finish.limits):
            assert self.finish.finish_reason != "stop"
        assert self.book.period.reserved_microusd == self.book.period.open_reservations == 0


def direct(turn, tokens, *, consent=False, model=None, competing=False, choose_offer=None):
    entries = turn.candidates(final=True, model=model)
    if not entries:
        turn.finish = refused_finish("budget_exceeded", not_attempted=True)
        return
    first = entries[0]
    if choose_offer is not None:
        first = choose_offer(turn, first, model, consent)
    quote = first[0]
    # Fixture sensitivity point, not an adopted threshold. Notice precedes any hold.
    if not turn.preflight(quote, consent):
        return
    if competing:
        second = Turn(turn.book, turn.shadow, key="b")
        stale = second.candidates(final=True)[0][0]
        assert stale == quote
        # Deterministic interleaving: both observed the same balance. A has the
        # slot while B tries; after A settles B's old quote must be checked anew.
        assert not turn.book.reserve(turn, quote)
        assert turn.book.reserve(second, stale) == "concurrency_exceeded"
        turn.calls += 1
        turn.quotes += quote
        turn.t += 5
        n = min(tokens, first[1])
        turn.book.settle(turn, quote, first[2].estimate_microusd(SIZE.estimate, n))
        turn.truncated = n < tokens
        turn.finish = answer_finish(wire("length" if turn.truncated else "stop"))
        assert turn.book.reserve(second, stale) == "budget_exceeded"
    else:
        result = turn.call(
            first, tokens=tokens, seconds=2 + min(first[1], tokens) // 400, final=True
        )
        if result != "delivered":
            turn.local("deadline_exceeded", attempted=result != "deadline_before_dispatch")
    assert turn.finish is not None
    turn.useful = turn.finish.finish_reason == "stop"


def walk(turn, mode, *, reconnect=False, consent=False, trace=None, on_progress=None):
    # Fixed synthetic request shapes and token receipts per step, not a real LLM
    # transcript. A tool success here is an in-process lookup, no metered search.
    answer_quote = turn.candidates(final=True, ask=1200)[0][0]
    # Bound for this script: at most one fallback per round, and one synthesis.
    # Notice is an upper bound on permitted spend, not an additional hold.
    steps = turn.candidates(ask=1024)
    max_rounds = BASE.limits.max_tool_loop_iterations
    assert max_rounds is not None
    per_round = sum(e[0] for e in steps[: 2 if mode == "retry" else 1])
    if not turn.preflight(
        min(
            turn.book.period.remaining(MONTH),
            answer_quote + max_rounds * per_round,
        ),
        consent,
    ):
        return
    assert turn.protect(answer_quote)
    stop = None
    roots = {turn.key: turn}
    cursor = 0
    for i in range(max_rounds):
        choices = turn.candidates(ask=1024)
        if not choices:
            stop = "budget_exceeded"
            break
        entry = choices[0]
        if mode == "retry":
            assert turn.call(entry, tokens=0, seconds=2, rejected=True) == "429"
            if turn.shadow and turn.retries >= RETRIES:
                stop = "retry_limit"
                break
            turn.retries += 1
            entry = choices[1]  # ordinary fallback after a proven pre-output rejection
        seconds, observed, action = (
            trace[i]
            if trace is not None
            else (
                30 if mode == "slow" else 4,
                0 if mode == "stuck" else i + 1,
                0 if mode == "stuck" else i,
            )
        )
        outcome = turn.call(
            entry,
            tokens=160,
            seconds=seconds,
            reconnect=(lambda k: roots[k].observe()) if reconnect and i == 2 else None,
        )
        if outcome != "delivered":
            stop = "deadline_exceeded"
            break
        turn.rounds += 1
        # Same action, arguments AND unchanged result/cursor; changing evidence is progress.
        signature = ("lookup", action, observed)
        turn.seen[signature] = turn.seen.get(signature, 0) + 1
        if observed == cursor + 1:
            cursor = observed
            if on_progress is not None:
                on_progress(turn, cursor)
        if mode == "slow" and cursor == 3:
            break  # fixture has obtained all three required observations
        if turn.shadow and turn.seen[signature] >= REPEAT:
            stop = "no_progress"
            break
    else:
        stop = "tool_round_limit"
    # A tools-off final may genuinely finish a task. Here only the slow fixture
    # has the evidence to do that; all other syntheses report partial findings.
    final = turn.candidates(final=True, ask=1200)
    if not final:
        turn.finish = refused_finish("budget_exceeded", not_attempted=True)
        return
    result = turn.call(final[0], tokens=600, seconds=6, final=True)
    if result != "delivered":
        turn.local("deadline_exceeded", attempted=result != "deadline_before_dispatch")
    elif stop and turn.shadow:
        turn.local(stop)  # preserve synthesis provider stop, not a successful turn
    assert turn.finish is not None
    turn.useful = mode == "slow" and cursor == 3 and turn.finish.finish_reason == "stop"


SAMPLES = [
    ("short", MONTH, 400),
    ("short_low_balance", 10000, 400),
    ("cheap_long", MONTH, 8000),
    ("deliberate", 10000, 11600),
    ("unconfirmed", 10000, 11600),
    ("concurrent", 6000, 6350),
    ("empty", 0, 400),
    ("reserve", 1500, None),
    ("retry", MONTH, None),
    ("stuck", MONTH, None),
    ("rounds", MONTH, None),
    ("reconnect", MONTH, None),
    ("zero", 0, 600),
]


@pytest.mark.usefixtures("_attested")
def test_replay(production_policy):
    rows = []
    zero = _route(model="openrouter/fixture/zero", input_price=0, output_price=0)
    for name, balance, tokens in SAMPLES:
        for shadow in (False, True):
            turn = Turn(Book.with_balance(balance), shadow)
            if name == "zero":
                with patch.object(
                    rt,
                    "load_inference_policy",
                    return_value=replace(production_policy, routes={zero.route_id: zero}),
                ):
                    direct(turn, tokens, model=zero.model)
                assert turn.book.period.requests_in_window == turn.calls == 1
                assert turn.quotes == turn.settled == 0
            elif tokens is not None:
                direct(
                    turn,
                    tokens,
                    consent=name in {"deliberate", "concurrent"},
                    model=production_policy.routes[GLM].model if name == "cheap_long" else None,
                    competing=name == "concurrent",
                )
            else:
                walk(
                    turn,
                    "slow" if name == "reconnect" else name,
                    reconnect=name == "reconnect",
                    consent=name == "reserve",
                )
                if name == "reconnect":
                    uninterrupted = Turn(Book.with_balance(balance), shadow)
                    walk(uninterrupted, "slow")
                    assert uninterrupted.observe() == turn.observe()
            turn.seal()
            assert turn.finish is not None
            assert turn.settled == balance - turn.book.period.remaining(MONTH)
            if name == "deliberate" and shadow:
                assert turn.useful and turn.settled >= balance * 95 // 100
                assert turn.notices == [(0, turn.quotes, balance, True)]
            if name == "unconfirmed" and shadow:
                assert turn.notices and not turn.calls
            if name == "short_low_balance" and shadow:
                # Same prompt and preflight as the long unconfirmed case. The
                # short realized answer is not information available at admission.
                assert turn.notices == [(0, balance, balance, False)]
            if name == "cheap_long":
                assert turn.useful == shadow
            if name == "reserve":
                assert turn.finish.reason == "budget_exceeded"
                assert (turn.finish.provider.reason == "stop") == shadow
            row = (
                name,
                "candidate" if shadow else "baseline",
                int(turn.useful),
                int(turn.truncated),
                turn.quotes,
                turn.settled,
                turn.t,
                turn.calls,
                turn.finish.reason,
                turn.finish.outcome,
                turn.finish.provider.reason or turn.finish.provider.state,
                turn.finish.finish_reason or "-",
            )
            rows.append(" ".join(map(str, row)))
    expected = Path(__file__).with_name("fixtures") / "free_guardrail_shadow.txt"
    print(*rows, sep="\n")
    assert rows == [line for line in expected.read_text().splitlines() if not line.startswith("#")]


@pytest.mark.usefixtures("_attested")
def test_binding_and_protection(production_policy):
    for shadow in (False, True):
        t = Turn(Book.with_balance(MONTH), shadow)
        choices = t.candidates()
        assert [e[2].route_id for e in choices] == [LUNA, GLM, DS]
        assert {e[1] for e in choices} == {32000 - SIZE.estimate if shadow else 4096}
        assert {e[1] for e in t.candidates(size=rt.InputSize(90000, 30000))} == {2000}
        assert all(e[1] == 512 for e in t.candidates(ask=512))
        print("routes", shadow, SIZE, [(r.route_id, n, q) for q, n, r, _, _ in choices])
    for winner in ("a", "b"):
        book = Book.with_balance(6000)
        a, b = (Turn(book, True, key=k) for k in (winner, "b" if winner == "a" else "a"))
        assert a.protect(4000) and not b.protect(4000)
        assert book.period.open_reservations == 0 and book.period.remaining(MONTH) == 2000
        assert book.reserve(b, 2001) == "budget_exceeded"
        assert not book.reserve(a, 6000, draw=4000)
        assert book.period.reserved_microusd == 6000 and a.escrow == 0
        book.settle(a, 6000, 5999)
        assert book.period.remaining(MONTH) == 1
    book = Book.with_balance(0)
    a, b = Turn(book, True), Turn(book, True, key="b")
    assert not book.reserve(a, 0) and book.reserve(b, 0) == "concurrency_exceeded"
    book.settle(a, 0, 0)
    book.period = replace(book.period, requests_in_window=BASE.limits.requests_per_minute)
    assert book.reserve(b, 0) == "rate_limited"
    # A cheaper settlement/another root releasing funds doesn't enlarge consent.
    book = Book.with_balance(10000)
    a = Turn(book, True)
    assert a.preflight(4000, True) and a.protect(1000)
    assert book.reserve(a, 3001) == "permission_exceeded" and not book.reserve(a, 3000)
    book.settle(a, 3000, 2500)
    assert book.reserve(a, 1501, draw=1000) == "permission_exceeded"
    assert not book.reserve(a, 1500, draw=1000)
    book.settle(a, 1500, 1500)
    assert book.period.remaining(MONTH) == 6000 and a.unallocated() == 0
    longer = Turn(Book.with_balance(MONTH), True, deadline=120)
    walk(longer, "slow", reconnect=True)
    longer.seal()
    assert longer.useful and longer.t == 96
    print("deadline sensitivity, 120s:", longer.useful, longer.t, longer.quotes, longer.settled)
    expired = Turn(Book.with_balance(MONTH), True, t=DEADLINE)
    direct(expired, 400)
    assert expired.finish is not None and expired.finish.provider.state == "not_attempted"
    assert expired.calls == expired.settled == expired.book.period.requests_in_window == 0


@pytest.mark.usefixtures("_attested")
@pytest.mark.parametrize("shadow", [False, True])
@pytest.mark.parametrize("ask", [None, 2001], ids=["automatic-output", "exact-output"])
def test_exact_model_respects_account_context(production_policy, shadow, ask):
    turn = Turn(Book.with_balance(MONTH), shadow)
    size = rt.InputSize(bound=90000, estimate=30000)
    model = production_policy.routes[LUNA].model
    assert turn.candidates(size=size, model=model, ask=2000)[0][1] == 2000
    entries = turn.candidates(size=size, model=model, ask=ask)
    if ask is None:
        assert entries[0][1] == 2000
    else:
        assert not entries  # exact caller bounds aren't silently resized
