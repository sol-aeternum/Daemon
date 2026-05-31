from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMORY_LAYER_DOC = REPO_ROOT / "MEMORY_LAYER.md"
VARIANCE_PATH = REPO_ROOT / "tests" / "benchmark_results" / "final" / "VARIANCE.md"
CONFIG_PATH = REPO_ROOT / "orchestrator" / "config.py"


def _load_variance_json() -> dict[str, object]:
    text = VARIANCE_PATH.read_text()
    match = re.search(
        r"```json\n(.*?)\n```",
        text,
        re.DOTALL,
    )
    assert match is not None, "final VARIANCE.md is missing its JSON summary"
    return cast(dict[str, object], json.loads(match.group(1)))


def _load_doc() -> str:
    return MEMORY_LAYER_DOC.read_text()


def _load_config() -> str:
    return CONFIG_PATH.read_text()


def test_memory_layer_doc_sync_variance_status() -> None:
    doc = _load_doc()
    variance = _load_variance_json()

    assert "67.8%" not in doc, "Stale 67.8% reference must not appear in MEMORY_LAYER.md"
    assert "81.1%" not in doc, "Stale 81.1% reference must not appear in MEMORY_LAYER.md"

    assert "no_shippable_composition" in doc, (
        "MEMORY_LAYER.md must document the no_shippable_composition outcome"
    )

    assert variance["composition_run_executed"] is False
    assert variance["full_corpus_triple_run_executed"] is False
    assert variance["status"] == "no_shippable_composition"
    assert variance["eligible_candidate_count"] == 0


def test_memory_layer_doc_sync_dedup_thresholds_match_shipped() -> None:
    doc = _load_doc()
    config = _load_config()

    merge_match = re.search(r"dedup_merge_threshold.*?default=(\d+\.\d+)", config, re.DOTALL)
    supersede_match = re.search(
        r"dedup_supersede_threshold.*?default=(\d+\.\d+)", config, re.DOTALL
    )
    same_slot_match = re.search(
        r"dedup_supersede_same_slot_threshold.*?default=(\d+\.\d+)", config, re.DOTALL
    )
    assert merge_match is not None, "Could not find dedup_merge_threshold default in config.py"
    assert supersede_match is not None, (
        "Could not find dedup_supersede_threshold default in config.py"
    )
    assert same_slot_match is not None, (
        "Could not find dedup_supersede_same_slot_threshold in config.py"
    )

    shipped_merge = merge_match.group(1)
    shipped_supersede = supersede_match.group(1)
    shipped_same_slot = same_slot_match.group(1)

    assert f"| Merge | `{shipped_merge}`" in doc, (
        f"MEMORY_LAYER.md merge threshold must be `{shipped_merge}` (shipped value)"
    )
    assert f"| Supersede (generic) | `{shipped_supersede}`" in doc, (
        f"MEMORY_LAYER.md generic supersede threshold must be `{shipped_supersede}` (shipped value)"
    )
    assert f"| Supersede (same slot) | `{shipped_same_slot}`" in doc, (
        f"MEMORY_LAYER.md same-slot supersede threshold must be `{shipped_same_slot}` (shipped value)"
    )


def test_memory_layer_doc_sync_contains_phase4d_closeout_terminology() -> None:
    doc = _load_doc()
    variance = _load_variance_json()

    assert "Phase 4d" in doc or "4d" in doc, (
        "MEMORY_LAYER.md should reference Phase 4d as the closeout step for no_shippable_composition"
    )

    assert "no_shippable_composition" in doc, (
        "MEMORY_LAYER.md must document the no_shippable_composition outcome"
    )

    blocking_reason = cast(str, variance.get("blocking_reason", ""))
    assert (
        "zero" in blocking_reason.lower()
        or "no" in blocking_reason.lower()
        or "0" in blocking_reason
    ), "VARIANCE.md blocking_reason should indicate zero eligible candidates"
