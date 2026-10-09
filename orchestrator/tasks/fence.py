"""Effect fence for tools run by durable tasks (DURABLE_REQUEST_DESIGN §9).

Slice 1 treats every tool not shown to be safe to repeat as *material*. Before
a material tool runs, its operation is committed under the task's lease fence,
so a later retry can tell "never attempted" from "may have happened" and stop
at ``needs_attention`` instead of repeating it.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from typing import Any, Final

from orchestrator.tasks.store import EffectRefused, LeaseLost, TaskStore
from orchestrator.tools.executor import OperationIdentity, ToolExecution
from orchestrator.tools.registry import Tool

logger = logging.getLogger(__name__)

#: Tools whose repetition has no material external effect: reads, arithmetic
#: and the clock. Anything else, including any tool added later, is material
#: until it is deliberately classified (slice 3 maps every tool's recovery class).
REPEATABLE_TOOLS = frozenset(
    {
        "calculate",
        "get_time",
        "memory_read",
        "memory_reflect",
        "reminder_list",
        "web_fetch",
        "web_search",
    }
)

#: Lease time that must remain when a material operation is recorded.
MIN_LEASE_MARGIN_S = 15.0


def is_material(tool_name: str) -> bool:
    return tool_name not in REPEATABLE_TOOLS


def _target_summary(tool_name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """What the operation was aimed at, without bodies or credentials."""
    summary: dict[str, Any] = {"tool": tool_name}
    for key in ("url", "method", "channel", "topic", "title", "when", "name", "action"):
        value = kwargs.get(key)
        if isinstance(value, (str, int, float, bool)):
            summary[key] = value if not isinstance(value, str) else value[:200]
    return summary


def _outcome_of(result: Any) -> str:
    """The recorded outcome of a material tool, from what it returned.

    A returned error is not proof that nothing happened: a write can time out
    after the server accepted it. Such a result is recorded ``unknown`` (the
    evidence then says the effect may have happened), and ``failed`` only when
    the tool states explicitly that it did not perform the action
    (``"performed": false``). Missing/legacy evidence is unknown, not success.
    """
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return "unknown"
    if not isinstance(result, dict):
        return "unknown"
    if result.get("performed") is False:
        return "failed"
    if result.get("success") is False or (
        bool(result.get("error")) and result.get("success") is not True
    ):
        return "unknown"
    status = result.get("status")
    if isinstance(status, int) and not isinstance(status, bool):
        # HTTP error responses do not prove that a write had no effect.
        return "succeeded" if 200 <= status < 300 else "unknown"
    if result.get("success") is True or result.get("performed") is True:
        return "succeeded"
    return "unknown"


def _as_json_object(result: Any) -> dict[str, Any] | None:
    """The evidence as a JSON object, or None when it is not one.

    Malformed or absent JSON is not proof of anything here; the caller
    classifies it ``unknown``. The parsed value stays local, so the input
    evidence is never mutated.
    """
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return None
    if isinstance(result, dict):
        return result
    return None


_JSON_PARSE_FAILED: Final[object] = object()


def _parsed_json(result: str) -> Any:
    """The parsed JSON value of text evidence, or a "no evidence" sentinel.

    The sentinel matters: ``json.loads("null")`` yields ``None``, which is a
    successful parse of evidence a tool never emits, not a parse failure.
    """
    try:
        return json.loads(result)
    except (TypeError, ValueError):
        return _JSON_PARSE_FAILED


def _readonly_outcome_of_json(
    result: dict[str, Any],
    recognized_success: Callable[[dict[str, Any]], bool],
) -> str:
    """One read-only tool's outcome from its JSON evidence.

    A read-only tool has no external effect, so what matters is only whether
    the read ran: an explicit error or non-performance is ``failed``, explicit
    success evidence and any recognized success shape (including a valid empty
    result set) is ``succeeded``, and malformed, absent or unrecognized
    evidence stays ``unknown``. Repeated anyway for parity with the material
    contract: ``success`` alongside an error means the error is noise, not a
    failure statement.
    """
    if result.get("performed") is False:
        return "failed"
    if (bool(result.get("error")) and result.get("success") is not True) or (
        result.get("success") is False
    ):
        return "failed"
    if result.get("success") is True or result.get("performed") is True:
        return "succeeded"
    return "succeeded" if recognized_success(result) else "unknown"


def _is_nonnegative_int(value: Any) -> bool:
    """A real nonnegative integer: not a bool, and not a negative counter."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_optional_nonnegative_int(value: Any) -> bool:
    return value is None or _is_nonnegative_int(value)


