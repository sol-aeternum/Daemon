"""Synthetic source adapter for worker algorithm tests, NOT SQL qualification.

Old unit fixtures used plaintext job snapshots and label IDs. Production now
requires a captured origin and database inputs. This driver stages those fixture
rows at the mocked source boundary; legacy/refusal tests call the real job instead.
Real ownership/locks/commit evidence is in test_fact_account_guard_postgres.py.
"""

import json
from types import SimpleNamespace
from uuid import UUID, NAMESPACE_URL, uuid5
from unittest.mock import AsyncMock, patch

from orchestrator.memory import extraction_fencing as fencing
from orchestrator.worker import jobs


def install_inputs(monkeypatch, store, user_id):
    original_uuid = jobs._as_uuid

    def label_uuid(value):
        try:
            return original_uuid(value)
        except ValueError:
            return uuid5(NAMESPACE_URL, str(value))

    monkeypatch.setattr(jobs, "_as_uuid", label_uuid)
    if "read_summary_continuation_pending" not in store.__dict__:
        store.read_summary_continuation_pending = AsyncMock(return_value=False)

    async def read_page(current, origin, cid):
        assert current is store and origin.account_id == user_id
        snapshot = SimpleNamespace(origin=origin, conversation=SimpleNamespace(id=cid))
        snapshot.subset = lambda ids: snapshot
        rows = getattr(store, "_unit_source_rows", None)
        if rows is None:
            reader = store.__dict__.get("get_messages_after_cursor")
            rows = await reader(cid, created_at=None, message_id=None, limit=250) if reader else []
        return snapshot, [dict(row) for row in rows]

    async def checkpoint(current, inputs, **kwargs):
        return await current.log_extraction(**kwargs)

    async def with_summary(current, inputs):
        return inputs, None

    monkeypatch.setattr(fencing, "read_page", read_page)
    monkeypatch.setattr(fencing, "check", AsyncMock())
    monkeypatch.setattr(fencing, "checkpoint", checkpoint)
    monkeypatch.setattr(fencing, "with_summary", with_summary)


async def extract_memories(ctx, user_id, conversation_id, messages_json=None, **kwargs):
    store = ctx["store"]
    if messages_json is not None:
        store._unit_source_rows = (
            json.loads(messages_json) if isinstance(messages_json, str) else messages_json
        )
        for index, row in enumerate(store._unit_source_rows):
            row.setdefault("id", str(UUID(int=index + 1)))
    kwargs.setdefault("origin_account_id", str(user_id))
    kwargs.setdefault("origin_account_generation", 7)
    if messages_json is not None and ctx.get("redis") is None:
        # Direct algorithm fixtures historically exhausted their input inline;
        # bounded queued continuation is covered by the queue-equipped cases.
        with patch.object(jobs, "MAX_EXTRACTION_CHUNKS_PER_JOB", 10000):
            return await jobs.extract_memories(ctx, user_id, conversation_id, **kwargs)
    return await jobs.extract_memories(ctx, user_id, conversation_id, **kwargs)
