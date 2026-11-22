"""render_block / read_block chat block tools (docs/ARTIFACT_PRESENTATION_DESIGN.md)."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

import pytest

from orchestrator.tasks.fence import is_material
from orchestrator.tools.blocks import (
    MAX_BLOCK_BYTES,
    MAX_BLOCKS_PER_TURN,
    MAX_INLINE_BYTES,
    ReadBlockTool,
    RenderBlockTool,
    block_history_note,
    block_tools,
    iter_persisted_blocks,
)
from orchestrator.tools.builtin import create_default_registry

USER = uuid.uuid4()
CONVERSATION = uuid.uuid4()
PAGE = "<!doctype html><h1>Sales</h1><script>document.title='x'</script>"


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def _persisted_message(content: str, block_id: str, version: int = 1, **extra: Any):
    data = {
        "block_id": block_id,
        "version": version,
        "kind": "html",
        "title": "Sales dashboard",
        "display": "inline",
        "bytes": len(content),
        "sha256": _digest(content),
        **extra,
    }
    return {
        "role": "assistant",
        "content": "Here it is.",
        "tool_calls": [
            {
                "name": "render_block",
                "arguments": {"kind": "html", "title": "Sales dashboard", "content": content},
            }
        ],
        "tool_results": [{"name": "render_block", "result": {"success": True, "data": data}}],
    }


def _tools(messages: list[dict[str, Any]] | None = None):
    """Tools over the blocks in a turn's accepted history rows."""
    render, read = block_tools(list(iter_persisted_blocks(messages or [])))
    return render, read


async def _call(tool: Any, **kwargs: Any) -> dict[str, Any]:
    return json.loads(await tool.execute(**kwargs))


@pytest.mark.asyncio
async def test_render_returns_compact_reference_without_echoing_content() -> None:
    render, _ = _tools()
    result = await _call(render, kind="html", title="  Sales\n dashboard ", content=PAGE)
    assert result["success"] is True
    data = result["data"]
    assert data["block_id"].startswith("blk_") and len(data["block_id"]) == 20
    assert data["version"] == 1
    assert data["title"] == "Sales dashboard"
    assert data["display"] == "panel"
    assert data["bytes"] == len(PAGE.encode())
    assert data["sha256"] == _digest(PAGE)
    assert "content" not in data
    assert PAGE not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "content", "display", "expected"),
    [
        # html is never inline while E1 execution is held off (design D4/D6).
        ("html", PAGE, "auto", "panel"),
        ("html", PAGE, "inline", "panel"),
        ("svg", "<svg/>", "inline", "inline"),
        ("svg", "<svg/>", "panel", "panel"),
        ("svg", "<svg>" + "x" * MAX_INLINE_BYTES + "</svg>", "inline", "panel"),
        ("markdown", "# Report", "inline", "panel"),
        ("code", "print(1)", "auto", "panel"),
    ],
)
async def test_placement_is_enforced_server_side(
    kind: str, content: str, display: str, expected: str
) -> None:
    render, _ = _tools()
    result = await _call(render, kind=kind, title="Block", content=content, display=display)
    assert result["data"]["display"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"kind": "exe", "title": "x", "content": "x"}, "invalid_kind"),
        ({"kind": "html", "title": "", "content": "x"}, "invalid_content"),
        ({"kind": "html", "title": "t" * 121, "content": "x"}, "invalid_content"),
        ({"kind": "html", "title": "x", "content": "   "}, "invalid_content"),
        ({"kind": "html", "title": "x", "content": "a\x00b"}, "invalid_content"),
        ({"kind": "html", "title": "x", "content": 42}, "invalid_content"),
        ({"kind": "html", "title": "x", "content": "\ud800"}, "invalid_content"),
        ({"kind": "json", "title": "x", "content": "{nope"}, "invalid_content"),
        ({"kind": "html", "title": "x", "content": "x" * (MAX_BLOCK_BYTES + 1)}, "too_large"),
    ],
)
async def test_invalid_input_fails_closed(kwargs: dict[str, Any], error: str) -> None:
    render, _ = _tools()
    assert await _call(render, **kwargs) == {"success": False, "error": error}