def _is_snapshot_id(value: Any) -> bool:
    """web_fetch identities are UUID strings; anything else is drift."""
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _is_web_search_success(result: dict[str, Any]) -> bool:
    """The normalized search payload: query, typed result entries, counters.

    Every entry carries a public ``url`` (the tool drops entries that have
    none), so an untyped or URL-less item is not a recognized result set.
    """
    results = result.get("results")
    return (
        isinstance(result.get("query"), str)
        and isinstance(results, list)
        and all(isinstance(entry, dict) and isinstance(entry.get("url"), str) for entry in results)
        and _is_nonnegative_int(result.get("total_found"))
        and result["total_found"] == len(results)
    )


def _is_web_fetch_list(result: dict[str, Any]) -> bool:
    """The saved-sources listing: typed entries with a real UUID identity,
    a nonnegative total and an optional nonnegative next_offset. An empty
    list is a valid result."""
    sources = result.get("sources")
    return (
        isinstance(sources, list)
        and all(
            isinstance(entry, dict) and _is_snapshot_id(entry.get("snapshot_id"))
            for entry in sources
        )
        and _is_nonnegative_int(result.get("total"))
        and result["total"] >= len(sources)
        and _is_optional_nonnegative_int(result.get("next_offset"))
        and (
            result.get("next_offset") is None
            or len(sources) <= result["next_offset"] < result["total"]
        )
    )


def _is_web_fetch_section(result: dict[str, Any]) -> bool:
    """A read section or a find page; both are completed reads.

    The identity is a UUID string, section counters are real nonnegative
    integers (never negative), and find pages carry typed match entries.
    A read with empty content and a find with no matches are valid results.
    """
    if not _is_snapshot_id(result.get("snapshot_id")):
        return False
    next_start_char = result.get("next_start_char")
    if not _is_optional_nonnegative_int(next_start_char):
        return False
    if "content" in result:
        content = result["content"]
        if not isinstance(content, str) or not (
            _is_nonnegative_int(result.get("start_char"))
            and _is_nonnegative_int(result.get("end_char"))
            and _is_nonnegative_int(result.get("content_length"))
            and _is_nonnegative_int(result.get("total_chars"))
        ):
            return False
        start, end, total = result["start_char"], result["end_char"], result["total_chars"]
        return (
            start <= end <= total
            and result["content_length"] == len(content) == end - start
            and next_start_char == (end if end < total else None)
            and result.get("complete") is (start == 0 and end == total)
            and result.get("has_more") is (end < total)
        )
    matches = result.get("matches")
    if not isinstance(matches, list) or not all(
        isinstance(entry, dict)
        and _is_nonnegative_int(entry.get("start_char"))
        and _is_nonnegative_int(entry.get("end_char"))
        and entry["start_char"] < entry["end_char"]
        for entry in matches
    ):
        return False
    # find advances the cursor to each accepted match's end. No match means
    # exhaustion; a non-null cursor must name the final returned match's end.
    previous_end = 0
    for entry in matches:
        if entry["start_char"] < previous_end:
            return False
        previous_end = entry["end_char"]
    return next_start_char is None or (bool(matches) and next_start_char == previous_end)


def _outcome_of_web_search(result: Any) -> str:
    shaped = _as_json_object(result)
    if shaped is None:
        return "unknown"
    return _readonly_outcome_of_json(shaped, _is_web_search_success)


def _outcome_of_web_fetch(result: Any) -> str:
    shaped = _as_json_object(result)
    if shaped is None:
        return "unknown"
    return _readonly_outcome_of_json(
        shaped, lambda result: _is_web_fetch_list(result) or _is_web_fetch_section(result)
    )


def _is_calculate_success(result: dict[str, Any]) -> bool:
    expression = result.get("expression")
    value = result.get("result")
    return (
        isinstance(expression, str)
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    )


def _outcome_of_calculate(result: Any) -> str:
    shaped = _as_json_object(result)
    if shaped is None:
        return "unknown"
    return _readonly_outcome_of_json(shaped, _is_calculate_success)


def _is_get_time_success(result: dict[str, Any]) -> bool:
    return isinstance(result.get("time"), str) and isinstance(result.get("timezone"), str)


def _outcome_of_get_time(result: Any) -> str:
    shaped = _as_json_object(result)
    if shaped is None:
        return "unknown"
    return _readonly_outcome_of_json(shaped, _is_get_time_success)


def _is_reminder_list_success(result: dict[str, Any]) -> bool:
    return isinstance(result.get("reminders"), list) and _is_nonnegative_int(result.get("count"))


