"""Chat blocks: conversation-scoped content the client renders inline or in the panel.

See docs/ARTIFACT_PRESENTATION_DESIGN.md. A chat block is message content, not a
durable artifact: its source lives only in the message's encrypted tool trace
(the ``render_block`` call arguments), and it is deleted with the conversation.
The tool never echoes the source back to the model; ``read_block`` returns it on
request so a later turn can revise it.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from orchestrator.tools.registry import Tool

RENDER_BLOCK = "render_block"
READ_BLOCK = "read_block"

BLOCK_KINDS = ("html", "svg", "markdown", "code", "csv", "json")
INLINE_KINDS = frozenset({"html", "svg"})
MAX_BLOCK_BYTES = 256 * 1024
MAX_INLINE_BYTES = 48 * 1024
MAX_BLOCKS_PER_TURN = 4
MAX_TITLE_CHARS = 120
# Bounded look-back for revisions/reads. Blocks older than this window are
# reported as not found rather than scanned without a limit.
HISTORY_LOOKBACK_MESSAGES = 200

_BLOCK_ID = re.compile(r"^blk_[0-9a-f]{16}$")
_LANGUAGE = re.compile(r"^[a-z0-9][a-z0-9+#.-]{0,19}$")


@dataclass(frozen=True)
class BlockVersion:
    block_id: str
    version: int
    kind: str
    title: str
    content: str
    sha256: str
    language: str | None = None


def _failure(code: str) -> str:
    return json.dumps({"success": False, "error": code})


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _clean_title(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    title = " ".join(value.split())
    if not title or len(title) > MAX_TITLE_CHARS:
        return None
    return title


def _valid_content(kind: str, content: Any) -> str | None:
    """Return an error code, or None when the content is acceptable."""
    if not isinstance(content, str) or not content.strip():
        return "invalid_content"
    if "\x00" in content:
        return "invalid_content"
    try:
        size = len(content.encode("utf-8"))
    except UnicodeEncodeError:
        return "invalid_content"
    if size > MAX_BLOCK_BYTES:
        return "too_large"
    if kind == "json":
        try:
            json.loads(content)
        except ValueError:
            return "invalid_content"
    elif kind == "csv":
        try:
            rows = [row for row in csv.reader(io.StringIO(content)) if row]
        except csv.Error:
            return "invalid_content"
        if not rows:
            return "invalid_content"
    return None


def _result_data(entry: Any) -> Mapping[str, Any] | None:
    if not isinstance(entry, Mapping) or entry.get("name") != RENDER_BLOCK:
        return None
    result = entry.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, Mapping) or result.get("success") is not True:
        return None
    data = result.get("data")
    return data if isinstance(data, Mapping) else None


def iter_persisted_blocks(messages: Iterable[Mapping[str, Any]]) -> Iterable[BlockVersion]:
    """Yield blocks recorded in persisted assistant messages, oldest first.

    A result is trusted only when a ``render_block`` call in the same message
    carries content with the result's digest; the client pairs them the same way.
    """
    for message in messages:
        if message.get("role") != "assistant":
            continue
        calls = message.get("tool_calls") or []
        sources: dict[str, Mapping[str, Any]] = {}
        for call in calls if isinstance(calls, list) else []:
            if not isinstance(call, Mapping) or call.get("name") != RENDER_BLOCK:
                continue
            arguments = call.get("arguments")
            if isinstance(arguments, Mapping) and isinstance(arguments.get("content"), str):
                sources.setdefault(_sha256(arguments["content"]), arguments)
        results = message.get("tool_results") or []
        for entry in results if isinstance(results, list) else []:
            data = _result_data(entry)
            if data is None:
                continue
            digest = data.get("sha256")
            if not isinstance(digest, str):
                continue
            arguments = sources.get(digest)
            block_id = data.get("block_id")
            version = data.get("version")
            if (
                arguments is None
                or not isinstance(block_id, str)
                or not _BLOCK_ID.match(block_id)
                or not isinstance(version, int)
                or version < 1
            ):
                continue
            language = arguments.get("language")
            yield BlockVersion(
                block_id=block_id,
                version=version,
                kind=str(data.get("kind") or arguments.get("kind") or ""),
                title=_clean_title(data.get("title")) or "Untitled",
                content=arguments["content"],
                sha256=digest,
                language=language if isinstance(language, str) else None,
            )


def block_history_note(tool_results: Any) -> str | None:
    """Model-only note naming the blocks an earlier assistant turn produced.

    History replay carries message text only, so without this a later turn
    cannot know a block exists. The note is added when history is built for the
    model; stored message content is unchanged.
    """
    if not isinstance(tool_results, list):
        return None
    lines = []
    for entry in tool_results:
        data = _result_data(entry)
        if data is None:
            continue
        block_id = data.get("block_id")
        version = data.get("version")
        if not isinstance(block_id, str) or not _BLOCK_ID.match(block_id):
            continue
        if not isinstance(version, int):
            continue
        title = (_clean_title(data.get("title")) or "Untitled").replace('"', "'")
        kind = data.get("kind") if data.get("kind") in BLOCK_KINDS else "block"
        lines.append(f'[block {block_id} v{version} {kind} "{title}"]')
    if not lines:
        return None
    return (
        "\n\n"
        + "\n".join(lines)
        + "\n(Shown to the user. Call read_block with the id to see or revise it.)"
    )


class _ConversationBlocks:
    """Blocks visible to this turn: persisted history plus this turn's renders."""

    def __init__(self, memory_store: Any, user_id: Any, conversation_id: Any) -> None:
        self._store = memory_store
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._persisted: list[BlockVersion] | None = None
        self.turn: list[BlockVersion] = []

    async def _load(self) -> list[BlockVersion]:
        if self._persisted is not None:
            return self._persisted
        self._persisted = []
        if self._store is None or self._user_id is None or self._conversation_id is None:
            return self._persisted
        conversation_id = uuid.UUID(str(self._conversation_id))
        conversation = await self._store.get_conversation(conversation_id)
        if not conversation or str(conversation.get("user_id")) != str(self._user_id):
            return self._persisted
        messages = await self._store.get_recent_messages(
            conversation_id,
            limit=HISTORY_LOOKBACK_MESSAGES,
            exclude_status=["streaming", "error", "cancelled"],
        )
        self._persisted = list(iter_persisted_blocks(messages))
        return self._persisted

    async def versions(self, block_id: str) -> list[BlockVersion]:
        persisted = await self._load()
        return [block for block in [*persisted, *self.turn] if block.block_id == block_id]


