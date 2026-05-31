from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from types import SimpleNamespace
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from orchestrator.eval.runner import (
    LongMemEvalRunner,
    load_dataset,
    load_runner_checkpoint,
)
from tests.longmemeval.ingest import build_corpus_key


def write_dataset(path: Path, payload: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(payload))


def make_runner(
    tmp_path: Path, dataset_path: Path, *, limit: int | None = None
) -> LongMemEvalRunner:
    return LongMemEvalRunner(
        dataset_path=dataset_path,
        output_path=tmp_path / "results.jsonl",
        checkpoint_path=tmp_path / "checkpoint.json",
        score_path=tmp_path / "score.json",
        limit=limit,
        force_retrieval_logging=True,
    )


def test_load_dataset_rejects_missing_path(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.json"

    with pytest.raises(FileNotFoundError, match="LongMemEval dataset not found"):
        load_dataset(missing_path)


def test_load_dataset_rejects_invalid_json(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("{not-json")

    with pytest.raises(ValueError, match="LongMemEval dataset is not valid JSON"):
        load_dataset(invalid_path)


def test_load_runner_checkpoint_rejects_dataset_mismatch(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "dataset_path": str(tmp_path / "other.json"),
                "version": 2,
                "phases": {
                    "ingest": {"results": {}},
                    "evaluate": {"results": {}},
                    "score": {"accuracy": {}},
                },
            }
        )
    )

    with pytest.raises(ValueError, match="Checkpoint dataset mismatch"):
        load_runner_checkpoint(checkpoint_path, dataset_path=tmp_path / "dataset.json")


def test_load_runner_checkpoint_rejects_legacy_checkpoint_version(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "version": 1,
                "dataset_path": str(tmp_path / "dataset.json"),
                "phases": {
                    "ingest": {"results": {}},
                    "evaluate": {"results": {}},
                    "score": {"accuracy": {}},
                },
            }
        )
    )

    with pytest.raises(ValueError, match="corpus-first LongMemEval harness requires version 2"):
        load_runner_checkpoint(checkpoint_path, dataset_path=tmp_path / "dataset.json")


