"""Tool-aware outcome classification of durable-task tool results.

Covers the read-only repeatable tools' actual return shapes (success, valid
empty results, explicit errors and refusals, malformed evidence) and the
unchanged material contract for everything else. Bounded: no HTTP, database,
or inference calls, and no mutation of the input evidence.
"""

import copy
import json

import pytest

from orchestrator.tasks.fence import REPEATABLE_TOOLS, _outcome_of, is_material
from orchestrator.tasks.fence import outcome_of_tool_result


_WEB_SEARCH_SUCCESS_EMPTY = json.dumps({"query": "rust fmt", "results": [], "total_found": 0})

_WEB_SEARCH_SUCCESS_NONEMPTY = json.dumps(
    {
        "query": "latest rust version",
        "results": [{"title": "Example", "url": "https://example.com", "description": "A page"}],
        "total_found": 1,
    }
)

_WEB_FETCH_READ_SECTION = json.dumps(
    {
        "snapshot_id": "0f9a1b2c-1111-4111-8111-111111111111",
        "url": "https://example.com/post",
        "final_url": "https://example.com/post",
        "title": "A post",
        "retrieved_at": "2026-10-09T00:00:00+00:00",
        "expires_at": "2026-10-10T00:00:00+00:00",
        "content": "First section of the page.",
        "content_length": 26,
        "total_chars": 40,
        "start_char": 0,
        "end_char": 26,
        "next_start_char": 26,
        "complete": False,
        "has_more": True,
    }
)

_WEB_FETCH_READ_EMPTY_CONTENT = json.dumps(
    {
        "snapshot_id": "0f9a1b2c-2222-4222-8222-222222222222",
        "url": "https://example.com/empty",
        "content": "",
        "content_length": 0,
        "total_chars": 0,
        "start_char": 0,
        "end_char": 0,
        "next_start_char": None,
        "complete": True,
        "has_more": False,
    }
)

_WEB_FETCH_FIND_EMPTY = json.dumps(
    {"snapshot_id": "0f9a1b2c-3333-4333-8333-333333333333", "matches": [], "next_start_char": None}
)

_WEB_FETCH_LIST_EMPTY = json.dumps({"sources": [], "total": 0, "next_offset": None})

_WEB_FETCH_LIST_NONEMPTY = json.dumps(
    {
        "sources": [
            {
                "snapshot_id": "0f9a1b2c-4444-4444-8444-444444444444",
                "title": "A saved source",
                "retrieved_at": "2026-10-09T00:00:00+00:00",
                "expires_at": "2026-10-10T00:00:00+00:00",
            }
        ],
        "total": 1,
        "next_offset": None,
    }
)


