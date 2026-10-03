"""Pure RESULT fixtures; no sockets, processes, browsers or persistence."""

from __future__ import annotations

import json

import pytest

from scripts.web_fetch_pilot_core import MAX_PAYLOAD, Frame, FrameType
from scripts.web_fetch_pilot_results import (
    FINAL_LIMIT,
    RESULT_LIMIT,
    ResultCollector,
    ResultError,
)

URL = "https://example.com/article?fixture=1"


def collector() -> ResultCollector:
    return ResultCollector(URL, ("example.com", "cdn.example.com"), "fixture-v1")


def result(body: bytes, tag: int = 1) -> Frame:
    return Frame(FrameType.RESULT, 0, bytes([tag]) + body)


def final(**overrides: object) -> Frame:
    record = {
        "status": "success",
        "original_url": URL,
        "final_url": URL,
        "title": "Fixture",
        "extraction_version": "fixture-v1",
    }
    record.update(overrides)  # type: ignore[arg-type]
    return result(json.dumps(record).encode(), 2)


def assert_latched(target: ResultCollector) -> None:
    assert not target.final_received
    for operation in (
        lambda: target.observe(result(b"later")),
        target.end_of_stream,
        target.candidate,
    ):
        with pytest.raises(ResultError):
            operation()


def test_split_utf8_and_provisional_completion() -> None:
    target = collector()
    for chunk in (b"article ", b"\xe2", b"\x82\xac"):
        assert target.observe(result(chunk))
    with pytest.raises(ResultError, match="incomplete"):
        target.candidate()
    target.observe(final(final_url="https://cdn.example.com:443/redirected"))
    with pytest.raises(ResultError, match="incomplete"):
        target.candidate()
    target.end_of_stream()
    candidate = target.candidate()
    assert candidate.content == "article €".encode()
    assert candidate.metadata.status == "success"
    counters = target.counters
    assert counters["frames"] == 4
    assert counters["wire_bytes"] == counters["result_payload_bytes"] + 4 * 12
    assert counters["result_payload_bytes"] == counters["source_and_metadata_bytes"] + 4
    counters["frames"] = 0
    assert target.counters["frames"] == 4
    target.abort()
    assert_latched(target)


@pytest.mark.parametrize("status", ["blocked", "error"])
def test_failure_status_without_content_is_candidate_not_success(status: str) -> None:
    target = collector()
    target.observe(final(status=status))
    target.end_of_stream()
    assert target.candidate().content == b""
    assert target.candidate().metadata.status == status


@pytest.mark.parametrize(
    "frame",
    [
        result(b"", 1),
        result(b"x", 3),
        result(b"\xff"),
        Frame(FrameType.RESULT, 1, b"\x01x"),
        Frame(FrameType.RESULT, 0, b""),
        Frame(FrameType.DATA, True, b"x"),
        Frame(8, 0, b"\x01x"),  # type: ignore[arg-type]
        Frame(FrameType.RESULT, 0, bytearray(b"\x01x")),  # type: ignore[arg-type]
        Frame(FrameType.OPEN_OK, 1, b""),
        Frame(FrameType.OPEN_ERROR, 1, b"denied"),
    ],
)
def test_invalid_frame_or_chunk_latches(frame: Frame) -> None:
    target = collector()
    with pytest.raises(ResultError):
        target.observe(frame)
    assert_latched(target)


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{}",
        b"[]",
        b"null",
        b"[" * 4000,
        b"\xff",
        b"{}junk",
        b'{"status":"success","status":"blocked"}',
        b'{"bad":NaN}',
        b" " * (FINAL_LIMIT + 1),
    ],
)
def test_bad_final_metadata_latches(body: bytes) -> None:
    target = collector()
    with pytest.raises(ResultError):
        target.observe(result(body, 2))
    assert_latched(target)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "done"},
        {"status": True},
        {"title": None},
        {"title": "\ud800"},
        {"extra": "not-policy"},
        {"original_url": "https://example.com/other"},
        {"extraction_version": "page-supplied-v2"},
        {"final_url": "https://evil.example/page"},
        {"final_url": "https://example.com:444/page"},
        {"final_url": "https://example.com@evil.example/page"},
        {"final_url": "https://example.com./page"},
        {"final_url": "https://EXAMPLE.com/page"},
        {"final_url": "https://127.0.0.1/page"},
        {"final_url": "http://example.com/page"},
        {"final_url": "https://example.com/page#fragment"},
        {"final_url": "\nhttps://example.com/page"},
        {"final_url": "https://exam\tple.com/page"},
    ],
)
def test_bad_fixed_fields_and_untrusted_provenance(overrides: dict[str, object]) -> None:
    target = collector()
    target.observe(result(b"article"))
    with pytest.raises(ResultError):
        target.observe(final(**overrides))
    assert_latched(target)