@pytest.mark.asyncio
async def test_ingest_resumes_from_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_session = [{"role": "user", "content": "first"}]
    dataset = [
        {
            "question_id": "q1",
            "haystack_session_ids": ["session-1", "session-2"],
            "haystack_sessions": [
                shared_session,
                [{"role": "user", "content": "second"}],
            ],
        },
        {
            "question_id": "q2",
            "haystack_session_ids": ["session-3"],
            "haystack_sessions": [
                [{"role": "user", "content": "first   "}],
            ],
        },
    ]
    dataset_path = tmp_path / "dataset.json"
    write_dataset(dataset_path, dataset)
    runner = make_runner(tmp_path, dataset_path)

    shared_key = build_corpus_key(shared_session)
    second_key = build_corpus_key([{"role": "user", "content": "second"}])

    runner.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 2,
                "dataset_path": str(dataset_path),
                "phases": {
                    "ingest": {
                        "results": {
                            shared_key: {
                                "corpus_key": shared_key,
                                "session_id": "session-1",
                                "status": "complete",
                            }
                        }
                    },
                    "evaluate": {"results": {}},
                    "score": {"accuracy": {}},
                },
            }
        )
    )

    mock_pool = AsyncMock()
    monkeypatch.setattr(
        "orchestrator.eval.runner.get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://daemon:daemon@postgres/daemon",
            daemon_encryption_key="test-key",
        ),
    )
    monkeypatch.setattr(
        "orchestrator.eval.runner.asyncpg.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr("orchestrator.eval.runner.ContentEncryption", lambda key: object())
    monkeypatch.setattr(
        "orchestrator.eval.runner.MemoryStore", lambda db_pool, encryption: MagicMock()
    )
    monkeypatch.setattr(
        "orchestrator.eval.runner.create_test_user", AsyncMock(return_value="user-id")
    )

    ingest_session_mock = AsyncMock(return_value={"session_id": "session-2", "status": "complete"})
    monkeypatch.setattr("orchestrator.eval.runner.ingest_session", ingest_session_mock)

    results = await runner.ingest()

    assert ingest_session_mock.await_count == 1
    assert ingest_session_mock.await_args_list[0].kwargs["session_id"] == "session-2"
    assert len(results) == 2
    assert {result["corpus_key"] for result in results} == {shared_key, second_key}

    checkpoint_payload = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint_payload["phases"]["ingest"]["completed_count"] == 2
    assert checkpoint_payload["phases"]["ingest"]["status"] == "completed"


@pytest.mark.asyncio
async def test_evaluate_resumes_from_checkpoint_and_writes_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_session = [{"role": "user", "content": "shared memory"}]
    dataset = [
        {
            "question_id": "q1",
            "question": "first?",
            "answer": "one",
            "question_type": "single-session-user",
            "haystack_session_ids": ["session-1"],
            "haystack_sessions": [shared_session],
        },
        {
            "question_id": "q2",
            "question": "second?",
            "answer": "two",
            "question_type": "multi-session",
            "haystack_session_ids": ["session-2"],
            "haystack_sessions": [shared_session],
        },
    ]
    dataset_path = tmp_path / "dataset.json"
    write_dataset(dataset_path, dataset)
    runner = make_runner(tmp_path, dataset_path)

    shared_key = build_corpus_key(shared_session)
    shared_conversation_id = str(uuid.uuid4())

    runner.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 2,
                "dataset_path": str(dataset_path),
                "phases": {
                    "ingest": {
                        "results": {
                            shared_key: {
                                "corpus_key": shared_key,
                                "session_id": "session-1",
                                "conversation_id": shared_conversation_id,
                                "status": "complete",
                            }
                        }
                    },
                    "evaluate": {
                        "results": {
                            "q1": {
                                "question_id": "q1",
                                "hypothesis": "one",
                                "category": "IE-user",
                                "judgment": "correct",
                            }
                        }
                    },
                    "score": {"accuracy": {}},
                },
            }
        )
    )

    mock_pool = AsyncMock()
    monkeypatch.setattr(
        "orchestrator.eval.runner.get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://daemon:daemon@postgres/daemon",
            daemon_encryption_key="test-key",
        ),
    )
    monkeypatch.setattr(
        "orchestrator.eval.runner.asyncpg.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr("orchestrator.eval.runner.ContentEncryption", lambda key: object())
    monkeypatch.setattr(
        "orchestrator.eval.runner.MemoryStore", lambda pool, encryption: MagicMock()
    )

    evaluate_single_mock = AsyncMock(
        return_value={
            "question_id": "q2",
            "question": "second?",
            "reference": "two",
            "hypothesis": "two",
            "category": "MR",
            "judgment": "correct",
            "memories_used": 1,
        }
    )
    monkeypatch.setattr("orchestrator.eval.runner.evaluate_single", evaluate_single_mock)

    results = await runner.evaluate()

    assert evaluate_single_mock.await_count == 1
    assert evaluate_single_mock.await_args_list[0].kwargs["question_id"] == "q2"
    assert evaluate_single_mock.await_args_list[0].kwargs["allowed_source_conversation_ids"] == [
        uuid.UUID(shared_conversation_id)
    ]
    assert [result["question_id"] for result in results] == ["q1", "q2"]
    assert runner.output_path.exists()
    assert len(runner.output_path.read_text().splitlines()) == 2

    checkpoint_payload = json.loads(runner.checkpoint_path.read_text())
    assert checkpoint_payload["phases"]["evaluate"]["completed_count"] == 2
    assert checkpoint_payload["phases"]["evaluate"]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_reuses_shared_corpus_across_multiple_questions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared_session = [{"role": "user", "content": "shared history"}]
    dataset = [
        {
            "question_id": "q1",
            "question": "what is shared?",
            "answer": "history",
            "question_type": "single-session-user",
            "haystack_session_ids": ["session-a"],
            "haystack_sessions": [shared_session],
        },
        {
            "question_id": "q2",
            "question": "repeat it?",
            "answer": "history",
            "question_type": "multi-session",
            "haystack_session_ids": ["session-b"],
            "haystack_sessions": [[{"role": "user", "content": "shared   history"}]],
        },
    ]
    dataset_path = tmp_path / "dataset.json"
    write_dataset(dataset_path, dataset)
    runner = make_runner(tmp_path, dataset_path)

    conversation_id = str(uuid.uuid4())
    ingest_calls: list[str] = []
    evaluate_scopes: list[list[uuid.UUID]] = []

    mock_pool = AsyncMock()
    monkeypatch.setattr(
        "orchestrator.eval.runner.get_settings",
        lambda: SimpleNamespace(
            database_url="postgresql://daemon:daemon@postgres/daemon",
            daemon_encryption_key="test-key",
        ),
    )
    monkeypatch.setattr(
        "orchestrator.eval.runner.asyncpg.create_pool",
        AsyncMock(return_value=mock_pool),
    )
    monkeypatch.setattr("orchestrator.eval.runner.ContentEncryption", lambda key: object())
    monkeypatch.setattr(
        "orchestrator.eval.runner.MemoryStore", lambda db_pool, encryption: MagicMock()
    )
    monkeypatch.setattr(
        "orchestrator.eval.runner.create_test_user", AsyncMock(return_value="user-id")
    )

    async def fake_ingest_session(**kwargs: Any) -> dict[str, Any]:
        ingest_calls.append(kwargs["session_id"])
        return {
            "session_id": kwargs["session_id"],
            "conversation_id": conversation_id,
            "status": "complete",
        }

    async def fake_evaluate_single(**kwargs: Any) -> dict[str, Any]:
        evaluate_scopes.append(kwargs["allowed_source_conversation_ids"])
        return {
            "question_id": kwargs["question_id"],
            "question": kwargs["question_text"],
            "reference": kwargs["reference"],
            "hypothesis": "history",
            "category": kwargs["category"],
            "judgment": "correct",
            "memories_used": 1,
        }

    monkeypatch.setattr("orchestrator.eval.runner.ingest_session", fake_ingest_session)
    monkeypatch.setattr("orchestrator.eval.runner.evaluate_single", fake_evaluate_single)
    monkeypatch.setattr("orchestrator.eval.runner.print_results", lambda results, accuracy: None)

    payload = await runner.run()

    assert ingest_calls == ["session-a"]
    assert evaluate_scopes == [
        [uuid.UUID(conversation_id)],
        [uuid.UUID(conversation_id)],
    ]
    assert len(payload["ingest"]) == 1
    assert [result["question_id"] for result in payload["evaluate"]] == ["q1", "q2"]
    assert payload["score"]["result_count"] == 2


def test_score_uses_checkpoint_results_and_writes_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = tmp_path / "dataset.json"
    write_dataset(dataset_path, [])
    runner = make_runner(tmp_path, dataset_path)

    runner.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 2,
                "dataset_path": str(dataset_path),
                "phases": {
                    "ingest": {"results": {}},
                    "evaluate": {
                        "results": {
                            "q1": {
                                "question_id": "q1",
                                "category": "IE-user",
                                "judgment": "correct",
                            },
                            "q2": {
                                "question_id": "q2",
                                "category": "MR",
                                "judgment": "incorrect",
                            },
                        }
                    },
                    "score": {"accuracy": {}},
                },
            }
        )
    )
    monkeypatch.setattr("orchestrator.eval.runner.print_results", lambda results, accuracy: None)

    payload = runner.score()

    assert payload["result_count"] == 2
    assert runner.score_path.exists()
    saved = json.loads(runner.score_path.read_text())
    assert saved["accuracy"]["IE-user"] == 1.0
    assert saved["accuracy"]["MR"] == 0.0