class RenderBlockTool(Tool):
    name = RENDER_BLOCK
    description = (
        "Show content to the user as a chat block. Small html/svg renders inline in the "
        "message; larger content and other kinds open as a card with a full preview panel. "
        "Every block can be downloaded. HTML may use inline JavaScript and CSS: it runs in an "
        "isolated sandbox with no network access, so inline all data, styles and scripts "
        "(no external libraries, fonts, images or requests). Use this instead of a code fence "
        "when the user wants something to see, use or download. Do not repeat the content in "
        "your reply. To change an existing block, pass its id as `revises`."
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(BLOCK_KINDS),
                "description": "html (interactive page or widget), svg, markdown (document), "
                "code (source file), csv or json.",
            },
            "title": {
                "type": "string",
                "description": "Short human title, also used as the download filename.",
            },
            "content": {
                "type": "string",
                "description": "The complete block source. At most 256 KB.",
            },
            "language": {
                "type": "string",
                "description": "For kind=code only: language name, e.g. python, typescript.",
            },
            "display": {
                "type": "string",
                "enum": ["auto", "inline", "panel"],
                "description": "auto (default): inline when small html/svg, otherwise panel.",
            },
            "revises": {
                "type": "string",
                "description": "Id of an existing block in this conversation to replace with a "
                "new version.",
            },
        },
        "required": ["kind", "title", "content"],
    }

    def __init__(self, blocks: _ConversationBlocks) -> None:
        self._blocks = blocks

    async def execute(self, **kwargs: Any) -> str:
        kind = kwargs.get("kind")
        if kind not in BLOCK_KINDS:
            return _failure("invalid_kind")
        title = _clean_title(kwargs.get("title"))
        if title is None:
            return _failure("invalid_content")
        content = kwargs.get("content")
        error = _valid_content(kind, content)
        if error is not None or not isinstance(content, str):
            return _failure(error or "invalid_content")
        if len(self._blocks.turn) >= MAX_BLOCKS_PER_TURN:
            return _failure("turn_limit")

        language: str | None = None
        if kind == "code" and isinstance(kwargs.get("language"), str):
            candidate = kwargs["language"].strip().lower()
            language = candidate if _LANGUAGE.match(candidate) else None

        revises = kwargs.get("revises")
        if revises not in (None, ""):
            if not isinstance(revises, str) or not _BLOCK_ID.match(revises):
                return _failure("unknown_revision")
            versions = await self._blocks.versions(revises)
            if not versions:
                return _failure("unknown_revision")
            block_id = revises
            version = max(block.version for block in versions) + 1
        else:
            block_id = f"blk_{secrets.token_hex(8)}"
            version = 1

        size = len(content.encode("utf-8"))
        display = kwargs.get("display")
        inline_allowed = kind in INLINE_KINDS and size <= MAX_INLINE_BYTES
        resolved = "inline" if inline_allowed and display != "panel" else "panel"
        digest = _sha256(content)
        self._blocks.turn.append(
            BlockVersion(
                block_id=block_id,
                version=version,
                kind=kind,
                title=title,
                content=content,
                sha256=digest,
                language=language,
            )
        )
        return json.dumps(
            {
                "success": True,
                "data": {
                    "block_id": block_id,
                    "version": version,
                    "kind": kind,
                    "title": title,
                    "display": resolved,
                    "bytes": size,
                    "sha256": digest,
                },
            }
        )


