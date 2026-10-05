"""Offline fixture/scoring and cumulative ledger support; no provider dispatch."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
import fcntl
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/azure_embedding_retrieval.json"
PARENT_SHA256 = "03b2d55fd68f6515de0532684dcb73e7faf6bdb53d8056828b216104ffe80232"
MAX_REQUESTS, MAX_INPUT = 24, 100_000
MAX_USD = Decimal("0.25")
PRICE_PER_MILLION = Decimal("0.02")
CRITERIA = {"queries": 16, "minimum_top1": 12, "minimum_top3": 15}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def reservation_cost(tokens: int) -> Decimal:
    if type(tokens) is not int or tokens <= 0:
        raise ValueError("Invalid token reservation")
    return Decimal(tokens) * PRICE_PER_MILLION / 1_000_000 * Decimal("1.10")


def parent_reservations(path: Path) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PARENT_SHA256:
        raise ValueError("Original embedding ledger changed")
    parent = json.loads(raw)
    if parent.get("approval") != "memory-445-20261004-fictional-only":
        raise ValueError("Wrong original embedding approval")
    attempts = parent.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("Corrupt original reservations")
    reservations = []
    for attempt in attempts:
        tokens, usd = attempt.get("reserved_tokens"), Decimal(attempt["reserved_usd"])
        if type(tokens) is not int or tokens <= 0 or not usd.is_finite() or usd <= 0:
            raise ValueError("Corrupt original reservation")
        reservations.append({"input": tokens, "usd": str(usd)})
    if (
        len(reservations) != 9
        or sum(row["input"] for row in reservations) != 75_711
        or sum((Decimal(row["usd"]) for row in reservations), Decimal(0)) != Decimal("0.01804121")
    ):
        raise ValueError("Original reservation totals differ from reviewed evidence")
    return reservations


@contextmanager
def cumulative_ledger(parent: Path):
    """Hold both cooperative locks, including original-runner exclusion."""
    parent = parent.resolve(strict=True)
    child = parent.with_name(parent.name + ".azure-small.json")
    with (
        parent.with_suffix(parent.suffix + ".lock").open("a") as parent_lock,
        child.with_suffix(child.suffix + ".lock").open("a") as child_lock,
    ):
        fcntl.flock(parent_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(child_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield child, parent_reservations(parent)


def reserve(ledger: dict[str, Any], batch: str, tokens: int) -> dict[str, Any]:
    cost = reservation_cost(tokens)
    previous = ledger["frozen"]["parent_reservations"] + ledger["attempts"]
    if (
        len(previous) >= MAX_REQUESTS
        or sum(row["input"] for row in previous) + tokens > MAX_INPUT
        or sum((Decimal(row["usd"]) for row in previous), Decimal(0)) + cost > MAX_USD
    ):
        raise ValueError("Cumulative embedding approval cap reached")
    if any(row.get("batch") == batch for row in ledger["attempts"]):
        raise ValueError("Batch already attempted; no replay")
    attempt = {
        "batch": batch,
        "input": tokens,
        "usd": str(cost),
        "at": datetime.now(UTC).isoformat(),
        "outcome": "uncertain",
    }
    ledger["attempts"].append(attempt)
    return attempt


def open_followup(path: Path, frozen: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on changed identity, reservations or any incomplete run."""
    ledger = json.loads(path.read_text()) if path.exists() else {"frozen": frozen, "attempts": []}
    if ledger.get("frozen") != frozen or not isinstance(ledger.get("attempts"), list):
        raise ValueError("Follow-up identity changed")
    checked: dict[str, Any] = {"frozen": frozen, "attempts": []}
    for attempt in ledger["attempts"]:
        expected = reserve(checked, attempt["batch"], attempt["input"])
        if Decimal(attempt["usd"]) != Decimal(expected["usd"]):
            raise ValueError("Corrupt follow-up reservation")
        if attempt.get("outcome") != "valid":
            raise ValueError("Invalid or uncertain run cannot resume")
        if not isinstance(attempt.get("receipt"), dict):
            raise ValueError("Completed attempt has no receipt")
    return ledger