@pytest.mark.asyncio
async def test_csv_and_code_language_validation() -> None:
    render, read = _tools()
    ok = await _call(render, kind="csv", title="Rows", content="a,b\n1,2\n")
    assert ok["success"] is True
    code = await _call(render, kind="code", title="Script", content="print(1)", language="Python")
    assert code["data"]["language"] == "python"
    source = await _call(read, block_id=code["data"]["block_id"])
    assert source["data"]["language"] == "python"
    odd = await _call(render, kind="code", title="Odd", content="x", language="<script>")
    assert "language" not in odd["data"]
    assert "language" not in (await _call(read, block_id=odd["data"]["block_id"]))["data"]


@pytest.mark.asyncio
async def test_turn_limit() -> None:
    render, _ = _tools()
    for index in range(MAX_BLOCKS_PER_TURN):
        result = await _call(render, kind="markdown", title=f"Doc {index}", content="# x")
        assert result["success"] is True
    assert await _call(render, kind="markdown", title="One more", content="# x") == {
        "success": False,
        "error": "turn_limit",
    }


@pytest.mark.asyncio
async def test_every_render_is_a_new_block_even_if_revises_is_passed() -> None:
    """Revisions are deferred: a stray ``revises`` never reuses an identity."""
    assert "revises" not in RenderBlockTool.parameters["properties"]
    assert "version" not in ReadBlockTool.parameters["properties"]
    render, read = _tools()
    first = (await _call(render, kind="html", title="Chart", content="<p>v1</p>"))["data"]
    second = (
        await _call(
            render, kind="html", title="Chart", content="<p>v2</p>", revises=first["block_id"]
        )
    )["data"]
    assert second["block_id"] != first["block_id"]
    assert first["version"] == second["version"] == 1
    assert (await _call(read, block_id=first["block_id"]))["data"]["content"] == "<p>v1</p>"
    assert (await _call(read, block_id=second["block_id"]))["data"]["content"] == "<p>v2</p>"


@pytest.mark.asyncio
async def test_read_of_persisted_block() -> None:
    block_id = "blk_" + "a" * 16
    _, read = _tools([_persisted_message(PAGE, block_id)])
    source = await _call(read, block_id=block_id)
    assert source["data"] == {
        "block_id": block_id,
        "version": 1,
        "kind": "html",
        "title": "Sales dashboard",
        "content": PAGE,
    }


@pytest.mark.asyncio
async def test_overlapping_turns_never_share_a_block_identity() -> None:
    """Two turns that accepted the same history each publish a distinct block."""
    block_id = "blk_" + "a" * 16
    history = [_persisted_message(PAGE, block_id)]
    outputs = []
    for content in ("<p>red</p>", "<p>blue</p>"):
        render, _ = _tools(history)
        data = (await _call(render, kind="html", title="Sales", content=content, revises=block_id))[
            "data"
        ]
        message = _persisted_message(content, data["block_id"])
        outputs.append(message)
    persisted = list(iter_persisted_blocks([*history, *outputs]))
    identities = [(block.block_id, block.version) for block in persisted]
    assert len(set(identities)) == len(identities) == 3
    assert {block.content for block in persisted} == {PAGE, "<p>red</p>", "<p>blue</p>"}


@pytest.mark.asyncio
async def test_unpaired_results_and_missing_history_are_not_found() -> None:
    block_id = "blk_" + "b" * 16
    # Without the turn's history rows, reads are withheld.
    _, read = _tools()
    assert await _call(read, block_id=block_id) == {"success": False, "error": "not_found"}

    forged = _persisted_message(PAGE, block_id, sha256=_digest("something else"))
    _, read = _tools([forged])
    assert await _call(read, block_id=block_id) == {"success": False, "error": "not_found"}

    _, read = _tools([_persisted_message(PAGE, block_id)])
    for bad in ({"block_id": "../x"}, {"block_id": 42}):
        assert await _call(read, **bad) == {"success": False, "error": "not_found"}


def test_persisted_blocks_ignore_user_and_failed_entries() -> None:
    block_id = "blk_" + "c" * 16
    good = _persisted_message(PAGE, block_id)
    user = {**good, "role": "user"}
    failed = {
        **good,
        "tool_results": [{"name": "render_block", "result": {"success": False, "error": "x"}}],
    }
    as_string = {
        **good,
        "tool_results": [
            {"name": "render_block", "result": json.dumps(good["tool_results"][0]["result"])}
        ],
    }
    blocks = list(iter_persisted_blocks([user, failed, good, as_string]))
    assert [(block.block_id, block.version) for block in blocks] == [(block_id, 1), (block_id, 1)]