class ReadBlockTool(Tool):
    name = READ_BLOCK
    description = (
        "Read the source of a chat block shown earlier in this conversation, so you can "
        "answer questions about it or revise it with render_block(revises=...)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "block_id": {"type": "string", "description": "The block id, e.g. blk_…"},
            "version": {
                "type": "integer",
                "description": "Optional version; defaults to the latest.",
            },
        },
        "required": ["block_id"],
    }

    def __init__(self, blocks: _ConversationBlocks) -> None:
        self._blocks = blocks

    async def execute(self, **kwargs: Any) -> str:
        block_id = kwargs.get("block_id")
        if not isinstance(block_id, str) or not _BLOCK_ID.match(block_id):
            return _failure("not_found")
        versions = await self._blocks.versions(block_id)
        requested = kwargs.get("version")
        if isinstance(requested, int) and not isinstance(requested, bool):
            versions = [block for block in versions if block.version == requested]
        elif requested is not None:
            return _failure("not_found")
        if not versions:
            return _failure("not_found")
        block = max(versions, key=lambda item: item.version)
        data: dict[str, Any] = {
            "block_id": block.block_id,
            "version": block.version,
            "kind": block.kind,
            "title": block.title,
            "content": block.content,
        }
        if block.language:
            data["language"] = block.language
        return json.dumps({"success": True, "data": data})


def block_tools(memory_store: Any, user_id: Any, conversation_id: Any) -> list[Tool]:
    """Both tools share one per-turn view of the conversation's blocks."""
    blocks = _ConversationBlocks(memory_store, user_id, conversation_id)
    return [RenderBlockTool(blocks), ReadBlockTool(blocks)]