def _outcome_of_reminder_list(result: Any) -> str:
    shaped = _as_json_object(result)
    if shaped is None:
        return "unknown"
    return _readonly_outcome_of_json(shaped, _is_reminder_list_success)


_MEMORY_READ_ARGUMENT_ERROR: Final[str] = "Invalid 'after' or 'before' timestamp. Use ISO8601."
#: The tool's explicit "nothing found" result is a completed empty read.
_MEMORY_READ_EMPTY_SUCCESS: Final[str] = "No relevant memories found."


def _is_memory_read_line(result: str) -> bool:
    """The producer's only multiline success shape: every line is a formatted
    ``- [CATEGORY]...`` entry (slotted and history variants share the prefix
    and are typed by the category bracket). Any other text is not evidence
    that the read completed.
    """
    lines = result.split("\n")
    return bool(lines) and all(
        line.startswith("- [")
        and (closing := line.find("]", 3)) > 3
        and line[closing + 1 :].startswith(" ")
        for line in lines
    )


def _outcome_of_memory_read(result: Any) -> str:
    """memory_read answers in plain text on its completed paths only:
    formatted ``- [CATEGORY]...`` lines, this explicit empty result, or the
    one argument error. The shared tool executor can also turn exceptions or
    invalid arguments into a JSON error, handled by outcome_of_tool_result.

    Everything else is unrecognized: whitespace-only text, JSON-shaped
    evidence such as ``null``, ``true``, a bare object or a truncated payload
    (this tool never emits JSON), and arbitrary unmatched text stay ``unknown``
    instead of being equated with success. A memory whose content contains a
    newline breaks the line shape and also stays unknown; that is the safe
    direction for evidence without a recognizable shape.
    """
    if not isinstance(result, str):
        return "unknown"
    if _parsed_json(result) is not _JSON_PARSE_FAILED:
        return "unknown"
    if not result.strip():
        return "unknown"
    if result == _MEMORY_READ_ARGUMENT_ERROR:
        return "failed"
    if result == _MEMORY_READ_EMPTY_SUCCESS or _is_memory_read_line(result):
        return "succeeded"
    return "unknown"


_MEMORY_REFLECT_FAILURE_TEXTS: Final[frozenset[str]] = frozenset(
    {
        "No topic provided for reflection.",
        "Reflection is available only in a verified cloud conversation.",
    }
)
_MEMORY_REFLECT_FAILURE_PREFIXES: Final[tuple[str, ...]] = ("Reflection synthesis failed:",)
#: Explicit completed-empty results the producer returns verbatim; they are the
#: only "success" texts recognized, because arbitrary synthesis narrative —
#: however plausible — carries no shape the classifier can check.
_MEMORY_REFLECT_EMPTY_SUCCESS_TEXTS: Final[frozenset[str]] = frozenset(
    {
        "No relevant memories found for reflection. Either no memories exist yet, or "
        "none matched the topic closely enough.",
        "Reflection generated but produced no content.",
    }
)


def _outcome_of_memory_reflect(result: Any) -> str:
    """memory_reflect answers in plain text, but its synthesis content is
    arbitrary narrative with no checkable shape, so only explicit,
    verbatim-comparison evidence is classified: the two producer refusals and
    ``Reflection synthesis failed:``-prefixed failures are ``failed``, the two
    exact completed-empty messages are ``succeeded``, and everything else —
    whitespace-only text, JSON-shaped ``null``/``true``/objects/truncated
    payloads, and unmatched narrative including arbitrary success-looking
    text — stays ``unknown``. Limitation, documented: a well-formed
    reflection that no longer matches a recognized text is classified
    unknown, the safe direction; it never spoils an explicit outcome.
    """
    if not isinstance(result, str):
        return "unknown"
    if _parsed_json(result) is not _JSON_PARSE_FAILED:
        return "unknown"
    if not result.strip():
        return "unknown"
    if result in _MEMORY_REFLECT_FAILURE_TEXTS or result.startswith(
        _MEMORY_REFLECT_FAILURE_PREFIXES
    ):
        return "failed"
    if result in _MEMORY_REFLECT_EMPTY_SUCCESS_TEXTS:
        return "succeeded"
    return "unknown"


#: Tool-aware outcome classification for the read-only repeatable tools. Each
#: classifier is audited against that tool's actual return statements; a tool
#: added later keeps the material contract until deliberately classified.
_READONLY_OUTCOME_CLASSIFIERS: Final[dict[str, Callable[[Any], str]]] = {
    "calculate": _outcome_of_calculate,
    "get_time": _outcome_of_get_time,
    "memory_read": _outcome_of_memory_read,
    "memory_reflect": _outcome_of_memory_reflect,
    "reminder_list": _outcome_of_reminder_list,
    "web_fetch": _outcome_of_web_fetch,
    "web_search": _outcome_of_web_search,
}