def test_history_note_names_blocks_for_the_model() -> None:
    block_id = "blk_" + "d" * 16
    message = _persisted_message(PAGE, block_id, title='Say "hi"\nnow')
    note = block_history_note(message["tool_results"])
    assert note is not None
    assert f"[block {block_id} v1 html \"Say 'hi' now\"]" in note
    assert "read_block" in note
    assert PAGE not in note
    assert block_history_note([{"name": "web_search", "result": {"success": True}}]) is None
    assert block_history_note(None) is None


def test_tools_are_registered_and_unfenced() -> None:
    registry = create_default_registry(user_id=USER, conversation_id=CONVERSATION)
    assert "render_block" in registry
    assert "read_block" in registry
    assert not is_material("render_block")
    assert not is_material("read_block")


@pytest.mark.asyncio
async def test_durable_task_history_also_names_blocks() -> None:
    from types import SimpleNamespace

    from orchestrator.tasks.runner import _history

    block_id = "blk_" + "f" * 16
    accepted = uuid.uuid4()
    rows = [
        {"id": uuid.uuid4(), "role": "user", "content": "Make a chart"},
        {"id": uuid.uuid4(), **_persisted_message(PAGE, block_id)},
        {"id": accepted, "role": "user", "content": "Bigger font"},
    ]

    class Store:
        async def get_recent_messages(self, conversation_id: uuid.UUID, **kwargs: Any):
            return rows

    claim = SimpleNamespace(task_input={}, conversation_id=CONVERSATION, user_message_id=accepted)
    history = await _history(Store(), claim, 20, None)  # type: ignore[arg-type]
    assert history[1]["content"].startswith("Here it is.")
    assert f"[block {block_id} v1 html" in history[1]["content"]
    assert history[2]["content"] == "Bigger font"


@pytest.mark.asyncio
async def test_durable_turn_sees_only_blocks_in_its_accepted_history() -> None:
    """A block written after acceptance is not readable by the accepted turn."""
    from types import SimpleNamespace

    from orchestrator.tasks.runner import _history
    from orchestrator.tools.blocks import BlockVersion

    block_id = "blk_" + "d" * 16
    accepted = uuid.uuid4()
    later_id = "blk_" + "9" * 16
    later = _persisted_message("<p>v2</p>", later_id)
    rows = [
        {"id": uuid.uuid4(), **_persisted_message(PAGE, block_id)},
        {"id": accepted, "role": "user", "content": "Make it blue"},
        # A request-bound turn wrote a block after this task was accepted.
        {"id": uuid.uuid4(), **later},
    ]
    seen: dict[str, Any] = {}

    class Store:
        async def get_recent_messages(self, conversation_id: uuid.UUID, **kwargs: Any):
            seen.update(kwargs)
            return rows

    claim = SimpleNamespace(task_input={}, conversation_id=CONVERSATION, user_message_id=accepted)
    blocks: list[BlockVersion] = []
    await _history(Store(), claim, 20, None, blocks)  # type: ignore[arg-type]
    assert seen["until_message_id"] == accepted
    assert [(block.block_id, block.version) for block in blocks] == [(block_id, 1)]

    _, read = block_tools(blocks)
    latest = (await _call(read, block_id=block_id))["data"]
    assert (latest["version"], latest["content"]) == (1, PAGE)
    assert await _call(read, block_id=later_id) == {"success": False, "error": "not_found"}


@pytest.mark.asyncio
async def test_registry_threads_history_blocks_to_read_block() -> None:
    block_id = "blk_" + "e" * 16
    history_blocks = list(iter_persisted_blocks([_persisted_message(PAGE, block_id)]))
    registry = create_default_registry(
        user_id=USER, conversation_id=CONVERSATION, history_blocks=history_blocks
    )
    read_block = registry.get("read_block")
    assert read_block is not None
    result = json.loads(await read_block.execute(block_id=block_id))
    assert result["data"]["content"] == PAGE