@pytest.mark.parametrize(
    ("tool_name", "result", "expected"),
    [
        # web_search: an empty result list is a valid success; refusals fail.
        ("web_search", _WEB_SEARCH_SUCCESS_EMPTY, "succeeded"),
        ("web_search", _WEB_SEARCH_SUCCESS_NONEMPTY, "succeeded"),
        ("web_search", json.loads(_WEB_SEARCH_SUCCESS_NONEMPTY), "succeeded"),
        ("web_search", json.dumps({"error": "Search provider timed out"}), "failed"),
        (
            "web_search",
            json.dumps({"error": "Search is busy with other requests; try again shortly"}),
            "failed",
        ),
        ("web_search", {"results": "many", "total_found": 2, "query": "q"}, "unknown"),
        ("web_search", {"query": "q"}, "unknown"),
        ("web_search", {"query": "q", "results": [], "total_found": "0"}, "unknown"),
        ("web_search", json.dumps([]), "unknown"),
        ("web_search", "not json {", "unknown"),
        # web_fetch: read, find and list sections; empty sources/matches valid.
        ("web_fetch", _WEB_FETCH_READ_SECTION, "succeeded"),
        ("web_fetch", _WEB_FETCH_READ_EMPTY_CONTENT, "succeeded"),
        ("web_fetch", _WEB_FETCH_FIND_EMPTY, "succeeded"),
        ("web_fetch", _WEB_FETCH_LIST_EMPTY, "succeeded"),
        ("web_fetch", _WEB_FETCH_LIST_NONEMPTY, "succeeded"),
        ("web_fetch", json.loads(_WEB_FETCH_LIST_NONEMPTY), "succeeded"),
        ("web_fetch", json.dumps({"error": "fetch_failed"}), "failed"),
        ("web_fetch", json.dumps({"error": "snapshot_expired"}), "failed"),
        ("web_fetch", json.dumps({"error": "context_budget_exhausted"}), "failed"),
        ("web_fetch", json.dumps({"sources": []}), "unknown"),
        ("web_fetch", {"snapshot_id": "s"}, "unknown"),
        ("web_fetch", "snapshot unavailable", "unknown"),
        # calculate: expression + numeric result, explicit calculation errors.
        ("calculate", json.dumps({"expression": "2 + 2", "result": 4}), "succeeded"),
        ("calculate", json.dumps({"expression": "1/3", "result": 0.5}), "succeeded"),
        (
            "calculate",
            json.dumps({"error": "Calculation failed: division by zero"}),
            "failed",
        ),
        ("calculate", json.dumps({"error": "expression must be a string"}), "failed"),
        ("calculate", json.dumps({"result": 4}), "unknown"),
        ("calculate", json.dumps({"expression": "2+2", "result": "4"}), "unknown"),
        # get_time: both ISO and human shapes carry the same clock fields.
        (
            "get_time",
            json.dumps(
                {
                    "time": "Friday, October 09, 2026 at 10:00 AM",
                    "timezone": "Australia/Adelaide",
                    "tz_abbr": "ACST",
                    "tz_offset": "+0930",
                    "utc_time": "2026-10-09T00:30:00+00:00",
                }
            ),
            "succeeded",
        ),
        (
            "get_time",
            json.dumps(
                {
                    "time": "2026-10-09T10:00:00+09:30",
                    "timezone": "UTC",
                    "tz_abbr": "UTC",
                    "tz_offset": "+0000",
                    "utc_time": "2026-10-09T00:30:00+00:00",
                }
            ),
            "succeeded",
        ),
        ("get_time", json.dumps("2026"), "unknown"),
        # reminder_list: empty and populated lists are both successes.
        ("reminder_list", json.dumps({"count": 0, "reminders": []}), "succeeded"),
        (
            "reminder_list",
            json.dumps({"count": 1, "reminders": [{"id": 1, "text": "call"}]}),
            "succeeded",
        ),
        (
            "reminder_list",
            json.dumps({"error": "Failed to list reminders: disk full"}),
            "failed",
        ),
        ("reminder_list", {"reminders": []}, "unknown"),
        # memory_read returns plain text, not JSON, on its completed paths.
        ("memory_read", "- [PROJECT] Deployed the fence\n- [FACT] Prefers Python", "succeeded"),
        ("memory_read", "No relevant memories found.", "succeeded"),
        ("memory_read", "Invalid 'after' or 'before' timestamp. Use ISO8601.", "failed"),
        ("memory_read", {"error": "Tool execution failed: fixture"}, "failed"),
        # memory_reflect: arbitrary synthesis narrative carries no shape proof.
        (
            "memory_reflect",
            "The user prefers short answers and keeps travel notes in memory.",
            "unknown",
        ),
        (
            "memory_reflect",
            "No relevant memories found for reflection. Either no memories exist "
            "yet, or none matched the topic closely enough.",
            "succeeded",
        ),
        ("memory_reflect", "Reflection generated but produced no content.", "succeeded"),
        ("memory_reflect", "No topic provided for reflection.", "failed"),
        (
            "memory_reflect",
            "Reflection is available only in a verified cloud conversation.",
            "failed",
        ),
        ("memory_reflect", "Reflection synthesis failed: provider unreachable", "failed"),
        # Malformed, empty, missing and unrecognized evidence stays unknown.
        ("memory_read", json.dumps("2026"), "unknown"),
    ],
)
def test_readonly_tool_shapes_are_classified_directly(tool_name, result, expected):
    assert outcome_of_tool_result(tool_name, result) == expected


@pytest.mark.parametrize("tool_name", sorted(REPEATABLE_TOOLS))
@pytest.mark.parametrize("result", [{}, "", None, []])
def test_malformed_absent_and_unrecognized_evidence_stays_unknown(tool_name, result):
    assert outcome_of_tool_result(tool_name, result) == "unknown"


@pytest.mark.parametrize(
    ("tool_name", "result", "expected"),
    [
        # Unknown or missing tool names keep the material contract.
        ("notification_send", {"success": True}, "succeeded"),
        ("notification_send", {"success": False, "error": "timeout"}, "unknown"),
        ("notification_send", {"error": "timeout without performed"}, "unknown"),
        ("notification_send", {"performed": False, "error": "not performed"}, "failed"),
        ("http_request", {"status": 201, "headers": {}, "body": "created"}, "succeeded"),
        ("http_request", {"status": 503, "headers": {}, "body": "upstream"}, "unknown"),
        ("reminder_set", json.dumps({"success": True, "reminder": {"id": 1}}), "succeeded"),
        ("memory_write", {}, "unknown"),
        # A read-only-shaped payload under an unknown tool name is not success.
        ("unclassified_tool", {"results": [], "total_found": 0, "query": "q"}, "unknown"),
        (None, {"success": True}, "succeeded"),
        (None, {"performed": False}, "failed"),
        ("", {"success": True}, "succeeded"),
    ],
)
def test_material_tools_keep_the_conservative_contract(tool_name, result, expected):
    assert outcome_of_tool_result(tool_name, result) == expected
    # The material contract itself is unchanged.
    assert _outcome_of(result) == expected