def test_load_runner_checkpoint_rejects_corrupted_json(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text("{not-valid-json")

    with pytest.raises(Exception, match="Expecting property name"):
        load_runner_checkpoint(checkpoint_path, dataset_path=None)


def test_load_runner_checkpoint_rejects_non_object_json(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text('"just a string"')

    with pytest.raises(ValueError, match="Checkpoint must be a JSON object"):
        load_runner_checkpoint(checkpoint_path, dataset_path=None)


def test_load_runner_checkpoint_rejects_array_json(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text("[1, 2, 3]")

    with pytest.raises(ValueError, match="Checkpoint must be a JSON object"):
        load_runner_checkpoint(checkpoint_path, dataset_path=None)


def test_load_runner_checkpoint_missing_phases_get_defaults(tmp_path: Path) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "version": 2,
                "dataset_path": str(tmp_path / "dataset.json"),
            }
        )
    )

    result = load_runner_checkpoint(checkpoint_path, dataset_path=tmp_path / "dataset.json")

    assert "phases" in result
    assert "ingest" in result["phases"]
    assert "evaluate" in result["phases"]
    assert "score" in result["phases"]
    assert result["phases"]["ingest"]["status"] == "pending"
    assert result["phases"]["evaluate"]["status"] == "pending"
    assert result["phases"]["score"]["status"] == "pending"
    assert result["phases"]["ingest"]["results"] == {}
    assert result["phases"]["evaluate"]["results"] == {}
    assert result["phases"]["score"]["accuracy"] == {}


