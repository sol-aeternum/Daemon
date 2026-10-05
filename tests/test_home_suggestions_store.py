"""Real MemoryStore SQL/encryption paths with a fictional transactional adapter.

This adapter verifies query predicates, short transaction boundaries, rollback
and merge behavior, but is not evidence for a running PostgreSQL lock scheduler.
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from orchestrator.home_suggestions.contracts import (
    EPOCH_KEY,
    MAX_CONVERSATIONS,
    MAX_EXCERPT_BYTES,
    MAX_MESSAGES,
    MAX_TOTAL_BYTES,
    PREFERENCE,
    bound_context,
    fingerprint,
    preference_state,
    render_context,
)
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.store import MemoryStore
from orchestrator.routes.users import SettingsUpdate


class Transaction:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        await self.pool.lock.acquire()
        self.previous = copy.deepcopy(
            (self.pool.settings, self.pool.destinations, self.pool.accepted)
        )
        self.pool.in_transaction = True

    async def __aexit__(self, kind, value, traceback):
        if kind is not None:
            self.pool.settings, self.pool.destinations, self.pool.accepted = self.previous
        self.pool.in_transaction = False
        self.pool.lock.release()


class Connection:
    def __init__(self, pool):
        self.pool = pool

    def transaction(self):
        return Transaction(self.pool)

    async def fetchrow(self, query, *args):
        self.pool.queries.append((query, args))
        assert "SELECT settings FROM users WHERE id = $1 FOR UPDATE" in query
        assert self.pool.in_transaction
        return {"settings": json.dumps(self.pool.settings)} if args[0] == self.pool.owner else None

    async def fetch(self, query, *args):
        self.pool.queries.append((query, args))
        assert self.pool.in_transaction
        if "FROM conversations c" in query:
            assert "c.user_id = $1 AND c.pipeline = 'cloud'" in query
            assert "m.user_id = $1 AND m.status = 'complete'" in query
            assert "LIMIT $2 FOR UPDATE OF c" in query
            owner, limit, title_limit = args
            assert limit == MAX_CONVERSATIONS
            if self.pool.bad_parent is not None:
                return [self.pool.bad_parent]
            selected = [
                conv
                for conv in self.pool.conversations
                if conv["user_id"] == owner
                and conv["pipeline"] == "cloud"
                and any(
                    msg["conversation_id"] == conv["id"]
                    and msg["user_id"] == owner
                    and msg["status"] == "complete"
                    and msg["role"] in {"user", "assistant"}
                    for msg in self.pool.messages
                )
            ]
            selected.sort(key=lambda conv: (-conv["activity"], str(conv["id"])))
            return [
                {
                    **conv,
                    "title": conv["title"] if len(conv["title"].encode()) <= title_limit else None,
                }
                for conv in selected[:limit]
            ]
        assert "FROM messages" in query and "LIMIT $3 FOR SHARE" in query
        assert "user_id = $2 AND status = 'complete'" in query
        assert "role IN ('user', 'assistant')" in query
        conv_id, owner, limit, ciphertext_limit = args
        assert limit == MAX_MESSAGES
        if self.pool.bad_message is not None:
            return [self.pool.bad_message]
        selected = [
            msg
            for msg in self.pool.messages
            if msg["conversation_id"] == conv_id
            and msg["user_id"] == owner
            and msg["status"] == "complete"
            and msg["role"] in {"user", "assistant"}
        ]
        selected.sort(key=lambda msg: (msg["created_at"], str(msg["id"])), reverse=True)
        return [
            {
                **msg,
                "content": msg["content"]
                if len(msg["content"].encode()) <= ciphertext_limit
                else None,
            }
            for msg in selected[:limit]
        ]

    async def execute(self, query, *args):
        self.pool.queries.append((query, args))
        assert self.pool.in_transaction
        if "UPDATE users SET settings" in query:
            self.pool.settings = json.loads(args[1])
        elif "INSERT INTO conversations" in query:
            assert "'{\"home_suggestion\":1}'::jsonb" in query
            self.pool.destinations.append(args)
        elif "INSERT INTO messages" in query:
            if self.pool.fail_message_insert:
                raise RuntimeError("fictional message persistence failure")
            self.pool.accepted.append(args)
        else:
            raise AssertionError(query)


class Acquire:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return Connection(self.pool)

    async def __aexit__(self, *args):
        return None


class Pool:
    def __init__(self):
        self.owner = uuid.uuid4()
        self.settings = {"preferences": {PREFERENCE: True, "personality": "default"}, EPOCH_KEY: 1}
        self.encryption = ContentEncryption(Fernet.generate_key().decode())
        self.conversations: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.destinations: list[tuple[Any, ...]] = []
        self.accepted: list[tuple[Any, ...]] = []
        self.queries: list[tuple[str, tuple[Any, ...]]] = []
        self.lock = asyncio.Lock()
        self.in_transaction = False
        self.fail_message_insert = False
        self.bad_parent: dict[str, Any] | None = None
        self.bad_message: dict[str, Any] | None = None

    def acquire(self):
        return Acquire(self)

    def conversation(
        self, *, owner=None, pipeline: str | None = "cloud", title="Fictional source", activity=1
    ):
        row = {
            "id": uuid.uuid4(),
            "user_id": owner or self.owner,
            "pipeline": pipeline,
            "title": title,
            "activity": activity,
        }
        self.conversations.append(row)
        return row

    def message(
        self,
        conv,
        *,
        owner=None,
        role="user",
        status="complete",
        text="Fictional private notes",
        created_at=1,
    ):
        row = {
            "id": uuid.uuid4(),
            "conversation_id": conv["id"],
            "user_id": owner or conv["user_id"],
            "role": role,
            "status": status,
            "content": self.encryption.encrypt(text),
            "created_at": created_at,
        }
        self.messages.append(row)
        return row

    def store(self):
        return MemoryStore(self, self.encryption)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_exact_owner_cloud_complete_bounded_stable_selection():
    pool = Pool()
    for index in range(10):
        conv = pool.conversation(activity=index)
        for message_index in range(8):
            pool.message(conv, created_at=message_index)
    foreign = pool.conversation(owner=uuid.uuid4(), activity=99)
    pool.message(foreign)
    for pipeline in ("local", None, "unknown"):
        conv = pool.conversation(pipeline=pipeline, activity=100)
        pool.message(conv)
    empty = pool.conversation(activity=101)
    pool.message(empty, owner=uuid.uuid4())
    pool.message(empty, status="streaming")
    pool.message(empty, status="error")
    pool.message(empty, role="system")
    store = pool.store()
    enabled, epoch, sources = await store.home_suggestion_snapshot(pool.owner)
    assert enabled and epoch == 1 and len(sources) == 6
    assert sources == (await store.home_suggestion_snapshot(pool.owner))[2]
    assert sources == sorted(sources, key=lambda source: source["conversation_id"])
    assert all(
        source["pipeline"] == "cloud" and source["user_id"] == str(pool.owner) for source in sources
    )
    assert all(len(source["messages"]) == 4 for source in sources)
    selected_ids = {source["conversation_id"] for source in sources}
    assert selected_ids == {str(conv["id"]) for conv in pool.conversations[4:10]}
    assert not pool.in_transaction


@pytest.mark.asyncio
async def test_full_content_not_timestamp_revision_and_aggregate_byte_caps():
    pool = Pool()
    for index in range(6):
        conv = pool.conversation(activity=index)
        for message_index in range(4):
            pool.message(conv, text="é" * 3000, created_at=message_index)
    store = pool.store()
    sources = (await store.home_suggestion_snapshot(pool.owner))[2]
    assert (
        sum(len(msg["content"].encode()) for source in sources for msg in source["messages"])
        <= MAX_TOTAL_BYTES
    )
    assert all(
        len(msg["content"].encode()) <= MAX_EXCERPT_BYTES
        for source in sources
        for msg in source["messages"]
    )
    # A mutation outside the displayed excerpt changes the full-content hash
    # despite unchanged id/status/created_at and identical excerpt text.
    source = sources[0]
    msg_id = source["messages"][0]["id"]
    row = next(msg for msg in pool.messages if str(msg["id"]) == msg_id)
    row["content"] = pool.encryption.encrypt("é" * 2999 + "a")
    changed = (await store.home_suggestion_snapshot(pool.owner))[2]
    assert fingerprint(changed) != fingerprint(sources)
    matching = next(
        item for item in changed if item["conversation_id"] == source["conversation_id"]
    )
    assert matching["messages"][0]["content"] == source["messages"][0]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [
        "parent_owner",
        "local",
        "unknown",
        "message_owner",
        "message_status",
        "message_role",
        "oversized",
        "ciphertext",
        "title",
    ],
)
async def test_untrusted_or_unreadable_rows_fail_closed(fault):
    pool = Pool()
    conv = pool.conversation()
    msg = pool.message(conv)
    if fault in {"parent_owner", "local", "unknown"}:
        pool.bad_parent = (
            {**conv, "user_id": uuid.uuid4()}
            if fault == "parent_owner"
            else {**conv, "pipeline": "local" if fault == "local" else None}
        )
    elif fault == "message_owner":
        pool.bad_message = {**msg, "user_id": uuid.uuid4()}
    elif fault == "message_status":
        pool.bad_message = {**msg, "status": "streaming"}
    elif fault == "message_role":
        pool.bad_message = {**msg, "role": "system"}
    elif fault == "oversized":
        msg["content"] = pool.encryption.encrypt("x" * 20000)
    elif fault == "ciphertext":
        msg["content"] = "corrupt ciphertext"
    else:
        conv["title"] = "x" * 1025
    with pytest.raises(Exception):
        await pool.store().home_suggestion_snapshot(pool.owner)
    assert not pool.in_transaction


@pytest.mark.asyncio
async def test_atomic_binding_encrypted_and_public_history_survives_cache_expiry():
    pool = Pool()
    conv = pool.conversation()
    pool.message(conv)
    store = pool.store()
    _, epoch, sources = await store.home_suggestion_snapshot(pool.owner)
    context = bound_context({"id": "a" * 32, "source_index": 0}, sources)
    destination = await store.bind_home_suggestion(
        pool.owner,
        epoch=epoch,
        expected_fingerprint=fingerprint(sources),
        prompt="Prepare the fictional plan.",
        context=context,
    )
    assert destination == pool.destinations[0][0]
    _, owner, encrypted_prompt, metadata = pool.accepted[0]
    assert owner == pool.owner and "fictional plan" not in encrypted_prompt
    assert "Fictional private notes" not in metadata and "Fictional source" not in metadata
    history = {
        "role": "user",
        "content": pool.encryption.decrypt(encrypted_prompt),
        "metadata": metadata,
    }
    store._decrypt_message_tool_traces(history)
    assert history["content"] == "Prepare the fictional plan."
    assert history["metadata"]["home_suggestion"] == context
    assert "Fictional private notes" in render_context(
        history["content"], history["metadata"]["home_suggestion"]
    )
    # All data needed after cache expiry is in this encrypted persisted turn;
    # its public shape is the approved separately inspectable context envelope.
    assert set(history["metadata"]["home_suggestion"]) == {"version", "suggestion_id", "sources"}
    assert set(context["sources"][0]["messages"][0]) == {"id", "role", "content"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["edit", "delete", "optout", "epoch", "context", "persist"])
async def test_acceptance_locks_revalidate_and_roll_back_entire_destination(failure):
    pool = Pool()
    conv = pool.conversation()
    message = pool.message(conv)
    store = pool.store()
    _, epoch, sources = await store.home_suggestion_snapshot(pool.owner)
    context = bound_context({"id": "a" * 32, "source_index": 0}, sources)
    if failure == "edit":
        message["content"] = pool.encryption.encrypt("Edited without timestamps")
    elif failure == "delete":
        pool.conversations.clear()
    elif failure == "optout":
        pool.settings["preferences"][PREFERENCE] = False
    elif failure == "epoch":
        pool.settings[EPOCH_KEY] += 1
    elif failure == "context":
        context["sources"][0]["messages"][0]["content"] = "Client substituted text"
    else:
        pool.fail_message_insert = True
    with pytest.raises(Exception):
        await store.bind_home_suggestion(
            pool.owner,
            epoch=epoch,
            expected_fingerprint=fingerprint(sources),
            prompt="Prepare the fictional plan.",
            context=context,
        )
    assert not pool.destinations and not pool.accepted and not pool.in_transaction


@pytest.mark.asyncio
async def test_atomic_preferences_cannot_restore_optin_from_stale_other_update():
    pool = Pool()
    store = pool.store()
    await asyncio.gather(
        store.merge_user_preferences(pool.owner, {PREFERENCE: False}),
        store.merge_user_preferences(pool.owner, {"personality": "concise"}),
        store.merge_user_preferences(pool.owner, {"characteristics": {"warmth": "high"}}),
    )
    assert preference_state(pool.settings) == (False, 2)
    assert pool.settings["preferences"]["personality"] == "concise"
    assert pool.settings["preferences"]["characteristics"] == {"warmth": "high"}
    # Older whole-preferences snapshots are refused instead of implicitly
    # turning the opt-in back on while saving an unrelated setting.
    with pytest.raises(ValueError):
        await store.merge_user_preferences(pool.owner, {PREFERENCE: True, "personality": "default"})
    assert preference_state(pool.settings) == (False, 2)
    await store.merge_user_preferences(pool.owner, {PREFERENCE: True})
    assert preference_state(pool.settings) == (True, 3)


@pytest.mark.parametrize("value", ["true", "false", 0, 1, None, {}, []])
def test_optin_requires_strict_boolean(value):
    with pytest.raises(ValidationError):
        SettingsUpdate(preferences={PREFERENCE: value})


def test_mixed_preference_patch_cannot_restore_stale_optin():
    with pytest.raises(ValidationError):
        SettingsUpdate(preferences={PREFERENCE: True, "personality": "concise"})
    assert SettingsUpdate(preferences={PREFERENCE: True}).preferences == {PREFERENCE: True}
    assert preference_state({}) == (False, 0)


def test_plaintext_generic_metadata_is_encrypted_on_write_and_rejected_on_read():
    pool = Pool()
    store = pool.store()
    context = {
        "version": 1,
        "suggestion_id": "a" * 32,
        "sources": [
            {
                "conversation_id": str(uuid.uuid4()),
                "title": "Fictional title",
                "messages": [
                    {"id": str(uuid.uuid4()), "role": "user", "content": "Fictional private source"}
                ],
            }
        ],
    }
    encrypted = store._encrypt_message_metadata({"home_suggestion": context})
    assert "Fictional private source" not in encrypted and "Fictional title" not in encrypted
    message: dict[str, Any] = {"metadata": encrypted}
    store._decrypt_message_tool_traces(message)
    assert message["metadata"]["home_suggestion"] == context
    with pytest.raises(ValueError):
        store._decrypt_message_tool_traces({"metadata": {"home_suggestion": context}})
    invalid = json.loads(encrypted)
    invalid["home_suggestion"]["ciphertext"] = "corrupt"
    with pytest.raises(Exception):
        store._decrypt_message_tool_traces({"metadata": invalid})
