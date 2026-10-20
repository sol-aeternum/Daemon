"""Stdlib-only Phase 1 client/evidence contract; no I/O on import.

This exact source is sent to the isolated client. Fake urlopen tests exercise
HTTPError bodies and transport handling without sockets or images.
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

BASE = "http://reader.pilot.test:11235"
MAX_RESPONSE = 4 * 1024 * 1024
POLICY_DENIAL = {"detail": "URL blocked (SSRF protection): URL blocked"}
PRIVATE = (
    "http://169.254.169.254/latest/meta-data/",
    "http://127.0.0.1:6379/",
    "http://10.0.0.1/",
    "http://[::1]/",
)
CONTROL_MARKER = "daemonpilotallowedfixture8a2fe9"
CONTROL_URL = "raw:<html><body><p>" + CONTROL_MARKER + "</p></body></html>"


def response_record(status: int, raw: bytes) -> dict:
    if len(raw) > MAX_RESPONSE:
        return {"status": status, "outcome": "oversize", "body": None}
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return {"status": status, "outcome": "malformed", "body": None}
    return {"status": status, "outcome": "http", "body": body}


def call(
    token: str, path: str, payload: dict | None = None, *, auth: bool = True, timeout: int = 120
) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        BASE + path, data=data, method="GET" if data is None else "POST"
    )
    request.add_header("Content-Type", "application/json")
    if auth:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response_record(response.status, response.read(MAX_RESPONSE + 1))
    except urllib.error.HTTPError as exc:
        with exc:
            return response_record(exc.code, exc.read(MAX_RESPONSE + 1))
    except OSError as exc:
        return {"status": None, "outcome": "transport", "error": type(exc).__name__, "body": None}


def crawl(token: str, url: str) -> dict:
    reply = call(token, "/crawl", {"urls": [url], "browser_config": {}, "crawler_config": {}})
    evidence = {
        "status": reply["status"],
        "outcome": reply["outcome"],
        "policy_denied": False,
        "success": None,
        "markdown_chars": 0,
        "control_match": False,
    }
    if reply["outcome"] != "http":
        return evidence
    if type(reply["status"]) is int and reply["status"] == 400 and reply["body"] == POLICY_DENIAL:
        evidence["policy_denied"] = True
        return evidence
    body = reply["body"]
    if reply["status"] != 200 or type(body) is not dict or body.get("success") is not True:
        evidence["outcome"] = "unrelated_http"
        return evidence
    results = body.get("results")
    if type(results) is not list or len(results) != 1 or type(results[0]) is not dict:
        evidence["outcome"] = "malformed_result"
        return evidence
    result = results[0]
    if result.get("url") != url or type(result.get("success")) is not bool:
        evidence["outcome"] = "malformed_result"
        return evidence
    markdown = result.get("markdown")
    text = markdown.get("raw_markdown") if type(markdown) is dict else markdown
    evidence["success"] = result["success"]
    evidence["markdown_chars"] = len(text) if type(text) is str else 0
    # Inline synthetic reader/auth control, NOT a network/TLS success control.
    evidence["control_match"] = (
        url == CONTROL_URL and type(text) is str and text.strip() == CONTROL_MARKER
    )
    if type(text) is str:
        evidence["markdown_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    return evidence


def require_application_evidence(record: object) -> None:
    """Fail on missing controls/probes or any inconclusive result."""
    if type(record) is not dict or record.get("health") != 200 or record.get("unauth_crawl") != 401:
        raise ValueError("reader health/auth control failed")
    control = record.get("allowed")
    if (
        type(control) is not dict
        or control.get("status") != 200
        or control.get("outcome") != "http"
        or control.get("success") is not True
        or control.get("control_match") is not True
    ):
        raise ValueError("authenticated inline reader control failed")
    probes = record.get("private")
    if type(probes) is not dict or set(probes) != set(PRIVATE):
        raise ValueError("private target evidence incomplete")
    for probe in probes.values():
        if (
            type(probe) is not dict
            or type(probe.get("status")) is not int
            or probe.get("status") != 400
            or probe.get("outcome") != "http"
            or probe.get("policy_denied") is not True
        ):
            raise ValueError("private target lacks positive application policy denial")


def main() -> None:
    signal.alarm(280)
    sys.dont_write_bytecode = True
    token = sys.stdin.readline().strip()
    start = time.monotonic()
    while True:
        health = call(token, "/health", timeout=5)
        if health["status"] == 200 or time.monotonic() - start > 150:
            break
        time.sleep(2)
    record = {
        "health": health["status"],
        "health_wait_s": round(time.monotonic() - start, 1),
        "unauth_crawl": call(token, "/crawl", {"urls": ["https://openai.com/"]}, auth=False)[
            "status"
        ],
        "allowed": crawl(token, CONTROL_URL),
        "fixture": crawl(token, sys.argv[1]),
        "private": {url: crawl(token, url) for url in PRIVATE},
    }
    os.write(1, json.dumps(record, sort_keys=True).encode() + b"\n")
