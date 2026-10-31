"""Historical-clock mode for a benchmark-only remapped corpus (#601). Synthetic only."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from orchestrator.eval import campaign_validation, fact_harness
from orchestrator.eval.durable_io import file_sha256


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload))
    return path


def _ingest(state_sha: str) -> dict[str, Any]:
    return {
        "status": "completed",
        "completed_count": 2,
        "results": {"q1:a": {"conversation_id": "c1", "status": "complete"}},
        "state_fingerprint": {"sha256": state_sha, "rows": {"memories": 3}},
    }


def _checkpoint(ingest: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "version": fact_harness.CHECKPOINT_VERSION,
        "dataset_path": None,
        "dataset_sha256": "dev",
        "run_id": "run-1",
        "phases": {"ingest": ingest, "evaluate": {"results": {}}, "score": {"accuracy": {}}},
        **extra,
    }


@pytest.fixture()
def remap(tmp_path: Path) -> dict[str, Any]:
    original = _write(tmp_path / "longmemeval_checkpoint.json", _checkpoint(_ingest("old")))
    _write(tmp_path / fact_harness.MANIFEST_FILENAME, {"run_id": "run-1"})
    lineage = _write(
        tmp_path / "lineage.json",
        {
            "status": "remapped_snapshot_created",
            "source": {"state_sha256": "old", "snapshot_sha256": "snap-old"},
            "remapped_state_sha256": "new",
            "snapshot": {"path": str(tmp_path / "remapped.dump"), "sha256": "snap-new"},
        },
    )
    block = {
        "lineage_path": str(lineage),
        "lineage_sha256": file_sha256(lineage),
        "original_checkpoint_path": str(original),
        "original_checkpoint_sha256": file_sha256(original),
    }
    derived_dir = tmp_path / "derived"
    derived_dir.mkdir()
    derived = _write(
        derived_dir / "longmemeval_checkpoint.json",
        _checkpoint(_ingest("new"), historical_remap=block),
    )
    return {"tmp": tmp_path, "block": block, "derived": derived, "lineage": lineage}


def test_parity_exceptions_swap_only_message_timestamps() -> None:
    default = fact_harness.parity_exceptions(False)
    historical = fact_harness.parity_exceptions(True)
    assert default is fact_harness.PARITY_EXCEPTIONS
    assert [i["id"] for i in default] == [i["id"] for i in historical]
    changed = [a["id"] for a, b in zip(default, historical, strict=True) if a != b]
    assert changed == ["message_timestamps"]
    assert "query_reference_time are the question's as-of time" in historical[-1]["difference"]


def test_verify_historical_remap_accepts_exact_lineage(remap: dict[str, Any]) -> None:
    manifest = fact_harness.verify_historical_remap(remap["block"], _ingest("new"), "run-1")
    assert manifest == remap["tmp"] / fact_harness.MANIFEST_FILENAME


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b, i: b.update(lineage_sha256="0" * 64), "lineage changed"),
        (lambda b, i: b.update(original_checkpoint_sha256="0" * 64), "source checkpoint changed"),
        (lambda b, i: i["results"].update(extra={}), "beyond state"),
        (lambda b, i: i["state_fingerprint"].update(sha256="other"), "does not match"),
    ],
)
def test_verify_historical_remap_refuses_tampering(
    remap: dict[str, Any], mutate: Any, message: str
) -> None:
    block, ingest = dict(remap["block"]), _ingest("new")
    mutate(block, ingest)
    with pytest.raises(ValueError, match=message):
        fact_harness.verify_historical_remap(block, ingest, "run-1")


def test_verify_historical_remap_refuses_other_run(remap: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="beyond state"):
        fact_harness.verify_historical_remap(remap["block"], _ingest("new"), "run-2")


def test_seed_from_remap_records_lineage_and_original_qualification(
    remap: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    def qualification(path: Path, run_id: Any) -> dict[str, Any]:
        seen.append(path)
        return {"baseline_qualified": True, "disqualifications": []}

    monkeypatch.setattr(fact_harness, "_source_qualification", qualification)
    runner = fact_harness.LongMemEvalFactRunner(
        dataset_path=tmp_path / "dev.json",
        output_path=tmp_path / "out" / "results.jsonl",
        checkpoint_path=tmp_path / "out" / "checkpoint.json",
        score_path=tmp_path / "out" / "score.json",
        from_ingest=remap["derived"],
    )
    checkpoint = _checkpoint({"results": {}}, run_id="eval-run")
    runner._seed_from_ingest(checkpoint)
    assert seen == [remap["tmp"] / fact_harness.MANIFEST_FILENAME]
    assert checkpoint["ingest_source"]["historical_remap"] == remap["block"]
    assert checkpoint["phases"]["ingest"]["state_fingerprint"]["sha256"] == "new"
    assert fact_harness._historical_clock(checkpoint) is True
    assert fact_harness._historical_clock({"ingest_source": {"baseline_qualified": True}}) is False


def test_expected_corpus_defaults_to_original_constants() -> None:
    assert campaign_validation.expected_corpus({}) == (
        campaign_validation.SNAPSHOT_SHA256,
        campaign_validation.STATE_SHA256,
    )
    pins = {"historical_remap": {"snapshot_sha256": "s", "state_sha256": "t"}}
    assert campaign_validation.expected_corpus(pins) == ("s", "t")


@pytest.mark.asyncio
@pytest.mark.parametrize("historical", [False, True])
async def test_evaluate_single_parity_anchors_windows_only_in_historical_mode(
    monkeypatch: pytest.MonkeyPatch, historical: bool
) -> None:
    from orchestrator.memory import injection
    from tests.longmemeval import evaluate

    gathered: dict[str, Any] = {}

    async def fake_gather(store: Any, **kwargs: Any) -> injection.MemoryContextInputs:
        gathered.update(kwargs)
        return injection.MemoryContextInputs(retrieval_mode="lexical")

    async def fake_llm(**kwargs: Any) -> Any:
        content = {"answer": "a", "judge": "CORRECT\nok", "official_judge": "yes"}
        response = {
            "choices": [
                {"message": {"content": content[kwargs["bm_call_key"]]}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        evaluate._capture_benchmark_metadata(response, key=kwargs["bm_call_key"])
        return response

    monkeypatch.setattr(evaluate, "gather_memory_context_inputs", fake_gather)
    monkeypatch.setattr(evaluate, "_call_llm_with_provider_config", fake_llm)
    as_of = datetime(2023, 3, 1, 19, 28, tzinfo=UTC)
    result = await evaluate.evaluate_single_parity(
        MagicMock(),
        "q1",
        "What did I do in February?",
        "a",
        "TR",
        allowed_source_conversation_ids=None,
        as_of=as_of,
        historical_clock=historical,
    )
    assert gathered["query_reference_time"] == (as_of if historical else None)
    assert result["historical_clock"] is historical


@pytest.mark.asyncio
async def test_historical_clock_requires_as_of() -> None:
    from tests.longmemeval import evaluate

    with pytest.raises(ValueError, match="requires as_of"):
        await evaluate.evaluate_single_parity(
            MagicMock(),
            "q1",
            "?",
            "a",
            "TR",
            allowed_source_conversation_ids=None,
            historical_clock=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [None, datetime(2023, 3, 1, tzinfo=UTC)])
async def test_gather_passes_query_reference_time_only_when_set(
    monkeypatch: pytest.MonkeyPatch, reference: datetime | None
) -> None:
    from orchestrator.memory import injection

    calls: list[dict[str, Any]] = []

    async def fake_embed(text: str) -> Any:
        return None

    async def fake_retrieve(**kwargs: Any) -> list[dict[str, object]]:
        calls.append(kwargs)
        return []

    store = MagicMock()

    async def empty(*args: Any, **kwargs: Any) -> list[dict[str, object]]:
        return []

    store.get_l0_memories = empty
    store.get_recent_summaries = empty
    monkeypatch.setattr(injection, "embed_query_with_metadata", fake_embed)
    monkeypatch.setattr(injection, "retrieve_memories_for_text", fake_retrieve)
    monkeypatch.setattr(injection, "get_selected_embedding_route_id", lambda: None)
    await injection.gather_memory_context_inputs(
        store,
        user_id=uuid.uuid4(),
        pipeline="cloud",
        query_text="What did I do in February?",
        record_side_effects=False,
        query_reference_time=reference,
    )
    assert len(calls) == 1
    if reference is None:
        assert "query_reference_time" not in calls[0]
    else:
        assert calls[0]["query_reference_time"] == reference


def _prepare_args(remap: dict[str, Any], snapshot: Path) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        historical_remap=remap["lineage"], snapshot=snapshot, ingest=remap["derived"]
    )


def _qualified_lineage(remap: dict[str, Any], **source: str) -> None:
    lineage = json.loads(remap["lineage"].read_text())
    lineage["source"] = {
        "snapshot_sha256": campaign_validation.SNAPSHOT_SHA256,
        "state_sha256": campaign_validation.STATE_SHA256,
        **source,
    }
    remap["lineage"].write_text(json.dumps(lineage))
    # Keep the derived checkpoint's lineage hash and the original state in step.
    original = Path(remap["block"]["original_checkpoint_path"])
    payload = json.loads(original.read_text())
    payload["phases"]["ingest"]["state_fingerprint"]["sha256"] = lineage["source"]["state_sha256"]
    original.write_text(json.dumps(payload))
    block = dict(
        remap["block"],
        lineage_sha256=file_sha256(remap["lineage"]),
        original_checkpoint_sha256=file_sha256(original),
    )
    derived = json.loads(remap["derived"].read_text())
    derived["historical_remap"] = block
    remap["derived"].write_text(json.dumps(derived))


def test_prepare_pins_a_verified_historical_remap(remap: dict[str, Any]) -> None:
    from orchestrator.eval import campaign_launch

    _qualified_lineage(remap)
    snapshot = Path(json.loads(remap["lineage"].read_text())["snapshot"]["path"])
    pins = campaign_launch._historical_remap_pins(_prepare_args(remap, snapshot))
    assert pins["snapshot_sha256"] == "snap-new" and pins["state_sha256"] == "new"
    assert pins["lineage_sha256"] == file_sha256(remap["lineage"])
    assert pins["source_manifest"] == str(remap["tmp"] / fact_harness.MANIFEST_FILENAME)


def test_prepare_refuses_remap_of_another_corpus(remap: dict[str, Any]) -> None:
    from orchestrator.eval import campaign_launch
    from orchestrator.eval.campaign_guard import CampaignStopped

    _qualified_lineage(remap, state_sha256="not-the-qualified-state")
    snapshot = Path(json.loads(remap["lineage"].read_text())["snapshot"]["path"])
    with pytest.raises(CampaignStopped, match="qualified corpus"):
        campaign_launch._historical_remap_pins(_prepare_args(remap, snapshot))


def test_prepare_refuses_other_snapshot_or_checkpoint(
    remap: dict[str, Any], tmp_path: Path
) -> None:
    from orchestrator.eval import campaign_launch
    from orchestrator.eval.campaign_guard import CampaignStopped

    _qualified_lineage(remap)
    with pytest.raises(CampaignStopped, match="Snapshot is not"):
        campaign_launch._historical_remap_pins(_prepare_args(remap, tmp_path / "other.dump"))
    snapshot = Path(json.loads(remap["lineage"].read_text())["snapshot"]["path"])
    derived = json.loads(remap["derived"].read_text())
    derived["historical_remap"]["lineage_sha256"] = "0" * 64
    remap["derived"].write_text(json.dumps(derived))
    with pytest.raises(CampaignStopped, match="not this remap"):
        campaign_launch._historical_remap_pins(_prepare_args(remap, snapshot))