def test_load_runner_checkpoint_preserves_existing_results_on_missing_phases(
    tmp_path: Path,
) -> None:
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint_path.write_text(
        json.dumps(
            {
                "version": 2,
                "dataset_path": str(tmp_path / "dataset.json"),
                "phases": {
                    "ingest": {
                        "status": "completed",
                        "completed_count": 5,
                        "results": {
                            "session-1": {
                                "session_id": "session-1",
                                "status": "complete",
                            }
                        },
                    },
                },
            }
        )
    )

    result = load_runner_checkpoint(checkpoint_path, dataset_path=tmp_path / "dataset.json")

    assert "evaluate" in result["phases"]
    assert "score" in result["phases"]
    assert result["phases"]["ingest"]["completed_count"] == 5
    assert "session-1" in result["phases"]["ingest"]["results"]


def test_score_falls_back_to_jsonl_when_evaluate_results_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = tmp_path / "dataset.json"
    write_dataset(dataset_path, [])
    runner = make_runner(tmp_path, dataset_path)

    runner.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 2,
                "dataset_path": str(dataset_path),
                "phases": {
                    "ingest": {
                        "results": {},
                        "status": "completed",
                        "completed_count": 0,
                    },
                    "evaluate": {
                        "results": {},
                        "status": "completed",
                        "completed_count": 0,
                    },
                    "score": {"status": "pending", "accuracy": {}},
                },
            }
        )
    )

    runner.output_path.write_text(
        json.dumps(
            {
                "question_id": "q1",
                "category": "IE-user",
                "judgment": "correct",
            }
        )
        + "\n"
        + json.dumps(
            {
                "question_id": "q2",
                "category": "MR",
                "judgment": "incorrect",
            }
        )
    )

    monkeypatch.setattr("orchestrator.eval.runner.print_results", lambda results, accuracy: None)

    payload = runner.score()

    assert payload["result_count"] == 2
    assert runner.score_path.exists()
    saved = json.loads(runner.score_path.read_text())
    assert saved["accuracy"]["IE-user"] == 1.0
    assert saved["accuracy"]["MR"] == 0.0


def test_score_raises_when_no_results_available(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.json"
    write_dataset(dataset_path, [])
    runner = make_runner(tmp_path, dataset_path)

    runner.checkpoint_path.write_text(
        json.dumps(
            {
                "version": 2,
                "dataset_path": str(dataset_path),
                "phases": {
                    "ingest": {
                        "results": {},
                        "status": "completed",
                        "completed_count": 0,
                    },
                    "evaluate": {
                        "results": {},
                        "status": "completed",
                        "completed_count": 0,
                    },
                    "score": {"status": "pending", "accuracy": {}},
                },
            }
        )
    )

    with pytest.raises(FileNotFoundError, match="No evaluation results available"):
        runner.score()