def test_repeatable_tool_classification_is_unchanged():
    assert REPEATABLE_TOOLS == frozenset(
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
    for name in REPEATABLE_TOOLS:
        assert not is_material(name)
    assert is_material("memory_write")
    assert is_material("reminder_set")


def test_does_not_mutate_inputs():
    # A dict passed straight through is returned unchanged...
    result = {"query": "q", "results": [], "total_found": 0}
    original = copy.deepcopy(result)
    assert outcome_of_tool_result("web_search", result) == "succeeded"
    assert result == original

    # ...and a JSON payload is parsed from a rebindable local, never edited.
    payload = json.dumps({"sources": [], "total": 0, "next_offset": None})
    text_before = copy.deepcopy(payload)
    assert outcome_of_tool_result("web_fetch", payload) == "succeeded"
    assert payload == text_before

    # The material path is untouched as well.
    material = {"success": False, "error": "timeout"}
    original_material = copy.deepcopy(material)
    assert outcome_of_tool_result("notification_send", material) == "unknown"
    assert material == original_material


@pytest.mark.parametrize(
    ("tool_name", "result", "expected"),
    [
        # Negative and boolean counters are malformed, not successes.
        ("web_search", {"query": "q", "results": [], "total_found": -1}, "unknown"),
        ("web_search", {"query": "q", "results": [], "total_found": True}, "unknown"),
        ("web_fetch", {"sources": [], "total": -1, "next_offset": None}, "unknown"),
        ("web_fetch", {"sources": [], "total": True, "next_offset": None}, "unknown"),
        ("web_fetch", {"sources": [], "total": 0, "next_offset": -4}, "unknown"),
        ("reminder_list", {"count": -1, "reminders": []}, "unknown"),
        # Result-list items are typed, not any-list success.
        ("web_search", {"query": "q", "results": [None], "total_found": 0}, "unknown"),
        (
            "web_search",
            {"query": "q", "results": [{"title": "x", "description": "d"}], "total_found": 1},
            "unknown",
        ),
        ("web_fetch", {"sources": [None], "total": 1, "next_offset": 1}, "unknown"),
        (
            "web_fetch",
            {
                "sources": [
                    {
                        "snapshot_id": "not a uuid",
                        "title": "t",
                        "retrieved_at": "r",
                        "expires_at": "e",
                    }
                ],
                "total": 1,
                "next_offset": 1,
            },
            "unknown",
        ),
        # Malformed snapshot identity and cleared section counters.
        (
            "web_fetch",
            {
                "snapshot_id": "not uuid",
                "content": "abc",
                "start_char": 0,
                "end_char": 3,
                "content_length": 3,
                "total_chars": 5,
                "next_start_char": 3,
            },
            "unknown",
        ),
        (
            "web_fetch",
            {
                "snapshot_id": "0f9a1b2c-5555-4555-8555-555555555555",
                "content": "abc",
                "start_char": 0,
                "end_char": 3,
                "content_length": 3,
                "total_chars": -5,
                "next_start_char": 3,
            },
            "unknown",
        ),
        (
            "web_fetch",
            {"snapshot_id": "0f9a1b2c-5555-4555-8555-555555555555", "content": "abc"},
            "unknown",
        ),
        (
            "web_fetch",
            {
                "snapshot_id": "0f9a1b2c-5555-4555-8555-555555555555",
                "matches": [None],
                "next_start_char": None,
            },
            "unknown",
        ),
        (
            "web_fetch",
            {
                "snapshot_id": "0f9a1b2c-5555-4555-8555-555555555555",
                "matches": [{"start_char": -1, "end_char": 2}],
                "next_start_char": 2,
            },
            "unknown",
        ),
        # Memory tools: empty/whitespace/JSON-shaped/truncated/arbitrary text is
        # not evidence of success, and JSON-shaped evidence is unrecognized.
        ("memory_read", "   ", "unknown"),
        ("memory_read", "null", "unknown"),
        ("memory_read", '{"snapshot_id": "abc"', "unknown"),
        ("memory_read", "Something went wrong retrieving memories.", "unknown"),
        ("memory_read", "- [", "unknown"),
        ("memory_read", "- [] unfinished category", "unknown"),
        ("memory_reflect", "   ", "unknown"),
        ("memory_reflect", "null", "unknown"),
        ("memory_reflect", {"a": 1}, "unknown"),
        ("memory_reflect", "Such a thoughtful reflection on the topic.", "unknown"),
        ("memory_reflect", "True", "unknown"),
    ],
)
def test_repair_regressions_malformed_and_unrecognized_stay_unknown(tool_name, result, expected):
    assert outcome_of_tool_result(tool_name, result) == expected


@pytest.mark.parametrize("tool_name", sorted(REPEATABLE_TOOLS))
@pytest.mark.parametrize(
    "result",
    [{"error": "Tool execution failed: fixture"}, {"error": "Invalid JSON arguments: fixture"}],
)
def test_executor_errors_are_failed_for_all_recognized_reads(tool_name, result):
    assert outcome_of_tool_result(tool_name, result) == "failed"
    assert outcome_of_tool_result(tool_name, json.dumps(result)) == "failed"


@pytest.mark.parametrize(
    "payload",
    [
        {"content_length": 0},
        {"start_char": 9, "end_char": 1},
        {"start_char": 0, "end_char": 4},
        {"total_chars": 2},
        {"next_start_char": 2},
        {"complete": False},
        {"has_more": True},
        {"content": None, "matches": []},
        {"content": 3, "matches": []},
    ],
)
def test_contradictory_page_fields_are_unknown(payload):
    result = {
        "snapshot_id": "0f9a1b2c-1111-4111-8111-111111111111",
        "content": "abc",
        "content_length": 3,
        "total_chars": 3,
        "start_char": 0,
        "end_char": 3,
        "next_start_char": None,
        "complete": True,
        "has_more": False,
        **payload,
    }
    assert outcome_of_tool_result("web_fetch", result) == "unknown"


@pytest.mark.parametrize(
    ("tool_name", "result"),
    [
        ("web_search", {"query": "q", "results": [], "total_found": 1}),
        (
            "web_search",
            {"query": "q", "results": [{"url": "https://example.test"}], "total_found": 0},
        ),
        (
            "web_fetch",
            {"sources": [{"snapshot_id": "0f9a1b2c-1111-4111-8111-111111111111"}], "total": 0},
        ),
        (
            "web_fetch",
            {
                "snapshot_id": "0f9a1b2c-1111-4111-8111-111111111111",
                "matches": [{"start_char": 3, "end_char": 1}],
                "next_start_char": None,
            },
        ),
    ],
)
def test_contradictory_web_counts_and_match_offsets_are_unknown(tool_name, result):
    assert outcome_of_tool_result(tool_name, result) == "unknown"


@pytest.mark.parametrize(
    ("matches", "cursor", "expected"),
    [
        ([], None, "succeeded"),
        ([], 0, "unknown"),
        ([], 1, "unknown"),
        ([{"start_char": 3, "end_char": 5}], 0, "unknown"),
        ([{"start_char": 3, "end_char": 5}], 6, "unknown"),
        ([{"start_char": 3, "end_char": 5}], 5, "succeeded"),
        ([{"start_char": 3, "end_char": 5}], None, "succeeded"),
        ([{"start_char": 3, "end_char": 5}, {"start_char": 4, "end_char": 6}], 6, "unknown"),
        ([{"start_char": 3, "end_char": 5}, {"start_char": 1, "end_char": 2}], None, "unknown"),
        ([{"start_char": 3, "end_char": 5}, {"start_char": 5, "end_char": 7}], 7, "succeeded"),
        ([{"start_char": 3, "end_char": 5}, {"start_char": 8, "end_char": 10}], None, "succeeded"),
    ],
)
def test_find_pagination_agrees_with_ordered_matches(matches, cursor, expected):
    result = {
        "snapshot_id": "0f9a1b2c-1111-4111-8111-111111111111",
        "matches": matches,
        "next_start_char": cursor,
    }
    assert outcome_of_tool_result("web_fetch", result) == expected
    assert outcome_of_tool_result("web_fetch", json.dumps(result)) == expected


@pytest.mark.parametrize(
    "recognized",
    [
        # Producer formats: plain, slotted, and the history variant.
        "- [PROJECT] Deployed the fence",
        "- [FACT] slot=work Prefers Python",
        "- [FACT] slot=work [2025-01-01T00:00:00+00:00 -> 2026-01-01T00:00:00+00:00] "
        "Deployed the fence",
        "- [PROJECT] Deployed the fence\n- [FACT] Prefers Python",
    ],
)
def test_memory_read_recognized_line_formats_succeed(recognized):
    assert outcome_of_tool_result("memory_read", recognized) == "succeeded"
