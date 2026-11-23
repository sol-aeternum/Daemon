"""Paired, provider-free 2x2 replay against #786. No new admission authority.

Lock both policies before running the second, separately reported scenario set.
The cheap option fits a *money* quote, not a target number of tokens. The time
option grants a single extension after two verified sequential source pages;
an action, a novel string, a retry or attaching a client isn't progress.
"""

from dataclasses import replace
from functools import partial
from unittest.mock import patch

import pytest

from orchestrator import compute_runtime as rt
from tests.test_compute_runtime import _route
from tests.test_free_guardrail_shadow import (
    Book,
    DEADLINE,
    GLM,
    MONTH,
    SAMPLES,
    Turn,
    direct,
    walk,
    _attested as _attested,
    production_policy as production_policy,
)


def economical_offer(turn, full, model, consent):
    available = turn.book.period.remaining(MONTH)
    if not full[0] or full[0] * 2 < available:
        return full
    # Compare on the originally selected route, before seeing any response.
    # Never silently substitute a cheaper model. No viable routine floor?
    # Return the original quote to the normal pre-dispatch checkpoint.
    entries = turn.candidates(final=True, model=model, offer_budget=available // 4)
    small = next((entry for entry in entries if entry[2].route_id == full[2].route_id), None)
    turn.offers.append((turn.calls, full[0], small[0] if small else None))
    return full if consent or small is None else small


def extend_once(turn, advance_streak):
    if advance_streak == 2 and turn.deadline == DEADLINE:
        turn.deadline = DEADLINE + 30


# Additional synthetic holdout: different balances/lengths, too little for
# the routine floor, credible progress that still cannot finish, and changing
# actions that return no new evidence. Do not combine its totals with #786.
HOLDOUT = [
    ("modest", 8000, 750),
    ("long_without_consent", 12000, 8000),
    ("thin_balance", 1800, 400),
    ("deliberate", 16000, 18000),
    ("new_pages", MONTH, None),
    ("too_slow", MONTH, None),
    ("cosmetic_change", MONTH, None),
]
# Each tuple is virtual seconds, contiguous result cursor, action ID.
TRACES = {
    "new_pages": [(32, 1, 1), (33, 2, 2), (34, 3, 3)],
    "too_slow": [(36, 1, 1), (38, 2, 2), (44, 3, 3)],
    "cosmetic_change": [(28, 0, 1), (28, 0, 2), (28, 0, 3), (28, 0, 4)],
}


def replay(policy, case, *, shadow, offer, progress):
    name, balance, tokens = case
    t = Turn(Book.with_balance(balance), shadow)
    if tokens is not None:
        answer = partial(
            direct,
            t,
            tokens,
            consent=name in {"deliberate", "concurrent"},
            competing=name == "concurrent",
            choose_offer=economical_offer if offer else None,
        )
        if name == "zero":
            zero = _route(model="openrouter/fixture/zero", input_price=0, output_price=0)
            with patch.object(
                rt,
                "load_inference_policy",
                return_value=replace(policy, routes={zero.route_id: zero}),
            ):
                answer(model=zero.model)
        else:
            answer(model=policy.routes[GLM].model if name == "cheap_long" else None)
    else:
        slow = name == "reconnect" or name in TRACES
        kwargs = dict(
            consent=name == "reserve",
            trace=TRACES.get(name),
            on_progress=extend_once if progress else None,
        )
        walk(t, "slow" if slow else name, reconnect=slow, **kwargs)
        if slow:
            other = Turn(Book.with_balance(balance), shadow)
            walk(other, "slow", **kwargs)
            assert other.observe() == t.observe()
    t.seal()
    assert t.finish is not None
    if shadow:
        assert t.t <= t.deadline <= DEADLINE + 30
        if name in {"cosmetic_change", "retry", "stuck"}:
            assert t.deadline == DEADLINE
        if name in {"too_slow", "cosmetic_change"}:
            assert t.finish.reason == "deadline_exceeded" and t.finish.finish_reason is None
        if name in {"new_pages", "reconnect"}:
            assert t.useful == progress
        if name in {"short_low_balance", "modest"}:
            assert t.useful == offer
            if offer:
                assert t.offers == [(0, balance, t.quotes)]
        if name == "thin_balance":
            assert t.calls == 0
        if name == "deliberate":
            assert t.useful and t.notices[0] == (0, balance, balance, True)
        if name in {"unconfirmed", "long_without_consent"}:
            assert t.truncated == offer and not t.useful
            if offer:
                assert t.calls == 1 and t.quotes <= balance // 4
    return (int(t.useful), int(t.truncated), t.quotes, t.settled, t.t, t.calls)


@pytest.mark.usefixtures("_attested")
@pytest.mark.parametrize("interruption", [1, 0, 3], ids=["unchanged", "regression", "gap"])
def test_interruption_breaks_advance_streak(production_policy, interruption):
    # All action IDs differ. Page 1 remains verified after the interruption;
    # returning page 2 is a single fresh advance, not authority for more time.
    trace = [(20, 1, 1), (20, interruption, 2), (20, 2, 3), (30, 3, 4)]
    turn = Turn(Book.with_balance(MONTH), True)
    walk(turn, "slow", trace=trace, reconnect=True, on_progress=extend_once)
    turn.seal()
    assert turn.deadline == DEADLINE
    assert turn.rounds == 3 and turn.finish is not None
    assert turn.finish.reason == "deadline_exceeded" and turn.finish.finish_reason is None


@pytest.mark.usefixtures("_attested")
@pytest.mark.parametrize("cases", [SAMPLES, HOLDOUT], ids=["original", "heldout"])
def test_comparison(production_policy, cases):
    for label, shadow, offer, progress in [
        ("baseline", False, False, False),
        ("v1", True, False, False),
        ("offer", True, True, False),
        ("progress", True, False, True),
        ("both", True, True, True),
    ]:
        rows = [
            replay(production_policy, c, shadow=shadow, offer=offer, progress=progress)
            for c in cases
        ]
        totals = tuple(map(sum, zip(*rows, strict=True)))
        print(label, len(rows), *totals, "uUSD/useful", round(totals[3] / totals[0], 1))