def outcome_of_tool_result(tool_name: str | None, result: Any) -> str:
    """The recorded outcome of a task tool result, from what it returned.

    Material tools keep the conservative :func:`_outcome_of` contract, because
    a returned error does not prove that a write had no effect. The read-only
    repeatable tools classified above have no external effect, so their
    recognized success shapes (including valid empty results) are ``succeeded``
    and their explicit errors and refusals are ``failed``; malformed, absent or
    unrecognized evidence stays ``unknown`` for every tool. Unknown or missing
    tool names fall back to the material contract.
    """
    classifier = (
        _READONLY_OUTCOME_CLASSIFIERS.get(tool_name) if isinstance(tool_name, str) else None
    )
    if classifier is None:
        return _outcome_of(result)
    # Even text-returning tools pass through ToolExecutor, which emits a JSON
    # error on invalid arguments or execution failure. This is known failure
    # evidence for a read, never proof that a material effect did not happen.
    shaped = _as_json_object(result)
    if shaped is not None and (
        shaped.get("performed") is False
        or shaped.get("success") is False
        or (bool(shaped.get("error")) and shaped.get("success") is not True)
    ):
        return "failed"
    return classifier(result)


class FencedTool(Tool):
    """Proxy that records a material operation before delegating."""

    def __init__(
        self,
        inner: Tool,
        store: TaskStore,
        task_id: uuid.UUID,
        epoch: int,
        on_lease_lost: Callable[[], None] | None = None,
        on_refused: Callable[[str], None] | None = None,
    ) -> None:
        self._inner = inner
        self._store = store
        self._task_id = task_id
        self._epoch = epoch
        self._on_lease_lost = on_lease_lost
        self._on_refused = on_refused
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters

    def __getattr__(self, attribute: str) -> Any:
        # Optional hooks such as set_result_allowance stay on the inner tool.
        return getattr(self._inner, attribute)

    def to_openai_schema(self) -> dict[str, Any]:
        return self._inner.to_openai_schema()

    async def execute(self, **kwargs: Any) -> str:
        return (await self.execute_invocation(**kwargs)).result

    async def execute_invocation(self, **kwargs: Any) -> ToolExecution:
        try:
            operation_id = await self._store.begin_operation(
                self._task_id,
                self._epoch,
                tool_name=self.name,
                target=_target_summary(self.name, kwargs),
                min_lease_margin_s=MIN_LEASE_MARGIN_S,
            )
        except LeaseLost:
            # This attempt no longer owns the task: the effect is not attempted,
            # and the attempt stops now rather than at its next heartbeat.
            if self._on_lease_lost is not None:
                self._on_lease_lost()
            return ToolExecution(
                json.dumps(
                    {
                        "success": False,
                        "performed": False,
                        "error": "Not performed: this task attempt was superseded.",
                    }
                )
            )
        except EffectRefused as refused:
            # Cancelled, suspended or out of lease time: the effect is not
            # attempted, and a cancel or suspension stops the attempt now.
            if self._on_refused is not None:
                self._on_refused(refused.reason)
            return ToolExecution(
                json.dumps(
                    {
                        "success": False,
                        "performed": False,
                        "error": "Not performed: this task can no longer act.",
                    }
                )
            )
        try:
            result = await self._inner.execute(**kwargs)
        except BaseException:
            await self._finish(operation_id, "unknown")
            raise
        outcome = _outcome_of(result)
        await self._finish(operation_id, outcome)
        return ToolExecution(
            result, OperationIdentity(self._task_id, self._epoch, operation_id, self.name, outcome)
        )

    async def _finish(self, operation_id: uuid.UUID, outcome: str) -> None:
        try:
            await self._store.finish_operation(operation_id, outcome=outcome)
        except Exception:
            # The started row already marks the effect as uncertain.
            logger.warning("Could not record task operation outcome", exc_info=True)


def guard_registry(
    registry: Any,
    store: TaskStore,
    task_id: uuid.UUID,
    epoch: int,
    on_lease_lost: Callable[[], None] | None = None,
    on_refused: Callable[[str], None] | None = None,
) -> None:
    """Wrap every material tool in ``registry`` with the effect fence."""
    for name in registry.names():
        tool = registry.get(name)
        if tool is not None and is_material(name) and not isinstance(tool, FencedTool):
            registry.register(FencedTool(tool, store, task_id, epoch, on_lease_lost, on_refused))
