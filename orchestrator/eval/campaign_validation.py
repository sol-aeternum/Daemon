"""Offline, fail-closed qualification for the approved fifty-question campaign."""

from __future__ import annotations

from itertools import combinations
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from orchestrator.eval.campaign_guard import CampaignStopped
from tests.longmemeval.evaluate import CATEGORY_MAP
from tests.longmemeval.official_judge import (
    require_complete_official_coverage,
    score_dual_judgments,
)

DEV_SHA256 = "6612e3a03c79b92bab2f1750d4661908e3dc37359715b7fb6b62c6f758bddb34"
SNAPSHOT_SHA256 = "3fd65416fdc24e845a184d7b8d3e0828811e048cd8341a39fe285fc14dc1f5a1"
STATE_SHA256 = "251dd9cfed9a20fe6c719f277359a8eecf9505aa0c6d2441ca954e5838fa5c0a"
RETRIEVAL_MODE = "embedding:openrouter:azure:openai/text-embedding-3-small:1024"


def digest_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def expected_corpus(pins: Mapping[str, Any]) -> tuple[str, str]:
    """(snapshot, protected-state) SHA-256 a plan's arms must restore and evaluate.

    Plans without a historical remap use the original qualified corpus constants,
    so every earlier campaign verifies unchanged.
    """
    remap = pins.get("historical_remap")
    if remap is None:
        return SNAPSHOT_SHA256, STATE_SHA256
    return str(remap["snapshot_sha256"]), str(remap["state_sha256"])


def require(condition: object, reason: str) -> None:
    if not condition:
        raise CampaignStopped(reason)


def load_dev(path: Path) -> list[dict[str, Any]]:
    require(digest_file(path) == DEV_SHA256, "Frozen dev dataset hash differs")
    rows = json.loads(path.read_bytes())
    require(isinstance(rows, list) and len(rows) == 50, "Invalid frozen dev dataset")
    require(len({row["question_id"] for row in rows}) == 50, "Duplicate dev identity")
    return rows


def qualification(
    rows: list[dict[str, Any]],
    dataset: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    checkpoint: Mapping[str, Any],
    score: Mapping[str, Any],
    *,
    corpus: tuple[str, str] = (SNAPSHOT_SHA256, STATE_SHA256),
) -> dict[str, Any]:
    """Check real artifacts, never accept a bare success flag or generic score."""
    snapshot_sha256, state_sha256 = corpus
    ids = [str(row["question_id"]) for row in dataset]
    official = require_complete_official_coverage(rows, ids)
    dual = score_dual_judgments(rows)
    expected = {row["question_id"]: row for row in dataset}
    for row in rows:
        source = expected[row["question_id"]]
        require(row.get("question") == source["question"], "Question text differs from freeze")
        require(row.get("reference") == source["answer"], "Reference differs from freeze")
        require(row.get("question_type") == source["question_type"], "Question type differs")
        require(row.get("category") == CATEGORY_MAP[source["question_type"]], "Category differs")
        require(not row.get("error"), "Canonical question has an error")
        require(row.get("official_judge_status") == "completed", "Official judge incomplete")
        require(
            row.get("judgment") in {"correct", "incorrect", "partially_correct"},
            "Generic label missing",
        )
    phases = manifest.get("phases", {})
    evaluate, scored = phases.get("evaluate", {}), phases.get("score", {})
    require(evaluate.get("official_judgments") == official, "Evaluate official summary differs")
    require(scored.get("dual_judgments") == dual, "Score manifest dual summary differs")
    require(score.get("dual_judgments") == dual, "Score artifact dual summary differs")
    require(scored.get("error_count") == score.get("error_count") == 0, "Score errors")
    require(scored.get("result_count") == score.get("result_count") == 50, "Score count differs")
    for phase in ("ingest", "evaluate", "score"):
        require(
            checkpoint.get("phases", {}).get(phase, {}).get("status") == "completed",
            f"{phase} incomplete",
        )
    ingest = checkpoint["phases"]["ingest"]
    sessions = list(ingest.get("results", {}).values())
    failures = sum(row.get("status") == "extraction_failed" for row in sessions)
    require(bool(sessions) and failures / len(sessions) < 0.01, "Ingest extraction failures")
    require(
        all(
            row.get("restored_pre_session_state") is True
            for row in [*ingest.get("ingest_rollbacks", []), *manifest.get("ingest_rollbacks", [])]
        ),
        "Unproven ingest rollback",
    )
    require(
        manifest.get("baseline_qualified") is True and manifest.get("disqualifications") == [],
        "Run unqualified",
    )
    require(not manifest.get("code_pin_changes"), "Code changed during run")
    require(
        manifest.get("ingest_source", {}).get("baseline_qualified") is True,
        "Ingest source unqualified",
    )
    require(manifest.get("git", {}).get("dirty") is False, "Dirty execution code")
    require(manifest.get("dataset", {}).get("sha256") == DEV_SHA256, "Manifest dataset differs")
    require(
        manifest.get("ingest_snapshot", {}).get("sha256") == snapshot_sha256, "Snapshot differs"
    )
    require(manifest.get("substrate") == "fact", "Wrong substrate")
    require(evaluate.get("state_matches_ingest_at_start") is True, "State mismatch override")
    require(evaluate.get("state_unchanged_by_evaluate") is True, "State mutated during evaluate")
    for key in ("state_fingerprint_start", "state_fingerprint_end"):
        require(
            evaluate.get(key, {}).get("sha256") == state_sha256, "Protected fingerprint differs"
        )
    require(evaluate.get("retrieval_modes") == {RETRIEVAL_MODE: 50}, "Retrieval mode/count differs")
    require(
        all(evaluate.get("provider_fingerprints", {}).get(key) for key in ("answer", "judge")),
        "Provider fingerprints absent",
    )
    require(0.05 <= official["strict_accuracy"] <= 0.95, "Implausible primary accuracy")
    require(
        all(item["correct"] > 0 for item in dual["official_by_category"].values()),
        "Zero official category; investigate",
    )
    require(dual["paired"]["total"] == 50, "Incomplete dual judge pairing")
    require(
        dual["paired"]["strict_disagreement_rate"] <= 0.20, "Judge disagreement exceeds 20 percent"
    )
    return {"official": official, "dual": dual, "extraction_failures": failures}


