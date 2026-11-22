"""Chat blocks: conversation-scoped content the client renders inline or in the panel.

See docs/ARTIFACT_PRESENTATION_DESIGN.md. A chat block is message content, not a
durable artifact: its source lives only in the message's encrypted tool trace
(the ``render_block`` call arguments), and it is deleted with the conversation.
The tool never echoes the source back to the model; ``read_block`` returns it on
request so a later turn can build on it. Each render is a new block
(``version`` is always 1): revisions are deferred until a revision identity
cannot collide across overlapping turns (review on #779).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from orchestrator.tools.registry import Tool

RENDER_BLOCK = "render_block"
READ_BLOCK = "read_block"

BLOCK_KINDS = ("html", "svg", "markdown", "code", "csv", "json")
#: Only passive svg renders inline. html stays a card, shown as source, until
#: E1 script execution meets its release condition (design D4/D6).
INLINE_KINDS = frozenset({"svg"})
MAX_BLOCK_BYTES = 256 * 1024
MAX_INLINE_BYTES = 48 * 1024
MAX_BLOCKS_PER_TURN = 4
MAX_TITLE_CHARS = 120

_BLOCK_ID = re.compile(r"^blk_[0-9a-f]{16}$")
#: Titles are quoted in the history note; keep a title from closing the quote.
_NOTE_QUOTES = str.maketrans({'"': "'"})
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
        title = (_clean_title(data.get("title")) or "Untitled").translate(_NOTE_QUOTES)
        kind = data.get("kind") if data.get("kind") in BLOCK_KINDS else "block"
        lines.append(f'[block {block_id} v{version} {kind} "{title}"]')
    if not lines:
        return None
    return (
        "\n\n"
        + "\n".join(lines)
        + "\n(Shown to the user. Call read_block with the id to see its source.)"
    )


class _ConversationBlocks:
    """Blocks visible to this turn: its accepted history plus its own renders.

    The persisted part comes from the exact history rows the turn was built
    from (``iter_persisted_blocks`` over them), never from a fresh read: a
    block written after the turn was accepted is not readable, and newer rows
    cannot push eligible blocks out of view. Where a caller has no such
    history, reads are withheld.
    """

    def __init__(self, history_blocks: Sequence[BlockVersion] = ()) -> None:
        self._persisted = list(history_blocks)
        self.turn: list[BlockVersion] = []

    async def versions(self, block_id: str) -> list[BlockVersion]:
        return [block for block in [*self._persisted, *self.turn] if block.block_id == block_id]


class RenderBlockTool(Tool):
    name = RENDER_BLOCK
    description = (
        "Show content to the user as a chat block. Small svg renders inline as a static image; "
        "other content opens as a card with a full preview panel. Every block can be "
        "downloaded. HTML is shown as source with a download and does not run in the chat: "
        "make it self-contained (inline all data, styles and scripts; no external libraries, "
        "fonts, images or requests) so it works when the user opens the downloaded file. Use "
        "this instead of a code fence when the user wants something to see, keep or download. "
        "Do not repeat the content in your reply. Each call creates a new block; to change "
        "one, render the full updated content again."
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": list(BLOCK_KINDS),
                "description": "html (complete page, shown as source with a download), svg, "
                "markdown (document), "
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
                "description": "auto (default): inline when small svg, otherwise panel.",
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

        # Every render is a new block. Revisions are deferred: a version number
        # derived from the turn's frozen history can collide when two
        # overlapping turns revise the same block (review on #779).
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
                    **({"language": language} if language else {}),
                },
            }
        )


class ReadBlockTool(Tool):
    name = READ_BLOCK
    description = (
        "Read the source of a chat block shown earlier in this conversation, so you can "
        "answer questions about it or build an updated block from it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "block_id": {"type": "string", "description": "The block id, e.g. blk_…"},
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


def block_tools(history_blocks: Sequence[BlockVersion] | None = None) -> list[Tool]:
    """Both tools share one per-turn view of the turn's accepted blocks."""
    blocks = _ConversationBlocks(history_blocks or ())
    return [RenderBlockTool(blocks), ReadBlockTool(blocks)]
