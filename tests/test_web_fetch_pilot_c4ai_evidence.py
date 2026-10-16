"""Controlled HTTP outcomes: no sockets, containers, images or live URLs."""

import copy
import io
import json
import urllib.error
from email.message import Message
from unittest.mock import patch

import pytest

from scripts import web_fetch_pilot_c4ai_evidence as client


def run_reply(status: int, body: bytes, url: str = client.PRIVATE[0]) -> dict:
    error = urllib.error.HTTPError(
        client.BASE + "/crawl", status, "synthetic", Message(), io.BytesIO(body)
    )
    with patch.object(client.urllib.request, "urlopen", side_effect=error) as opened:
        evidence = client.crawl("synthetic-token", url)
    request = opened.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer synthetic-token"
    assert json.loads(request.data)["urls"] == [url]
    assert error.closed
    return evidence


def good_record() -> dict:
    denial = run_reply(400, json.dumps(client.POLICY_DENIAL).encode())
    allowed = run_reply(
        200,
        json.dumps(
            {
                "success": True,
                "results": [
                    {
                        "url": client.CONTROL_URL,
                        "success": True,
                        "markdown": {"raw_markdown": client.CONTROL_MARKER + "\n"},
                    }
                ],
            }
        ).encode(),
        client.CONTROL_URL,
    )
    return {
        "health": 200,
        "unauth_crawl": 401,
        "allowed": allowed,
        "private": {url: dict(denial) for url in client.PRIVATE},
    }


def test_exact_structured_denial_and_authenticated_inline_control() -> None:
    record = good_record()
    client.require_application_evidence(record)
    assert "body" not in str(record) and "error_message" not in str(record)
    assert record["allowed"]["markdown_chars"] > 0


@pytest.mark.parametrize(
    "status,body",
    [
        (401, client.POLICY_DENIAL),
        (500, client.POLICY_DENIAL),
        (200, client.POLICY_DENIAL),
        (403, client.POLICY_DENIAL),
        (400, {"detail": "URL blocked"}),
        (400, {"detail": "bad request"}),
        (400, {**client.POLICY_DENIAL, "extra": True}),
        (400, None),
        (400, []),
        (400, {}),
        (200, {"success": False}),
        (200, {"success": True, "results": []}),
        (200, {"success": True, "results": [None]}),
        (200, {"success": True, "results": [{"success": False}]}),
    ],
)
def test_unrelated_http_failures_never_qualify(status: int, body: object) -> None:
    record = good_record()
    probe = run_reply(status, json.dumps(body).encode())
    assert probe["policy_denied"] is False
    record["private"][client.PRIVATE[0]] = probe
    with pytest.raises(ValueError, match="positive application policy denial"):
        client.require_application_evidence(record)


@pytest.mark.parametrize(
    "raw,outcome",
    [
        (b"", "malformed"),
        (b"{", "malformed"),
        (b"\xff", "malformed"),
        (b"x" * (client.MAX_RESPONSE + 1), "oversize"),
    ],
)
def test_malformed_or_oversize_http_error_is_inconclusive(raw: bytes, outcome: str) -> None:
    probe = run_reply(400, raw)
    assert probe["outcome"] == outcome and not probe["policy_denied"]


@pytest.mark.parametrize(
    "error", [ConnectionError(), TimeoutError(), urllib.error.URLError("offline")]
)
def test_transport_failure_cannot_qualify(error: OSError) -> None:
    record = good_record()
    with patch.object(client.urllib.request, "urlopen", side_effect=error):
        probe = client.crawl("synthetic-token", client.PRIVATE[0])
    assert probe["outcome"] == "transport" and probe["status"] is None
    record["private"][client.PRIVATE[0]] = probe
    with pytest.raises(ValueError):
        client.require_application_evidence(record)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.__setitem__("private", {}),
        lambda r: r["private"].pop(client.PRIVATE[0]),
        lambda r: r["private"].__setitem__("http://unexpected/", {}),
        lambda r: r.pop("allowed"),
        lambda r: r["allowed"].__setitem__("success", False),
        lambda r: r["allowed"].__setitem__("control_match", False),
        lambda r: r.__setitem__("health", 500),
        lambda r: r.__setitem__("unauth_crawl", 200),
    ],
)
def test_required_targets_and_controls_cannot_be_missing(mutate) -> None:
    record = copy.deepcopy(good_record())
    mutate(record)
    with pytest.raises(ValueError):
        client.require_application_evidence(record)


@pytest.mark.parametrize(
    "result",
    [
        {"url": "wrong", "success": True, "markdown": client.CONTROL_MARKER},
        {"url": client.CONTROL_URL, "success": 1, "markdown": client.CONTROL_MARKER},
        {"url": client.CONTROL_URL, "success": True, "markdown": "wrong content"},
        {"url": client.CONTROL_URL, "success": True, "markdown": None},
    ],
)
def test_allowed_control_must_be_exact_successful_synthetic_result(result: dict) -> None:
    record = good_record()
    record["allowed"] = run_reply(
        200, json.dumps({"success": True, "results": [result]}).encode(), client.CONTROL_URL
    )
    with pytest.raises(ValueError):
        client.require_application_evidence(record)


def test_success_http_response_uses_same_bounded_decoder() -> None:
    class Response(io.BytesIO):
        status = 400

    response = Response(json.dumps(client.POLICY_DENIAL).encode())
    with patch.object(client.urllib.request, "urlopen", return_value=response):
        assert client.crawl("token", client.PRIVATE[0])["policy_denied"] is True
    assert response.closed