@pytest.mark.parametrize("content", [None, b"  \t\r\n", b"\xe2\x82"])
def test_empty_or_incomplete_utf8_is_not_success(content: bytes | None) -> None:
    target = collector()
    if content is not None:
        target.observe(result(content))
    with pytest.raises(ResultError):
        target.observe(final())
    assert_latched(target)


@pytest.mark.parametrize("status", ["blocked", "error"])
def test_failure_cannot_include_content(status: str) -> None:
    target = collector()
    target.observe(result(b"article"))
    with pytest.raises(ResultError, match="failure status"):
        target.observe(final(status=status))
    assert_latched(target)


@pytest.mark.parametrize(
    "later", [result(b"extra"), final(), Frame(FrameType.OPEN, 1, b"example.com:443")]
)
def test_no_result_or_open_after_final(later: Frame) -> None:
    target = collector()
    target.observe(result(b"article"))
    target.observe(final())
    with pytest.raises(ResultError):
        target.observe(later)
    assert_latched(target)


def test_eof_without_final_and_frames_after_eof_fail_closed() -> None:
    missing = collector()
    missing.observe(result(b"article"))
    with pytest.raises(ResultError, match="without final"):
        missing.end_of_stream()
    assert_latched(missing)
    target = collector()
    target.observe(final(status="error"))
    assert not target.observe(Frame(FrameType.CLOSE, 1, b""))
    target.end_of_stream()
    with pytest.raises(ResultError):
        target.observe(Frame(FrameType.CLOSE, 1, b""))
    assert_latched(target)


def _content(target: ResultCollector, length: int) -> int:
    count = 0
    while length:
        size = min(length, MAX_PAYLOAD - 1)
        target.observe(result(b"x" * size))
        length -= size
        count += 1
    return count


def test_strict_payload_limit_exact_and_final_metadata_accounting() -> None:
    closing = final()
    # Chunks + final tags count, not merely text + JSON: 64 chunks needed here.
    chunk_count = 64
    length = RESULT_LIMIT - len(closing.payload) - chunk_count
    exact = collector()
    assert _content(exact, length) == chunk_count
    exact.observe(closing)
    exact.end_of_stream()
    assert exact.counters["result_payload_bytes"] == RESULT_LIMIT
    assert len(exact.candidate().content) == length
    overflow = collector()
    _content(overflow, length + 1)
    with pytest.raises(ResultError, match="payload limit"):
        overflow.observe(closing)
    assert_latched(overflow)
    body_only = collector()
    _content(body_only, RESULT_LIMIT - len(closing.payload) + 1)
    with pytest.raises(ResultError, match="payload limit"):
        body_only.observe(closing)


def test_all_browser_frames_share_quota_and_wire_counter() -> None:
    target = collector()
    for _ in range(4095):
        assert not target.observe(Frame(FrameType.DATA, 1, b""))
    target.observe(final(status="error"))
    assert target.counters["frames"] == 4096
    with pytest.raises(ResultError, match="frame limit"):
        target.observe(Frame(FrameType.CLOSE, 1, b""))
    assert_latched(target)


def test_browser_data_limit_never_resets_on_new_stream() -> None:
    target = collector()
    for _ in range(2048):
        target.observe(Frame(FrameType.DATA, 1, b"x" * MAX_PAYLOAD))
    assert target.counters["browser_data_bytes"] == 32 * 1024 * 1024
    with pytest.raises(ResultError, match="DATA limit"):
        target.observe(Frame(FrameType.DATA, 2, b"x"))
    assert_latched(target)


@pytest.mark.parametrize(
    "url, hosts, version",
    [
        ("http://example.com/a", ("example.com",), "v1"),
        (URL, (), "v1"),
        (URL, ("EVIL.com",), "v1"),
        (URL, ("example.com",), ""),
        (URL, ("example.com",), "x" * 4097),
    ],
)
def test_trusted_configuration_invalid(url: str, hosts: tuple[str, ...], version: str) -> None:
    with pytest.raises((ResultError, ValueError)):
        ResultCollector(url, hosts, version)