def comparability(first: Mapping[str, Any], second: Mapping[str, Any]) -> None:
    for key in ("substrate", "config_pin", "memory_formatter", "parity_exceptions"):
        require(first.get(key) == second.get(key), f"Comparator {key} differs")
    require(first["dataset"]["sha256"] == second["dataset"]["sha256"], "Comparator dataset differs")
    for key in ("answer", "judge", "official_judge", "inference_policy", "model_routing"):
        require(first["routes"][key] == second["routes"][key], f"Comparator {key} route differs")
    # Route attestation timestamps can differ; vector identity may not.
    require(
        first["phases"]["evaluate"]["retrieval_modes"]
        == second["phases"]["evaluate"]["retrieval_modes"],
        "Comparator embedding differs",
    )


def paired_result(arms: Mapping[str, list[dict[str, Any]]]) -> dict[str, Any]:
    require(set(arms) == {"A0", "A1", "A2", "C0"}, "Campaign arm coverage differs")
    labels = {
        arm: {row["question_id"]: row["official_judgment"] == "correct" for row in rows}
        for arm, rows in arms.items()
    }
    require(
        all(set(value) == set(labels["A0"]) and len(value) == 50 for value in labels.values()),
        "Paired identities differ",
    )
    flips = {
        f"{a}/{b}": sum(labels[a][qid] != labels[b][qid] for qid in labels[a])
        for a, b in combinations(("A0", "A1", "A2"), 2)
    }
    noise = max(flips.values())
    gains = [qid for qid in labels["A0"] if labels["C0"][qid] and not labels["A0"][qid]]
    losses = [qid for qid in labels["A0"] if labels["A0"][qid] and not labels["C0"][qid]]
    categories = {row["question_id"]: row["category"] for row in arms["A0"]}
    deltas = {
        category: sum(
            (int(labels["C0"][qid]) - int(labels["A0"][qid]))
            for qid in categories
            if categories[qid] == category
        )
        for category in sorted(set(categories.values()))
    }
    abstention = sum(
        int(labels["C0"][qid]) - int(labels["A0"][qid]) for qid in labels["A0"] if "_abs" in qid
    )
    threshold = max(3, 2 * noise)
    accepted = (
        len(gains) - len(losses) >= threshold and min(deltas.values()) >= -2 and abstention >= 0
    )
    return {
        "rule": "2026-10-28-official-v1",
        "paired_comparator": "A0",
        "control_flips": flips,
        "F": noise,
        "threshold": threshold,
        "gains": gains,
        "losses": losses,
        "net_gain": len(gains) - len(losses),
        "category_deltas": deltas,
        "abstention_delta": abstention,
        "accepted": accepted,
        "interpretation": "measured dev gain"
        if accepted
        else "reject or investigate; no candidate rerun",
    }
