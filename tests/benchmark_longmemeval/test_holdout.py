from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_longmemeval.holdout import freeze, project_metadata, select_holdout


def row(qid: str, kind: str = "multi-session", sessions: list[str] | None = None) -> dict[str, Any]:
    return {
        "question_id": qid,
        "question_type": kind,
        "answer_session_ids": [qid] if sessions is None else sessions,
    }


def test_metadata_projection_never_reads_content() -> None:
    class MetadataOnly(dict):
        def __getitem__(self, key):
            if key not in {"question_id", "question_type", "answer_session_ids"}:
                raise AssertionError("Content must not be accessed")
            return super().__getitem__(key)

    assert project_metadata([MetadataOnly(row("q"))])[0].question_id == "q"


def test_proportional_strata_exclusions_and_order_independence() -> None:
    dev = [row("dev", sessions=["shared"])]
    rows = dev + [row("excluded", sessions=["shared"])]
    rows += [row(f"m{i}") for i in range(6)]
    rows += [row(f"u{i}", "single-session-user") for i in range(3)]
    rows += [row("rare_abs", "knowledge-update")]
    result = select_holdout(project_metadata(rows), project_metadata(dev), size=5)
    assert result == select_holdout(
        project_metadata(list(reversed(rows))), project_metadata(dev), size=5
    )
    assert result["selected_strata"] == {
        "knowledge-update:abs": 1,
        "multi-session:answerable": 3,
        "single-session-user:answerable": 1,
    }
    assert result["eligible_count"] == 10
    assert result["excluded_dev_ids"] == 1
    assert result["excluded_shared_answer_sessions_only"] == 1
    assert "excluded" not in result["question_ids"]
    assert "dev" not in result["question_ids"]
    assert len(set(result["question_ids"])) == 5
    encoded = ("\n".join(result["question_ids"]) + "\n").encode()
    assert result["question_ids_sha256"] == hashlib.sha256(encoded).hexdigest()


def test_abstention_stratification_and_seed() -> None:
    rows = [row(f"q{i}") for i in range(20)] + [row(f"q{i}_abs") for i in range(20)]
    metadata = project_metadata(rows)
    result = select_holdout(metadata, [], size=10)
    assert result["selected_strata"] == {"multi-session:abs": 5, "multi-session:answerable": 5}
    assert result["question_ids"] != select_holdout(metadata, [], size=10, seed=43)["question_ids"]
    assert select_holdout(metadata, [], size=40)["question_ids"] == sorted(
        r["question_id"] for r in rows
    )


@pytest.mark.parametrize(
    "rows",
    [None, [{}], [row("q"), row("q")], [row("")], [row("q", "unknown")], [row("q", sessions=[""])]],
)
def test_bad_metadata_fails_without_echoing_content(rows: Any) -> None:
    with pytest.raises(ValueError):
        project_metadata(rows)


def test_invalid_size_and_dev_mismatch() -> None:
    metadata = project_metadata([row("q")])
    for size in (0, -1, 2):
        with pytest.raises(ValueError):
            select_holdout(metadata, [], size=size)
    with pytest.raises(ValueError, match="Dev selection metadata"):
        select_holdout(metadata, project_metadata([row("q", sessions=["different"])]), size=1)


def test_manifest_is_content_free_and_never_overwritten(tmp_path: Path) -> None:
    dataset, dev, output = (tmp_path / name for name in ("full.json", "dev.json", "holdout.json"))
    dataset.write_text(json.dumps([{**row("q"), "question": "NEVER EMIT", "answer": "NEVER EMIT"}]))
    dev.write_text("[]")
    freeze(dataset, dev, output, size=1, seed=42)
    before = output.read_bytes()
    assert b"NEVER EMIT" not in before
    assert json.loads(before)["dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        freeze(dataset, dev, output, size=1, seed=43)
    assert output.read_bytes() == before


def test_frozen_manifest_integrity() -> None:
    fixtures = Path(__file__).with_name("fixtures")
    manifest = json.loads((fixtures / "holdout_ids.json").read_bytes())
    assert manifest["algorithm"] == "type-x-abstention-largest-remainder-sha256-v1"
    assert manifest["size"] == len(manifest["question_ids"]) == 100
    assert manifest["seed"] == 42
    assert manifest["question_ids"] == sorted(set(manifest["question_ids"]))
    assert manifest["question_ids_sha256"] == (
        "58ac5a5dd4c9f3f4af5ef123be04cd977f1ad2a854253ba3b93b91f2641bc502"
    )
    encoded = ("\n".join(manifest["question_ids"]) + "\n").encode()
    assert hashlib.sha256(encoded).hexdigest() == manifest["question_ids_sha256"]
    assert sum(manifest["selected_strata"].values()) == 100
    assert (
        sum(value for key, value in manifest["selected_strata"].items() if key.endswith(":abs"))
        == 5
    )
    assert (
        hashlib.sha256((fixtures / "dev_subset.json").read_bytes()).hexdigest()
        == manifest["dev_sha256"]
    )
    dev_ids = {
        r.question_id
        for r in project_metadata(json.loads((fixtures / "dev_subset.json").read_bytes()))
    }
    assert not dev_ids.intersection(manifest["question_ids"])
