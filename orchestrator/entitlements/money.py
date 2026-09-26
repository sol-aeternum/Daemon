"""Internal money unit for commercial budgets.

All internal budgets and ceilings are integer **microusd**: 1 USD = 1_000_000
microusd. Integers only, so no float rounding can erode a ceiling.

Displayed prices are *configured* in the display currency's minor units (for
example AUD cents) in ``config/commercial.json``. They are never derived from
internal budgets, so this module performs no FX conversion and cannot invent an
exchange rate.
"""

from __future__ import annotations

from typing import Final

MICRO_USD_PER_USD: Final[int] = 1_000_000

__all__ = ["MICRO_USD_PER_USD", "Microusd", "require_microusd", "microusd_to_usd_str"]

#: A validated internal budget amount in microusd.
Microusd = int


def require_microusd(value: object, *, field: str) -> Microusd:
    """Return ``value`` as a non-negative microusd amount or raise ``ValueError``.

    ``bool`` is rejected explicitly: it is an ``int`` subclass and silently
    accepting it would turn a config typo into a 0-or-1 budget.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer microusd amount, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{field} must not be negative, got {value}")
    return value


def require_positive_microusd(value: object, *, field: str) -> Microusd:
    """Like :func:`require_microusd` but also rejects zero."""
    amount = require_microusd(value, field=field)
    if amount == 0:
        raise ValueError(f"{field} must be greater than zero")
    return amount


def microusd_to_usd_str(amount: Microusd) -> str:
    """Format microusd for operator-facing log lines (never for public APIs)."""
    require_microusd(amount, field="amount")
    return f"{amount // MICRO_USD_PER_USD}.{amount % MICRO_USD_PER_USD:06d} USD"
