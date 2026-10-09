from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from orchestrator.services.fetch.service import FetchService
from orchestrator.services.fetch.models import EXTRACTION_VERSION_V1, FetchContentError
from orchestrator.services.web_snapshots import WebSnapshot, WebSnapshotError, WebSnapshotStore
from orchestrator.tools.registry import Tool


class RefreshGuard(Protocol):
    """Trusted task provenance, never model-supplied owner/epoch arguments."""

    async def pinned(
        self, url: str, mode: str, version: str, refresh: bool
    ) -> uuid.UUID | None: ...

    def publication_hook(
        self, url: str, mode: str, version: str, refresh: bool
    ) -> Callable[[Any, uuid.UUID], Awaitable[None]]: ...


class WebFetchTool(Tool):
    name = "web_fetch"
    description = (
        "Read public pages in bounded sections. Reuse snapshot_id and next_start_char to read "
        "more of the same immutable source. Find literal text or list this conversation's "
        "saved sources. A section is not the complete page; refresh creates a new version."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["read", "find", "list"], "default": "read"},
            "url": {"type": "string"},
            "snapshot_id": {"type": "string"},
            "extract": {
                "type": "string",
                "enum": ["article", "text", "markdown", "metadata", "transcript"],
            },
            "force_refresh": {"type": "boolean", "default": False},
            "start_char": {"type": "integer", "minimum": 0},
            "max_chars": {"type": "integer", "minimum": 1},
            "query": {"type": "string", "maxLength": 256},
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
    }

    def __init__(
        self,
        store: WebSnapshotStore | None = None,
        user_id: uuid.UUID | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> None:
        self._store = store
        self._user_id = user_id
        self._conversation_id = conversation_id
        self._fetch_service: FetchService | None = None
        self._allowance: Callable[[str], bool] | None = None
        self._created = 0
        #: Durable tasks set this: a refresh counts once per task, so a
        #: regenerated attempt reuses the snapshot an earlier attempt already
        #: refreshed instead of fetching it again (#475). Keyed by task
        #: identity, never by comparing clocks of different hosts.
        self.refresh_guard: RefreshGuard | None = None

    def set_result_allowance(self, allowance: Callable[[str], bool] | None) -> None:
        self._allowance = allowance

    @staticmethod
    def _normalize_extract_mode(extract: Any) -> str:
        value = str(extract or "article").strip().lower()
        return {"text": "article", "markdown": "article"}.get(value, value)

    @staticmethod
    def _error(code: str) -> str:
        return json.dumps({"error": code})

    def _fits(self, result: str) -> bool:
        return len(result.encode("utf-8")) <= 80_000 and (
            self._allowance is None or self._allowance(result)
        )

    @staticmethod
    def _integer(kwargs: dict[str, Any], key: str, default: int, minimum: int) -> int:
        value = kwargs.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError("Invalid numeric argument")
        return value

    def _section(self, snapshot: WebSnapshot, start: int, length: int) -> str:
        def encode(count: int) -> str:
            end = min(start + count, snapshot.content_chars)
            content = snapshot.content[start:end]
            return json.dumps(
                {
                    "snapshot_id": str(snapshot.id),
                    "url": snapshot.source_url,
                    "final_url": snapshot.final_url,
                    "title": snapshot.title,
                    "retrieved_at": snapshot.retrieved_at.isoformat(),
                    "expires_at": snapshot.expires_at.isoformat(),
                    "content": content,
                    "content_length": len(content),
                    "total_chars": snapshot.content_chars,
                    "start_char": start,
                    "end_char": end,
                    "next_start_char": end if end < snapshot.content_chars else None,
                    "complete": start == 0 and end == snapshot.content_chars,
                    "has_more": end < snapshot.content_chars,
                },
                ensure_ascii=False,
            )

        low, high = 0, min(length, snapshot.content_chars - start)
        if not self._fits(encode(0)):
            return self._error("context_budget_exhausted")
        while low < high:
            middle = (low + high + 1) // 2
            if self._fits(encode(middle)):
                low = middle
            else:
                high = middle - 1
        if low == 0 and start < snapshot.content_chars:
            return self._error("context_budget_exhausted")
        return encode(low)

    async def execute(self, **kwargs: Any) -> str:
        store, user, conversation = self._store, self._user_id, self._conversation_id
        if store is None or user is None or conversation is None:
            return self._error("snapshot_storage_unavailable")
        try:
            action = kwargs.get("action", "read")
            if action == "list":
                offset = self._integer(kwargs, "offset", 0, 0)
                limit = min(20, self._integer(kwargs, "limit", 20, 1))
                page = await store.list(user, conversation, offset=offset, limit=limit)
                entries = [
                    {
                        "snapshot_id": str(item.id),
                        "title": item.title[:240],
                        "retrieved_at": item.retrieved_at.isoformat(),
                        "expires_at": item.expires_at.isoformat(),
                    }
                    for item in page.items
                ]
                while True:
                    if page.items and not entries:
                        return self._error("context_budget_exhausted")
                    result = json.dumps(
                        {
                            "sources": entries,
                            "total": page.total,
                            "next_offset": offset + len(entries)
                            if offset + len(entries) < page.total
                            else None,
                        }
                    )
                    if self._fits(result):
                        return result
                    if not entries:
                        return self._error("context_budget_exhausted")
                    entries.pop()
            if action not in {"read", "find"}:
                return self._error("invalid_action")
            mode = self._normalize_extract_mode(kwargs.get("extract"))
            if mode not in {"article", "metadata", "transcript"}:
                return self._error("invalid_extract_mode")
            refresh = kwargs.get("force_refresh", False)
            if not isinstance(refresh, bool):
                return self._error("invalid_refresh")
            snapshot_id = kwargs.get("snapshot_id")
            url = kwargs.get("url")
            if isinstance(url, str):
                url = url.strip()
            if snapshot_id:
                if refresh:
                    return self._error("refresh_requires_url_without_snapshot_id")
                snapshot = await store.get(user, conversation, uuid.UUID(str(snapshot_id)))
                if (url is not None and url != snapshot.source_url) or (
                    "extract" in kwargs and mode != snapshot.extract_mode
                ):
                    return self._error("snapshot_source_mismatch")
            else:
                if action == "find" or not isinstance(url, str) or not url or len(url) > 8192:
                    return self._error("url_or_snapshot_id_required")
                # Extraction version belongs to this immutable representation.
                version = EXTRACTION_VERSION_V1
                guard = self.refresh_guard
                pinned = await guard.pinned(url, mode, version, refresh) if guard else None
                if pinned is not None:
                    snapshot = await store.get(user, conversation, pinned)
                elif refresh:
                    snapshot = None
                elif guard is None:
                    snapshot = await store.find_latest(user, conversation, url, mode, version)
                else:
                    snapshot = await store.find_latest(
                        user,
                        conversation,
                        url,
                        mode,
                        version,
                        on_selected=guard.publication_hook(url, mode, version, refresh),
                    )
                if snapshot is None:
                    if self._created >= store.settings.web_snapshot_max_new_per_turn:
                        return self._error("snapshot_turn_limit")
                    if not self._fits(self._error("fetch_failed") + " " * 1024):
                        return self._error("context_budget_exhausted")
                    if self._fetch_service is None:
                        self._fetch_service = FetchService()
                    fetched = await self._fetch_service.fetch(
                        url=url, extract=mode, force_refresh=refresh, use_cache=False
                    )
                    if fetched is None:
                        return self._error("fetch_failed")
                    if fetched.extraction_version != version:
                        return self._error("snapshot_representation_mismatch")
                    snapshot = await store.create(
                        user,
                        conversation,
                        source_url=fetched.source_url or url,
                        final_url=fetched.final_url or fetched.source_url or url,
                        title=fetched.title,
                        content=fetched.content,
                        extract_mode=mode,
                        extraction_version=fetched.extraction_version,
                        on_created=guard.publication_hook(url, mode, version, refresh)
                        if guard is not None
                        else None,
                    )
                    self._created += 1
            start = self._integer(kwargs, "start_char", 0, 0)
            if start > snapshot.content_chars:
                return self._error("offset_out_of_range")
            if action == "find":
                query = kwargs.get("query")
                if not isinstance(query, str) or not query or len(query) > 256:
                    return self._error("invalid_query")
                matches: list[dict[str, Any]] = []
                cursor = start
                limit = min(20, self._integer(kwargs, "limit", 10, 1))
                for _ in range(limit):
                    found = snapshot.content.find(query, cursor)
                    if found < 0:
                        cursor = snapshot.content_chars
                        break
                    end = found + len(query)
                    entry = {
                        "start_char": found,
                        "end_char": end,
                        "excerpt_start": max(0, found - 100),
                        "excerpt": snapshot.content[
                            max(0, found - 100) : min(snapshot.content_chars, end + 100)
                        ],
                    }
                    candidate = json.dumps(
                        {
                            "snapshot_id": str(snapshot.id),
                            "matches": [*matches, entry],
                            "next_start_char": end,
                        }
                    )
                    if not self._fits(candidate):
                        if not matches:
                            return self._error("context_budget_exhausted")
                        break
                    matches.append(entry)
                    cursor = end
                result = json.dumps(
                    {
                        "snapshot_id": str(snapshot.id),
                        "matches": matches,
                        "next_start_char": cursor if cursor < snapshot.content_chars else None,
                    }
                )
                return result if self._fits(result) else self._error("context_budget_exhausted")
            length = self._integer(
                kwargs, "max_chars", store.settings.web_snapshot_default_chunk_chars, 1
            )
            if length > store.settings.web_snapshot_max_chunk_chars:
                return self._error("invalid_chunk_limit")
            return self._section(snapshot, start, length)
        except FetchContentError as exc:
            return self._error(exc.code)
        except WebSnapshotError as exc:
            return self._error(
                "snapshot_expired"
                if exc.reason in {"expired", "not_found", "owner_mismatch"}
                else exc.reason
            )
        except (ValueError, TypeError):
            return self._error("invalid_snapshot_arguments")
        except Exception:
            return self._error("snapshot_unavailable")
