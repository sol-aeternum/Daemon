from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from orchestrator.config import Settings
from orchestrator.eval.runner import (
    BENCHMARK_CONFIG_PIN_PATH,
    LongMemEvalRunner,
    build_longmemeval_pinned_config,
)
from tests.longmemeval.evaluate import build_answer_prompt


def _default_benchmark_settings() -> Settings:
    return Settings.model_validate({})


def _make_runner(tmp_path: Path, dataset_path: Path) -> LongMemEvalRunner:
    return LongMemEvalRunner(
        dataset_path=dataset_path,
        output_path=tmp_path / "longmemeval_results.jsonl",
        checkpoint_path=tmp_path / "longmemeval_checkpoint.json",
        score_path=tmp_path / "longmemeval_score.json",
        limit=7,
        force_retrieval_logging=True,
    )


def test_committed_benchmark_config_pin_matches_live_authority_defaults() -> None:
    committed_pin = json.loads(BENCHMARK_CONFIG_PIN_PATH.read_text())

    assert committed_pin == build_longmemeval_pinned_config(settings=_default_benchmark_settings())


def test_answer_prompt_sha256_tracks_aligned_contract_not_only_thin_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _default_benchmark_settings()
    baseline = build_longmemeval_pinned_config(settings=settings)
    answer_config = baseline["shared"]["answer"]
    thin_prompt = build_answer_prompt(
        "__LONGMEMEVAL_QUESTION__",
        [{"content": "__LONGMEMEVAL_MEMORY__"}],
    )
    thin_hash = hashlib.sha256(thin_prompt.encode("utf-8")).hexdigest()

    assert answer_config["prompt_sha256"] != thin_hash
    assert answer_config["prompt_contract_kind"] == ("aligned_system_user_with_legacy_fallback_v1")
    assert answer_config["active_message_roles"] == ["system", "user"]
    assert answer_config["legacy_fallback_message_roles"] == ["user"]

    original_hash = answer_config["prompt_sha256"]

    async def fake_aligned_builder(*args: object, **kwargs: object) -> str:
        return "patched aligned prompt"

    monkeypatch.setattr(
        "orchestrator.eval.runner.build_assembled_system_prompt",
        fake_aligned_builder,
    )
    aligned_hash = build_longmemeval_pinned_config(settings=settings)["shared"]["answer"][
        "prompt_sha256"
    ]
    assert aligned_hash != original_hash


def test_runner_load_checkpoint_emits_effective_benchmark_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _default_benchmark_settings()
    monkeypatch.setattr("orchestrator.eval.runner.get_settings", lambda: settings)

    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]")
    runner = _make_runner(tmp_path, dataset_path)

    checkpoint = runner.load_checkpoint()
    effective_config = checkpoint["benchmark_effective_config"]

    assert effective_config["lane"] == "canonical"
    assert effective_config["pin_path"] == str(BENCHMARK_CONFIG_PIN_PATH)
    assert effective_config["runtime"] == {
        "dataset_path": str(dataset_path),
        "limit": 7,
        "output_path": str(runner.output_path),
        "checkpoint_path": str(runner.checkpoint_path),
        "score_path": str(runner.score_path),
        "force_retrieval_logging": True,
        "benchmark_mode": False,
    }
    assert effective_config["pinned_authority"] == json.loads(BENCHMARK_CONFIG_PIN_PATH.read_text())
    assert checkpoint["benchmark_config_drift_warnings"] == []


def test_runner_load_checkpoint_warns_on_benchmark_config_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _default_benchmark_settings().model_copy(
        update={"embedding_query_model": "voyage-4-large"}
    )
    monkeypatch.setattr("orchestrator.eval.runner.get_settings", lambda: settings)

    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text("[]")
    runner = _make_runner(tmp_path, dataset_path)

    with caplog.at_level("WARNING", logger="orchestrator.eval.runner"):
        checkpoint = runner.load_checkpoint()

    warnings = checkpoint["benchmark_config_drift_warnings"]
    assert any("shared.query_embedding.embedding_query_model" in warning for warning in warnings)
    assert any(
        "LongMemEval benchmark config drift detected" in record.message for record in caplog.records
    )
