from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from orchestrator.memory.store import MemoryStore
from orchestrator.memory.trust import (
    TRUST_BOOST_AMOUNT,
    TRUST_CEILING,
    TRUST_FLOOR,
    TRUST_PENALTY_AMOUNT,
    boost_trust,
    penalize_trust,
)


@pytest.mark.asyncio
async def test_boost_trust_parameterizes_score_bounds() -> None:
    memory_ids = [uuid.uuid4(), uuid.uuid4()]
    fetch = AsyncMock(return_value=[{"id": memory_id} for memory_id in memory_ids])
    store = cast(MemoryStore, SimpleNamespace(_pool=SimpleNamespace(fetch=fetch)))

    updated = await boost_trust(memory_ids, store)

    assert updated == 2
    await_args = fetch.await_args
    assert await_args is not None
    query, ids_param, boost_param, ceiling_param = await_args.args
    assert "trust_score + $2" in query
    assert "trust_score < $3" in query
    assert ids_param == memory_ids
    assert boost_param == TRUST_BOOST_AMOUNT
    assert ceiling_param == TRUST_CEILING


@pytest.mark.asyncio
async def test_penalize_trust_parameterizes_score_bounds() -> None:
    memory_ids = [uuid.uuid4()]
    fetch = AsyncMock(return_value=[{"id": memory_ids[0]}])
    store = cast(MemoryStore, SimpleNamespace(_pool=SimpleNamespace(fetch=fetch)))

    updated = await penalize_trust(memory_ids, store)

    assert updated == 1
    await_args = fetch.await_args
    assert await_args is not None
    query, ids_param, penalty_param, floor_param = await_args.args
    assert "trust_score - $2" in query
    assert "trust_score > $3" in query
    assert ids_param == memory_ids
    assert penalty_param == TRUST_PENALTY_AMOUNT
    assert floor_param == TRUST_FLOOR
