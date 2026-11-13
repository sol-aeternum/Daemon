"""Pure supervisor-side pilot RESULT collector, not a completion authority.

All browser frames must pass observe() BEFORE tunnel forwarding. Only RESULT is
consumed here; it must never reach the gateway. A candidate remains untrusted
page data, not article success: child exits, containment, extraction classification
and owned-resource teardown still need independent supervisor verification.
No I/O, navigation, persistence, subprocesses or runtime entrypoint are provided.
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from scripts.web_fetch_pilot_core import (
    HEADER_LEN,
    MAX_FRAMES,
    Frame,
    FrameParser,
    FrameType,
    encode_frame,
    require_canonical_hostname,
)

RESULT_LIMIT = 1024 * 1024
FINAL_LIMIT = 4096
DATA_LIMIT = 32 * 1024 * 1024
_FIELDS = frozenset({"status", "original_url", "final_url", "title", "extraction_version"})
_BROWSER_TYPES = frozenset(
    {
        FrameType.OPEN,
        FrameType.DATA,
        FrameType.WINDOW,
        FrameType.HALF_CLOSE,
        FrameType.CLOSE,
        FrameType.RESULT,
    }
)


class ResultError(Exception):
    """Terminal protocol failure; error messages never contain page data."""


@dataclass(frozen=True)
class ResultMetadata:
    status: Literal["success", "blocked", "error"]
    original_url: str
    final_url: str
    title: str
    extraction_version: str


@dataclass(frozen=True)
class ResultCandidate:
    """A bounded child claim; never proof of fetch or cleanup success."""

    content: bytes
    metadata: ResultMetadata


def _url_host(value: str) -> str:
    # Strict URL parsing must not silently strip tabs/newlines or leading C0s.
    if type(value) is not str or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ResultError("invalid result URL")
    try:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.fragment:
            raise ResultError("invalid result URL")
        authority = parsed.netloc
        host = authority[:-4] if authority.endswith(":443") else authority
        require_canonical_hostname(host)
        if authority not in (host, host + ":443"):
            raise ResultError("invalid result authority")
    except (ValueError, UnicodeError) as exc:
        raise ResultError("invalid result URL") from exc
    return host


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ResultError("duplicate final field")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ResultError("non-JSON final constant")


class ResultCollector:
    """Single-use, fail-latched bounded collector with shared browser counters.

    Construct from trusted immutable run values, never child-supplied policy.
    observe() counts ALL browser frames (including RESULT) and records wire bytes;
    a future supervisor must separately count the gateway direction and aggregate
    both directions' DATA. This object counts only browser DATA, not socket bytes.
    Retaining a candidate outside this object transfers responsibility to discard
    it on subsequent failure; no downstream persistence is authorized here.
    """

    def __init__(
        self, original_url: str, allowed_hosts: Sequence[str], extraction_version: str
    ) -> None:
        if type(allowed_hosts) not in (tuple, list) or not 0 < len(allowed_hosts) <= 40:
            raise ValueError("bounded trusted host manifest required")
        hosts: set[str] = set()
        for host in allowed_hosts:
            if type(host) is not str:
                raise ValueError("canonical trusted host required")
            hosts.add(require_canonical_hostname(host))
        if _url_host(original_url) not in hosts:
            raise ValueError("original URL outside trusted manifest")
        if (
            type(extraction_version) is not str
            or not extraction_version.strip()
            or len(extraction_version.encode("utf-8")) > FINAL_LIMIT
        ):
            raise ValueError("bounded trusted extraction version required")
        self._original = original_url
        self._hosts = frozenset(hosts)
        self._version = extraction_version
        self._content = bytearray()
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._nonwhite = False
        self._metadata: ResultMetadata | None = None
        self._failed: str | None = None
        self._eof = False
        self._frames = 0
        self._wire_bytes = 0
        self._result_bytes = 0
        self._source_bytes = 0
        self._data_bytes = 0

    @property
    def counters(self) -> dict[str, int]:
        return {
            "frames": self._frames,
            "wire_bytes": self._wire_bytes,
            "result_payload_bytes": self._result_bytes,
            "source_and_metadata_bytes": self._source_bytes,
            "browser_data_bytes": self._data_bytes,
        }

    @property
    def final_received(self) -> bool:
        return self._failed is None and self._metadata is not None

    def _check(self) -> None:
        if self._failed is not None:
            raise ResultError(self._failed)

    def _fail(self, reason: str) -> ResultError:
        self._failed = reason
        self._content.clear()
        self._decoder.reset()
        self._metadata = None
        return ResultError(reason)

    def abort(self) -> None:
        """Discard buffered data on supervisor failure, even after a final record."""
        if self._failed is None:
            self._fail("supervisor aborted result")

    def observe(self, frame: Frame) -> bool:
        """Validate/count a browser frame; return True only when RESULT consumed."""
        self._check()
        try:
            if self._eof:
                raise ResultError("frame after browser EOF")
            if (
                type(frame) is not Frame
                or type(frame.frame_type) is not FrameType
                or type(frame.stream_id) is not int
                or type(frame.payload) is not bytes
                or frame.frame_type not in _BROWSER_TYPES
            ):
                raise ResultError("invalid browser frame")
            raw = encode_frame(frame.frame_type, frame.stream_id, frame.payload)
            parser = FrameParser()
            list(parser.feed(raw))
            parser.end_of_stream()
            if self._frames >= MAX_FRAMES:
                raise ResultError("browser frame limit")
            if self._metadata is not None and frame.frame_type in (
                FrameType.RESULT,
                FrameType.OPEN,
            ):
                raise ResultError("result or OPEN after final record")
            self._frames += 1
            self._wire_bytes += HEADER_LEN + len(frame.payload)
            if frame.frame_type is FrameType.DATA:
                if self._data_bytes + len(frame.payload) > DATA_LIMIT:
                    raise ResultError("browser DATA limit")
                self._data_bytes += len(frame.payload)
            if frame.frame_type is not FrameType.RESULT:
                return False
            self._consume(frame.payload)
            return True
        except ResultError as exc:
            raise self._fail(str(exc)) from None
        except Exception:
            raise self._fail("invalid result framing or encoding") from None

    def _consume(self, payload: bytes) -> None:
        tag, body = payload[0], payload[1:]
        if self._result_bytes + len(payload) > RESULT_LIMIT:
            raise ResultError("RESULT payload limit")
        if self._source_bytes + len(body) > RESULT_LIMIT:
            raise ResultError("source and metadata limit")
        self._result_bytes += len(payload)
        self._source_bytes += len(body)
        if tag == 1:
            if not body:
                raise ResultError("empty content chunk")
            decoded = self._decoder.decode(body, final=False)
            self._nonwhite = self._nonwhite or bool(decoded.strip())
            self._content.extend(body)
        elif tag == 2:
            if not body or len(body) > FINAL_LIMIT:
                raise ResultError("final metadata limit")
            self._decoder.decode(b"", final=True)
            record = json.loads(
                body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant
            )
            if type(record) is not dict or record.keys() != _FIELDS:
                raise ResultError("invalid final fields")
            if any(type(value) is not str for value in record.values()):
                raise ResultError("invalid final field types")
            if record["status"] not in ("success", "blocked", "error"):
                raise ResultError("invalid final status")
            # Reject escaped lone surrogates; JSON's decoder otherwise accepts them.
            for value in record.values():
                value.encode("utf-8", errors="strict")
            if record["original_url"] != self._original:
                raise ResultError("original URL mismatch")
            if _url_host(record["final_url"]) not in self._hosts:
                raise ResultError("final URL outside trusted manifest")
            if record["extraction_version"] != self._version:
                raise ResultError("extraction version mismatch")
            if record["status"] == "success":
                if not self._nonwhite:
                    raise ResultError("empty success content")
            elif self._content:
                raise ResultError("content accompanying failure status")
            self._metadata = ResultMetadata(**record)
        else:
            raise ResultError("unknown RESULT subtype")

    def end_of_stream(self) -> None:
        """Require a final child claim; EOF alone is never article success."""
        self._check()
        if self._metadata is None:
            raise self._fail("browser EOF without final record")
        self._eof = True

    def candidate(self) -> ResultCandidate:
        """Return data only after final + EOF; caller still owns completion checks."""
        self._check()
        if self._metadata is None or not self._eof:
            raise ResultError("result candidate incomplete")
        return ResultCandidate(bytes(self._content), self._metadata)