def write_ledger(path: Path, value: dict[str, Any]) -> None:
    import os

    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def reserve_durable(path: Path, ledger: dict[str, Any], batch: str, tokens: int) -> dict[str, Any]:
    """Return permission to send only after the full reservation is fsynced.

    The caller must hold ``cumulative_ledger`` for the entire run. A failed write
    raises before this function returns; the caller must not dispatch or retry.
    """
    attempt = reserve(ledger, batch, tokens)
    write_ledger(path, ledger)
    return attempt


def eligible(document: dict[str, Any], fixture: dict[str, Any]) -> bool:
    return (
        document.get("owner", fixture["owner"]) == fixture["owner"]
        and document.get("space", fixture["space"]) == fixture["space"]
        and document.get("status", "active") == "active"
        and document.get("valid_to") is None
        and document.get("local_only", False) is False
    )


def fixtures(path: Path = FIXTURE) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fixture = json.loads(path.read_text())
    if (
        fixture.get("version") != 1
        or fixture.get("fictional") is not True
        or fixture.get("split") != "frozen-heldout"
        or fixture.get("criteria") != CRITERIA
        or fixture.get("owner") != "fictional-owner"
        or fixture.get("space") != "candidate"
    ):
        raise ValueError("Not the frozen fictional retrieval fixture")
    scenarios = fixture.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != CRITERIA["queries"]:
        raise ValueError("Wrong query count")
    if len({row["id"] for row in scenarios}) != len(scenarios):
        raise ValueError("Duplicate query identity")
    documents = [document for scenario in scenarios for document in scenario["documents"]]
    if len({row["id"] for row in documents}) != len(documents):
        raise ValueError("Duplicate document identity")
    for text in [row["query"] for row in scenarios] + [row["text"] for row in documents]:
        if not isinstance(text, str) or not text.strip() or len(text) > 2_000:
            raise ValueError("Invalid fixture text")
    for scenario in scenarios:
        matches = [row for row in scenario["documents"] if row["id"] == scenario["relevant"]]
        if len(matches) != 1 or not eligible(matches[0], fixture):
            raise ValueError("Relevant document is not eligible")
    return fixture, [document for document in documents if eligible(document, fixture)]


def cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("Vector shape mismatch")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in left + right):
        raise ValueError("Nonfinite vector")
    left_norm, right_norm = math.hypot(*left), math.hypot(*right)
    if not math.isfinite(left_norm + right_norm) or left_norm == 0 or right_norm == 0:
        raise ValueError("Invalid vector norm")
    return math.fsum((a / left_norm) * (b / right_norm) for a, b in zip(left, right, strict=True))


def score(
    fixture: dict[str, Any],
    documents: list[dict[str, Any]],
    document_vectors: list[list[float]],
    query_vectors: list[list[float]],
    *,
    query_texts: list[str],
) -> dict[str, Any]:
    expected = [
        row
        for scenario in fixture["scenarios"]
        for row in scenario["documents"]
        if eligible(row, fixture)
    ]
    if documents != expected or len(document_vectors) != len(documents):
        raise ValueError("Document set differs from eligible corpus")
    if len(query_vectors) != len(fixture["scenarios"]):
        raise ValueError("Query vector count mismatch")
    if query_texts != [scenario["query"] for scenario in fixture["scenarios"]]:
        raise ValueError("Query request manifest differs from frozen order")
    rankings, top1, top3 = [], 0, 0
    for scenario, query in zip(fixture["scenarios"], query_vectors, strict=True):
        # Stable id tie-breaking avoids an annotated-first positional advantage.
        ordered = sorted(
            (
                (cosine(query, vector), row["id"])
                for row, vector in zip(documents, document_vectors, strict=True)
            ),
            key=lambda pair: (-pair[0], pair[1]),
        )
        ids = [identity for _, identity in ordered]
        top1 += ids[0] == scenario["relevant"]
        top3 += scenario["relevant"] in ids[:3]
        rankings.append(
            {"query": scenario["id"], "relevant": scenario["relevant"], "top3": ids[:3]}
        )
    return {
        "top1": top1,
        "top3": top3,
        "queries": len(query_vectors),
        "rankings": rankings,
        "retrieval_pass": top1 >= CRITERIA["minimum_top1"] and top3 >= CRITERIA["minimum_top3"],
        "note": "Bounded retrieval screen; not semantic merge authority or universal quality proof.",
    }
