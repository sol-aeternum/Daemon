"""Advisor call budget tracking helpers.

Provides budget checking and atomic increment for per-conversation advisor call limits.
Used by the consult_advisor handler to enforce ADVISOR_BUDGET_PER_CONVERSATION limits.

Only calls that actually begin model execution count against the budget.
Validation failures, timeouts before model start, and registry construction failures
do not increment the counter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orchestrator.config import get_settings

if TYPE_CHECKING:
    from orchestrator.memory.store import MemoryStore


@dataclass
class BudgetCheckResult:
    """Result of a budget check operation."""

    allowed: bool
    current_count: int
    budget: int
    message: str


async def check_advisor_budget(
    conversation_id: uuid.UUID,
    store: "MemoryStore",
) -> BudgetCheckResult:
    """Check if an advisor call is allowed under the current budget.

    Args:
        conversation_id: The conversation to check budget for.
        store: MemoryStore instance for database access.

    Returns:
        BudgetCheckResult with allowed status and diagnostic info.
    """
    settings = get_settings()
    budget = settings.advisor_budget_per_conversation

    current_count = await store.get_advisor_call_count(conversation_id)

    if current_count >= budget:
        return BudgetCheckResult(
            allowed=False,
            current_count=current_count,
            budget=budget,
            message=f"Advisor budget exhausted: {current_count}/{budget} calls used in this conversation.",
        )

    return BudgetCheckResult(
        allowed=True,
        current_count=current_count,
        budget=budget,
        message="",
    )


async def increment_advisor_budget(
    conversation_id: uuid.UUID,
    store: "MemoryStore",
) -> int:
    """Atomically increment advisor call count after model execution begins.

    Should be called ONLY after the advisor model call has actually started,
    not on validation failures or pre-execution errors.

    Args:
        conversation_id: The conversation to increment budget for.
        store: MemoryStore instance for database access.

    Returns:
        The new advisor call count after incrementing.
    """
    return await store.increment_advisor_call_count(conversation_id)
