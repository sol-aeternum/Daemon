"""Synthetic tests for the dev2 pilot selection, arms, volumes and runner.

Every row here is invented: no dataset, provider, database or network access.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from orchestrator import routing_log, safe_logging
from tests.benchmark_longmemeval import dev2_pilot, holdout
from tests.benchmark_longmemeval.dev2_pilot import (
    ALGORITHM,
    ARM_SIZES,
    BENCHMARK_RUN_LOCK_KEY,
    PILOT_SIZE,
    RUN_LOCK_SQL,
    SPEND_SQL,
    STOP_EXTRACTION,
    STOP_GUARD_FAILED,
    STOP_RATE_LIMIT,
    STOP_RUN_LOCK_BUSY,
    STOP_SPEND,
    StopRule,
    acquire_benchmark_run_lock,
    answer_conversation_ids,
    assign_arms,
    checkpoint_invariants,
    conversation_counts_sql,
    count_rate_limits,
    empty_checkpoint,
    expected_sessions_from_dataset,
    failed_rate,
    log_lines_seen_routing_events,
    make_log_reader,
    make_spend_reader,
    pilot_volume,
    read_checkpoint_file,
    run_arm,
    sample_resources_psutil_free,
    select_pilot,
    session_invariants,
    spend_breakdown_reader,
    spend_projection,
    verify_db_counts,
    verify_resume,
    write_arm_datasets,
)

SENTINEL = "SENTINEL-QUESTION-TEXT"
SCRIPT = Path(__file__).with_name("bench_db.sh")


def make_row(index: int, question_type: str, *, abstain: bool = False) -> dict[str, Any]:
    question_id = f"q{index:03d}" + ("_abs" if abstain else "")
    return {
        "question_id": question_id,
        "question_type": question_type,
        "answer_session_ids": [f"answer-{index}"],
        "haystack_session_ids": [f"session-{index}"],
        "haystack_sessions": [[{"role": "user", "content": SENTINEL}]],
        "question": SENTINEL,
        "answer": SENTINEL,
    }


def make_dataset() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(12):
        rows.append(make_row(index, "multi-session", abstain=index % 5 == 0))
    for index in range(12, 22):
        rows.append(make_row(index, "temporal-reasoning"))
    for index in range(22, 30):
        rows.append(make_row(index, "knowledge-update"))
    return rows


def ids_of(rows: list[dict[str, Any]]) -> list[str]:
    return [row["question_id"] for row in rows]


def test_constants_are_fixed() -> None:
    assert PILOT_SIZE == 14
    assert ARM_SIZES == {1: 2, 4: 4, 8: 8}
    assert sum(ARM_SIZES.values()) == PILOT_SIZE
    assert ALGORITHM != holdout.ALGORITHM
    assert ALGORITHM != "dev2-eligible-type-x-abstention-largest-remainder-sha256-v1"


def test_select_pilot_is_deterministic_and_sized() -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    ids = ids_of(dataset)
    first = select_pilot(rows, ids)
    second = select_pilot(rows, ids)
    assert first == second
    assert len(first) == PILOT_SIZE
    assert len(set(first)) == PILOT_SIZE
    assert first == sorted(first)
    assert set(first) <= set(ids)
    shifted = select_pilot(rows, ids, seed=7)
    assert len(shifted) == PILOT_SIZE
    assert shifted != first or select_pilot(rows, ids, seed=8) != first


def test_select_pilot_strata_proportions() -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    selected = select_pilot(rows, ids_of(dataset))
    by_id = {row.question_id: row for row in rows}
    counts = Counter(by_id[question_id].stratum for question_id in selected)
    # 12/10/8 rows, 14 seats: floors 5/4/3 plus the two largest remainders.
    assert counts == {
        "multi-session:abs": 1,
        "multi-session:answerable": 4,
        "temporal-reasoning:answerable": 5,
        "knowledge-update:answerable": 4,
    }
    assert sum(counts.values()) == PILOT_SIZE


def test_select_pilot_ignores_non_dev2_rows() -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    dev2_ids = ids_of(dataset[:20])
    selected = select_pilot(rows, dev2_ids, size=10)
    assert set(selected) <= set(dev2_ids)
    assert len(selected) == 10


def test_select_pilot_requires_every_dev2_id_in_rows() -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset[:10])
    with pytest.raises(ValueError, match="absent"):
        select_pilot(rows, ids_of(dataset))


def test_assign_arms_exact_sizes_partition_and_determinism() -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    pilot_ids = select_pilot(rows, ids_of(dataset))
    arms = assign_arms(rows, pilot_ids)
    assert arms == assign_arms(rows, pilot_ids)
    assert {arm: len(members) for arm, members in arms.items()} == ARM_SIZES
    dealt = [question_id for members in arms.values() for question_id in members]
    assert sorted(dealt) == sorted(pilot_ids)
    assert len(set(dealt)) == PILOT_SIZE


def test_assign_arms_rejects_wrong_pilot_size() -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    with pytest.raises(ValueError, match="capacity"):
        assign_arms(rows, ids_of(dataset)[:10])


def test_write_arm_datasets_writes_full_rows_in_dataset_order(tmp_path: Path) -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    pilot_ids = select_pilot(rows, ids_of(dataset))
    arms = assign_arms(rows, pilot_ids)
    hashes = write_arm_datasets(dataset, arms, tmp_path, dev2_ids=ids_of(dataset), holdout_ids=[])
    for arm, members in arms.items():
        path = tmp_path / f"arm_c{arm}.json"
        data = path.read_bytes()
        assert hashes[arm] == hashlib.sha256(data).hexdigest()
        assert data.endswith(b"\n") and not data.endswith(b"\n\n")
        written = json.loads(data)
        expected = [row for row in dataset if row["question_id"] in set(members)]
        assert written == expected
        assert [row["question_id"] for row in written] == [
            row["question_id"] for row in dataset if row["question_id"] in set(members)
        ]


def test_write_arm_datasets_is_byte_stable(tmp_path: Path) -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    arms = assign_arms(rows, select_pilot(rows, ids_of(dataset)))
    first = write_arm_datasets(
        dataset, arms, tmp_path / "a", dev2_ids=ids_of(dataset), holdout_ids=[]
    )
    second = write_arm_datasets(
        dataset, arms, tmp_path / "b", dev2_ids=ids_of(dataset), holdout_ids=[]
    )
    assert first == second
    for arm in arms:
        name = f"arm_c{arm}.json"
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    # A second write into the same directory must not overwrite.
    with pytest.raises(FileExistsError):
        write_arm_datasets(dataset, arms, tmp_path / "a", dev2_ids=ids_of(dataset), holdout_ids=[])


@pytest.mark.parametrize("field", ["holdout", "non-dev2"])
def test_write_arm_datasets_refuses_before_writing(tmp_path: Path, field: str) -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    pilot_ids = select_pilot(rows, ids_of(dataset))
    arms = {arm: list(members) for arm, members in assign_arms(rows, pilot_ids).items()}
    outsider = "q-holdout-1" if field == "holdout" else "q-unknown-1"
    arms[1] = [*arms[1], outsider]
    kwargs: dict[str, Any] = {
        "dev2_ids": [*ids_of(dataset), outsider],
        "holdout_ids": [],
    }
    if field == "holdout":
        kwargs["holdout_ids"] = [outsider]
    else:
        kwargs["dev2_ids"] = ids_of(dataset)
    with pytest.raises(ValueError):
        write_arm_datasets(dataset, arms, tmp_path, **kwargs)
    assert list(tmp_path.iterdir()) == []


class ExplodingRow(dict):
    """A row that only tolerates a ``question_id`` read."""

    def __getitem__(self, key: str) -> Any:
        if key != "question_id":
            raise AssertionError(f"row field read outside an arm: {key}")
        return super().__getitem__(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key != "question_id":
            raise AssertionError(f"row field read outside an arm: {key}")
        return super().get(key, default)


def guarded_dataset(arms: dict[int, list[str]]) -> list[Any]:
    dataset = make_dataset()
    inside = {question_id for members in arms.values() for question_id in members}
    return [
        row if row["question_id"] in inside else ExplodingRow({"question_id": row["question_id"]})
        for row in dataset
    ]


def test_pilot_volume_reads_nothing_outside_the_arms() -> None:
    rows = holdout.project_metadata(make_dataset())
    arms = assign_arms(rows, select_pilot(rows, ids_of(make_dataset())))
    volume = pilot_volume(guarded_dataset(arms), arms)
    assert set(volume["arms"]) == set(arms)
    assert volume["total"]["estimated_questions"] == PILOT_SIZE


def test_pilot_volume_is_numbers_only() -> None:
    dataset = make_dataset()
    rows = holdout.project_metadata(dataset)
    arms = assign_arms(rows, select_pilot(rows, ids_of(dataset)))
    volume = pilot_volume(dataset, arms)
    serialised = json.dumps(volume)
    assert SENTINEL not in serialised
    assert not any(question_id in serialised for question_id in ids_of(dataset))

    def leaves(value: Any) -> list[Any]:
        if isinstance(value, dict):
            return [leaf for item in value.values() for leaf in leaves(item)]
        return [value]

    assert all(
        isinstance(leaf, (int, float)) and not isinstance(leaf, bool) for leaf in leaves(volume)
    )
    total = volume["total"]
    assert total["estimated_ingest_sessions"] == sum(
        arm["estimated_ingest_sessions"] for arm in volume["arms"].values()
    )


def test_spend_projection_arithmetic() -> None:
    volume = {"total": {"estimated_tokens": 3000, "estimated_ingest_sessions": 7}}
    projected = spend_projection(volume, usd_per_session=0.5, usd_per_1k_tokens=0.25)
    assert projected["estimated_token_scaled_usd"] == pytest.approx(0.75)
    assert projected["estimated_session_scaled_usd"] == pytest.approx(3.5)


def test_spend_projection_rejects_negative_rates() -> None:
    volume = {"total": {"estimated_tokens": 3000, "estimated_ingest_sessions": 7}}
    with pytest.raises(ValueError):
        spend_projection(volume, usd_per_session=-1.0, usd_per_1k_tokens=0.25)


def _db_name_guard_line() -> str:
    """The one guard line that validates BENCH_DB_NAME, extracted verbatim."""
    matches = [
        line
        for line in SCRIPT.read_text().splitlines()
        if '"$DB_NAME" =~ ^daemon_lme_bench[a-z0-9_]*$' in line
    ]
    assert len(matches) == 1
    return matches[0]


def _run_db_name_guard(db_name: str) -> subprocess.CompletedProcess[str]:
    """Run the extracted guard in bash; no database command is ever evaluated."""
    script = 'die() { echo "$*" >&2; exit 3; }\nDB_NAME="$1"\n' + _db_name_guard_line() + "\n"
    return subprocess.run(
        ["bash", "-c", script, "_", db_name], capture_output=True, text=True, check=False
    )


def test_bench_db_script_parses_and_guards_the_db_name() -> None:
    text = SCRIPT.read_text()
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    assert 'DB_NAME="${BENCH_DB_NAME:-daemon_lme_bench}"' in text
    assert dev2_pilot.MANIFEST_NAME == "pilot_manifest.json"
    for accepted in ("daemon_lme_bench", "daemon_lme_bench_v2", "daemon_lme_bench2"):
        assert _run_db_name_guard(accepted).returncode == 0, accepted
    for rejected in (
        'daemon_lme_bench"; DROP DATABASE daemon; --',
        "daemon_lme_bench;DROP DATABASE postgres",
        "daemon_lme_benchV2",
        "daemon_lme_bench-2",
        "daemon_lme_bench db",
        "xdaemon_lme_bench",
        "",
    ):
        result = _run_db_name_guard(rejected)
        assert result.returncode == 3, rejected
        assert "must match" in result.stderr


class FakeClock:
    """Monotonic fake clock; ``sleep`` advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 1.0
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeProcess:
    """Alive for ``polls`` checks, then exits; an interrupt ends it at once."""

    def __init__(self, polls: int = 3, exit_code: int = 0, ignore_interrupts: int = 0) -> None:
        self.remaining = polls
        self.exit_code = exit_code
        self.interrupts = 0
        self.ignore_interrupts = ignore_interrupts
        self.ignored = 0
        self.waits = 0
        self.commands: list[Sequence[str]] = []

    def poll(self) -> int | None:
        if self.interrupts:
            if self.ignored < self.ignore_interrupts:
                self.ignored += 1
                return None
            return self.exit_code
        if self.remaining <= 0:
            return self.exit_code
        self.remaining -= 1
        return None

    def wait(self) -> int:
        self.waits += 1
        return self.exit_code


def _spend_reader(values: Sequence[float]) -> Any:
    state = {"index": 0}

    def read() -> float:
        index = state["index"]
        state["index"] = min(index + 1, len(values) - 1)
        return values[index]

    return read


def _checkpoint(sessions: Sequence[str], failures: Sequence[str] = ()) -> dict[str, Any]:
    results: dict[str, dict[str, str]] = {f"q1:{s}": {"status": "complete"} for s in sessions}
    results.update({f"q1:{s}": {"status": "error"} for s in failures})
    return {"phases": {"ingest": {"results": results, "pending_sessions": {}}}}


def _extract(
    checkpoint: Mapping[str, Any], question_id: str
) -> tuple[Sequence[str], Sequence[str]]:
    return dev2_pilot.default_session_extractor(checkpoint, question_id)


class Harness:
    def __init__(
        self,
        *,
        spend: Sequence[float] = (0.0,),
        log_lines: Sequence[str] = (),
        checkpoint: Mapping[str, Any] | None = None,
        process: FakeProcess | None = None,
    ) -> None:
        self.clock = FakeClock()
        self.process = process or FakeProcess()
        self.spend = spend
        self.log_lines = list(log_lines)
        self.checkpoint: Mapping[str, Any] = checkpoint or _checkpoint([])
        self.sent: list[FakeProcess] = []

    def spawn(self, command: Sequence[str]) -> FakeProcess:
        self.process.commands.append(command)
        return self.process

    def send_interrupt(self, process: FakeProcess) -> None:
        process.interrupts += 1
        self.sent.append(process)

    def run(self, **overrides: Any) -> dict[str, Any]:
        settings: dict[str, Any] = {
            "command": ["ingest"],
            "spawn": self.spawn,
            "read_spend_usd": _spend_reader(self.spend),
            "read_log_lines": lambda: list(self.log_lines),
            "read_checkpoint": lambda: self.checkpoint,
            "sample_resources": lambda: {"cpu_percent": 10.0, "rss_bytes": 2048.0},
            "clock": self.clock,
            "sleep": self.clock.advance,
            "poll_seconds": 1.0,
            "stop_rule": StopRule(cap_usd=6.0, margin_usd=1.0, max_rate_limited=10),
            "send_interrupt": self.send_interrupt,
        }
        settings.update(overrides)
        return run_arm(**settings)


def _rate_limited_lines(count: int) -> list[str]:
    record = '{"event":"attempt_outcome","failure_category":"rate_limited"}'
    line = f"2026-11-04 10:00:00,001 INFO daemon.routing routing_event {record}"
    return [line] * count


def test_spend_threshold_stops_with_exactly_one_sigint() -> None:
    harness = Harness(spend=(0.0, 1.0, 2.0, 5.2, 5.2), process=FakeProcess(polls=10))
    result = harness.run()
    assert result["stop_reason"] == STOP_SPEND
    assert result["exit_code"] == 0
    assert len(harness.sent) == 1
    assert harness.process.interrupts == 1
    assert [item["kind"] for item in result["interrupts"]] == ["stop"]
    assert result["spend_usd"] == 5.2


def test_rate_limit_loop_stops() -> None:
    harness = Harness(
        log_lines=_rate_limited_lines(4),
        process=FakeProcess(polls=10),
    )
    result = harness.run(stop_rule=StopRule(cap_usd=6.0, margin_usd=1.0, max_rate_limited=3))
    assert result["stop_reason"] == STOP_RATE_LIMIT
    assert result["rate_limited_events"] == 4
    assert len(harness.sent) == 1


def test_failure_rate_gate_stops() -> None:
    sessions = [f"s{index}" for index in range(50)]
    harness = Harness(
        checkpoint=_checkpoint(sessions, ["bad"]),
        process=FakeProcess(polls=10),
    )
    result = harness.run()
    assert result["stop_reason"] == STOP_EXTRACTION
    assert result["failed_sessions"] == 1
    assert len(harness.sent) == 1


def test_interrupt_for_resume_fires_once_after_n_completed() -> None:
    states = [
        checkpoint_invariants(_checkpoint([])),
        checkpoint_invariants(_checkpoint(["s0"])),
        checkpoint_invariants(_checkpoint(["s0"])),
    ]
    calls = {"index": 0}

    def read_checkpoint() -> Mapping[str, Any]:
        index = min(calls["index"], len(states) - 1)
        calls["index"] += 1
        return _checkpoint([] if index == 0 else ["s0"])

    harness = Harness(process=FakeProcess(polls=10))
    result = harness.run(
        read_checkpoint=read_checkpoint,
        interrupt_after_completed=1,
        expected_sessions={"q1": 1},
    )
    assert result["interrupted_for_resume"] is True
    assert result["stop_reason"] is None
    assert len(harness.sent) == 1
    assert [item["kind"] for item in result["interrupts"]] == ["resume"]


def test_normal_completion_records_no_stop_reason() -> None:
    reads = {"count": 0}

    def read_checkpoint() -> Mapping[str, Any]:
        # Empty at invocation start; both sessions finish during this invocation.
        reads["count"] += 1
        return _checkpoint([] if reads["count"] == 1 else ["s0", "s1"])

    harness = Harness(process=FakeProcess(polls=2))
    result = harness.run(read_checkpoint=read_checkpoint)
    assert result["stop_reason"] is None
    assert result["interrupted_for_resume"] is False
    assert result["interrupts"] == []
    assert harness.process.interrupts == 0
    assert result["completed_sessions"] == 2
    assert result["completed_sessions_this_invocation"] == 2
    assert result["sessions_per_hour"] > 0


def test_second_sigint_only_after_grace() -> None:
    harness = Harness(process=FakeProcess(polls=200, ignore_interrupts=4))
    result = harness.run(
        poll_seconds=10.0,
        grace_seconds=20.0,
        stop_rule=StopRule(cap_usd=6.0, margin_usd=1.0, max_rate_limited=10),
        read_checkpoint=lambda: _checkpoint(["s0"]),
        interrupt_after_completed=1,
        expected_sessions={"q1": 1},
    )
    kinds = [item["kind"] for item in result["interrupts"]]
    assert kinds[0] == "resume"
    assert kinds.count("grace") == 1
    assert len(harness.sent) == 2


def _duplicating(sessions: Sequence[str], failed: Sequence[str] = ()) -> Any:
    """Checkpoint keys are unique; a duplicate can only come from another extractor."""
    return lambda _checkpoint, _question_id: (list(sessions), list(failed))


def test_session_invariants_counts_and_duplicates() -> None:
    invariants = session_invariants({}, ["q1"], extract=_duplicating(["a", "a", "b"], ["b"]))
    assert invariants["total_sessions"] == 3
    assert invariants["failed_sessions"] == 1
    assert invariants["completed_sessions"] == 2
    assert invariants["duplicate_sessions"] == 1
    assert invariants["questions"]["q1"]["duplicate_sessions"] == 1
    assert failed_rate(invariants) == 1 / 3


def test_checkpoint_invariants_reads_every_question() -> None:
    checkpoint = {
        "phases": {
            "ingest": {
                "results": {
                    "q1:a": {"status": "complete"},
                    "q2:b": {"status": "complete"},
                    "q2:c": {"status": "complete"},
                    "q2:d": {"status": "error"},
                }
            }
        }
    }
    invariants = checkpoint_invariants(checkpoint)
    assert invariants["total_sessions"] == 4
    assert invariants["failed_sessions"] == 1


def test_a_foreign_checkpoint_shape_raises_instead_of_reading_as_no_failures() -> None:
    with pytest.raises(KeyError):
        checkpoint_invariants({"results": {"q1": {"sessions": ["a"]}}})


def test_verify_resume_passes_clean_resume() -> None:
    before = session_invariants(_checkpoint(["a", "b"]), ["q1"], extract=_extract)
    after = session_invariants(_checkpoint(["a", "b", "c"]), ["q1"], extract=_extract)
    assert verify_resume(before, after, 3) == []


def test_verify_resume_flags_duplicate_lost_mismatch_and_failures() -> None:
    before = session_invariants(_checkpoint(["a", "b"]), ["q1"], extract=_extract)
    duplicated = session_invariants(
        {}, ["q1"], extract=_duplicating(["a", "b", "a", "bad"], ["bad"])
    )
    violations = verify_resume(before, duplicated, 3)
    assert any("duplicated" in item for item in violations)
    assert any("does not match" in item for item in violations)
    assert any("extraction_failed" in item for item in violations)

    lost = session_invariants(_checkpoint(["a"]), ["q1"], extract=_extract)
    lost_violations = verify_resume(before, lost, 1)
    assert any("lost" in item for item in lost_violations)

    per_question = verify_resume(before, lost, {"q1": 1})
    assert not any("does not match" in item for item in per_question)


def test_count_rate_limits_tolerates_junk_lines() -> None:
    marker = "2026-11-04 10:00:00,001 INFO daemon.routing routing_event "
    lines = [
        "",
        "not json at all",
        marker + '{"event":"attempt_outcome","failure_category":"rate_limited"}',
        marker + '{"event":"attempt_outcome","failure_category":"rate_limited"}',
        marker + '{"event":"attempt_outcome","failure_category":"timeout"}',
        marker + '{"event":"other","failure_category":"rate_limited"}',
        marker + '{"event":"attempt_outcome"}',
        marker + '{"event":"attempt_outcome","failure_category":5}',
        marker + "[1, 2, 3]",
        '{"event":"attempt_outcome","failure_category":"rate_limited"}',
    ]
    counts = count_rate_limits(lines)
    assert counts == {"rate_limited": 3, "timeout": 1}
    assert all(isinstance(value, int) for value in counts.values())


def test_prepare_reads_ids_from_real_manifests_and_enforces_the_holdout_guard(
    tmp_path: Path,
) -> None:
    fixtures = Path(__file__).parent / "fixtures"
    for name in ("dev2_ids.json", "holdout_ids.json"):
        manifest = json.loads((fixtures / name).read_text())
        assert set(dev2_pilot._manifest_ids(manifest, name)) == set(manifest["question_ids"])
        with pytest.raises(ValueError, match="manifest"):
            dev2_pilot._manifest_ids(manifest["question_ids"], name)
    with pytest.raises(ValueError, match="manifest"):
        dev2_pilot._manifest_ids({"size": 1}, "dev2_ids")
    rows = [
        {"question_id": f"q{i}", "question_type": "multi-session", "answer_session_ids": [f"s{i}"]}
        for i in range(20)
    ]
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps(rows))
    dev2 = tmp_path / "dev2.json"
    dev2.write_text(json.dumps({"question_ids": [f"q{i}" for i in range(20)]}))
    overlapping = tmp_path / "holdout.json"
    overlapping.write_text(json.dumps({"question_ids": [f"q{i}" for i in range(20)]}))
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError):
        dev2_pilot.prepare(
            dataset_path=dataset, dev2_path=dev2, holdout_path=overlapping, out_dir=out
        )
    assert list(out.iterdir()) == []


def test_default_extractor_reads_the_real_checkpoint_shape() -> None:
    checkpoint = {
        "phases": {
            "ingest": {
                "results": {
                    "q1:a": {"status": "complete"},
                    "q1:b": {"status": "error"},
                    "q10:c": {"status": "complete"},
                }
            }
        }
    }
    sessions, failed = dev2_pilot.default_session_extractor(checkpoint, "q1")
    assert sorted(sessions) == ["a", "b"]
    assert failed == ["b"]


# --- Real failure path: pending_sessions -------------------------------------

PENDING_FAILURE: dict[str, Any] = {
    "corpus_key": "corpus-b",
    "started_at": "2026-11-04T00:00:00+00:00",
    "state_fingerprint": "benchmark-state",
    "failure": {
        "at": "2026-11-04T00:00:01+00:00",
        "session_id": "s-b",
        "status": "error",
        "error": "TimeoutError",
        "chunks_failed": 1,
        "chunk_errors": {"extraction": 1},
    },
}


def _pending_checkpoint(pending: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "phases": {
            "ingest": {
                "results": {"q1:a": {"status": "complete"}},
                "pending_sessions": dict(pending),
            }
        }
    }


def test_a_real_pending_failure_counts_as_a_failed_session_and_trips_the_gate() -> None:
    checkpoint = _pending_checkpoint({"q1": PENDING_FAILURE})
    sessions, failed = dev2_pilot.default_session_extractor(checkpoint, "q1")
    assert sorted(sessions) == ["a", "s-b"]
    assert failed == ["s-b"]
    invariants = checkpoint_invariants(checkpoint)
    assert invariants["total_sessions"] == 2
    assert invariants["failed_sessions"] == 1
    assert failed_rate(invariants) == pytest.approx(0.5)

    harness = Harness(checkpoint=checkpoint, process=FakeProcess(polls=10))
    result = harness.run()
    assert result["stop_reason"] == STOP_EXTRACTION
    assert harness.process.interrupts == 1


def test_an_in_flight_pending_entry_is_neither_failed_nor_completed() -> None:
    in_flight = {key: value for key, value in PENDING_FAILURE.items() if key != "failure"}
    checkpoint = _pending_checkpoint({"q1": in_flight})
    sessions, failed = dev2_pilot.default_session_extractor(checkpoint, "q1")
    assert sessions == ["a"]
    assert failed == []
    invariants = checkpoint_invariants(checkpoint)
    assert invariants["total_sessions"] == 1
    assert invariants["failed_sessions"] == 0
    assert invariants["completed_sessions"] == 1
    assert Harness(checkpoint=checkpoint).run()["stop_reason"] is None


def test_checkpoint_invariants_includes_a_pending_only_question() -> None:
    checkpoint = _pending_checkpoint({"q2": {**PENDING_FAILURE, "corpus_key": "other"}})
    invariants = checkpoint_invariants(checkpoint)
    assert set(invariants["questions"]) == {"q1", "q2"}
    assert invariants["questions"]["q2"]["failed"] == 1
    assert invariants["duplicate_sessions"] == 0


# --- Real log lines: routing_event -------------------------------------------


def test_count_rate_limits_parses_real_routing_event_lines() -> None:
    lines = [
        '2026-11-04 10:00:00,001 INFO daemon.routing routing_event {"event":"attempt_outcome",'
        '"failure_category":"rate_limited","attempt":0}',
        'routing_event {"failure_category":"rate_limited","event":"attempt_outcome"}',
        'prefix routing_event {"event":"attempt_outcome","failure_category":"timeout"}',
        "routing_event not json",
        "routing_event [1, 2]",
        'daemon.routing routing_eventx {"event":"attempt_outcome","failure_category":"junk"}',
        "",
    ]
    assert count_rate_limits(lines) == {"rate_limited": 2, "timeout": 1}
    assert log_lines_seen_routing_events(lines) == 3
    assert count_rate_limits(["routing_event not json"]) == {}
    assert log_lines_seen_routing_events([]) == 0


def test_run_arm_reports_how_many_routing_events_the_log_carried() -> None:
    harness = Harness(log_lines=_rate_limited_lines(1), process=FakeProcess(polls=2))
    result = harness.run()
    assert result["routing_events_seen"] == 1
    assert result["rate_limits"] == {"rate_limited": 1}
    empty = Harness(process=FakeProcess(polls=2)).run()
    assert empty["routing_events_seen"] == 0
    assert empty["rate_limits"] == {}


# --- run_arm guard failure ----------------------------------------------------


def test_a_reader_raising_is_a_guard_failure_that_still_drains_the_child() -> None:
    harness = Harness(process=FakeProcess(polls=50))

    def broken_checkpoint() -> Mapping[str, Any]:
        raise RuntimeError("checkpoint unreadable")

    result = harness.run(read_checkpoint=broken_checkpoint)
    assert result["stop_reason"] == STOP_GUARD_FAILED
    assert result["guard_error"] == "RuntimeError"
    assert [item["kind"] for item in result["interrupts"]] == ["guard"]
    assert len(harness.sent) == 1
    assert harness.process.interrupts == 1
    assert harness.process.waits == 1
    assert result["spend_after_usd"] is None
    assert result["spend_usd"] is None


def test_a_missing_checkpoint_before_the_first_save_is_an_empty_shell(tmp_path: Path) -> None:
    path = tmp_path / "longmemeval_checkpoint.json"
    assert read_checkpoint_file(path) == empty_checkpoint()
    invariants = checkpoint_invariants(read_checkpoint_file(path))
    assert invariants["total_sessions"] == 0
    assert invariants["failed_sessions"] == 0
    path.write_text(json.dumps(_checkpoint(["a"])))
    assert checkpoint_invariants(read_checkpoint_file(path))["total_sessions"] == 1
    not_an_object = tmp_path / "bad.json"
    not_an_object.write_text("[1, 2]")
    with pytest.raises(ValueError):
        read_checkpoint_file(not_an_object)


# --- Live wiring --------------------------------------------------------------


def test_spend_sql_is_whole_database_settled_plus_open_holds() -> None:
    sql = SPEND_SQL.lower()
    assert "from entitlement_reservations" in sql
    assert "actual_microusd" in sql
    assert "reserved_microusd" in sql
    assert "status = 'settled'" in sql
    assert "status = 'open'" in sql
    assert "user_id" not in sql
    assert "since" not in sql
    assert "created_at" not in sql


def test_make_spend_reader_sums_settled_and_open_holds() -> None:
    quoted: list[str] = []

    def fetchrow(sql: str) -> Mapping[str, Any]:
        quoted.append(sql)
        return {"settled_microusd": 1_500_000, "held_microusd": 500_000}

    assert make_spend_reader(fetchrow)() == pytest.approx(2.0)
    breakdown = spend_breakdown_reader(fetchrow)()
    assert breakdown.settled_usd == pytest.approx(1.5)
    assert breakdown.held_usd == pytest.approx(0.5)
    assert breakdown.total_usd == pytest.approx(2.0)
    assert quoted == [SPEND_SQL, SPEND_SQL]
    assert make_spend_reader(lambda _sql: None)() == 0.0


def test_make_log_reader_reads_a_captured_child_log(tmp_path: Path) -> None:
    path = tmp_path / "arm.log"
    read = make_log_reader(path)
    assert read() == []
    path.write_text("\n".join(_rate_limited_lines(2)) + "\n")
    assert log_lines_seen_routing_events(read()) == 2
    assert count_rate_limits(read()) == {"rate_limited": 2}


def test_verify_db_counts_compares_per_user_and_ignores_other_arms() -> None:
    rows = [
        {"user_id": "u1", "conversations": 3},
        {"user_id": "u2", "conversations": 2},
        {"user_id": "u9", "conversations": 7},
    ]
    assert verify_db_counts(rows, {"u1": 3, "u2": 2}) == []
    violations = verify_db_counts(rows, {"u1": 4, "u2": 2, "u3": 1})
    assert len(violations) == 2
    assert any("u1 has 3" in item for item in violations)
    assert any("u3 has 0" in item for item in violations)
    sql = conversation_counts_sql()
    assert "user_id::text" in sql
    assert "FROM conversations" in sql


def test_expected_sessions_from_dataset_deduplicates_like_the_harness() -> None:
    first = [{"role": "user", "content": "alpha"}]
    second = [{"role": "user", "content": "beta"}]
    rows = [
        {
            "question_id": "q1",
            "haystack_sessions": [first, first, second],
            "haystack_session_ids": ["s1", "s2", "s3"],
        },
        {"question_id": "q2", "haystack_sessions": [second]},
        {"question_id": "q3", "haystack_sessions": ["not a session"]},
    ]
    assert expected_sessions_from_dataset(rows) == {"q1": 2, "q2": 1, "q3": 0}
    with pytest.raises(ValueError):
        expected_sessions_from_dataset([rows[0], rows[0]])


def test_answer_conversation_ids_maps_sessions_and_raises_on_a_missing_one() -> None:
    checkpoint = {
        "phases": {
            "ingest": {
                "results": {
                    "q1:corpus-a": {
                        "status": "complete",
                        "session_id": "s1",
                        "conversation_id": "c1",
                    },
                    "q1:corpus-b": {
                        "status": "complete",
                        "session_id": "s2",
                        "conversation_id": "c2",
                    },
                    "q2:corpus-c": {
                        "status": "complete",
                        "session_id": "s3",
                        "conversation_id": "c3",
                    },
                },
                "pending_sessions": {},
            }
        }
    }
    assert answer_conversation_ids(checkpoint, "q1", ["s2", "s1"]) == {
        "s2": "c2",
        "s1": "c1",
    }
    with pytest.raises(ValueError, match="no completed ingest result"):
        answer_conversation_ids(checkpoint, "q1", ["s1", "s3"])


def test_sample_resources_psutil_free_reads_proc_without_psutil(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    (proc / "self").mkdir(parents=True)
    (proc / "loadavg").write_text("0.50 0.40 0.30 1/100 1234\n")
    (proc / "meminfo").write_text(
        "MemTotal:       1000 kB\nMemFree:  1 kB\nMemAvailable:    400 kB\n"
    )
    (proc / "self" / "statm").write_text("100 25 10 1 0 5 0\n")
    sample = sample_resources_psutil_free(proc)
    assert sample["load1"] == pytest.approx(0.5)
    assert sample["mem_total_bytes"] == pytest.approx(1_024_000.0)
    assert sample["mem_available_bytes"] == pytest.approx(409_600.0)
    assert sample["rss_bytes"] > 0
    assert sample["disk_free_bytes"] > 0
    absent = sample_resources_psutil_free(tmp_path / "absent")
    assert "load1" not in absent
    assert "mem_total_bytes" not in absent
    assert "disk_free_bytes" in absent


# --- ``run-arm`` command ------------------------------------------------------
#
# The command is driven with fakes only: no process, database, provider or
# dataset read happens here.


class _FakeProcess:
    """A child that reports one live poll, then exits cleanly."""

    pid = 4242

    def __init__(self) -> None:
        self.live_polls = 1

    def poll(self) -> int | None:
        if self.live_polls:
            self.live_polls -= 1
            return None
        return 0

    def wait(self) -> int:
        return 0


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _FakeRunLock:
    """An injected benchmark run lock that records acquisition and release."""

    def __init__(self, acquired: bool = True, error: BaseException | None = None) -> None:
        self.acquired = acquired
        self.error = error
        self.acquire_calls = 0
        self.releases = 0

    def acquire(self) -> bool:
        self.acquire_calls += 1
        if self.error is not None:
            raise self.error
        return self.acquired

    def release(self) -> None:
        self.releases += 1


def _arm_rows() -> list[dict[str, Any]]:
    return [{"question_id": "q1", "haystack_sessions": []}]


def _run_arm_kwargs(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    dataset = tmp_path / "arm.json"
    dataset.write_text(json.dumps(_arm_rows()), encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir(exist_ok=True)
    clock = _Clock()
    kwargs: dict[str, Any] = {
        "arm_dataset": dataset,
        "output_dir": output,
        "concurrency": 2,
        "cap_usd": 8.5,
        "margin_usd": 1.0,
        "max_rate_limited": 3,
        "record_path": tmp_path / "record.json",
        "database_url": "postgresql://u:p@localhost/daemon_lme_bench_dev2",
        "status_porcelain": "",
        "code_sha": "c" * 40,
        "dataset_rows": _arm_rows(),
        "spawn": lambda command: _FakeProcess(),
        "fetchrow_factory": lambda: lambda sql: {"settled_microusd": 0, "held_microusd": 0},
        "sample_resources": dict,
        "sleep": clock.sleep,
        "clock": clock,
        "send_interrupt": lambda process: None,
        "now": lambda: "2026-11-04T00:00:00+00:00",
        "run_lock": _FakeRunLock(),
    }
    kwargs.update(overrides)
    return kwargs


def test_child_command_is_exact_and_weakening_flags_are_absent() -> None:
    command = dev2_pilot.child_command(
        arm_dataset=Path("arm.json"),
        output_dir=Path("out"),
        concurrency=4,
        python="/usr/bin/python3",
    )
    assert command == [
        "/usr/bin/python3",
        "-m",
        "orchestrator.eval.longmemeval",
        "ingest",
        "--dataset",
        "arm.json",
        "--output-dir",
        "out",
        "--concurrency",
        "4",
    ]
    for flag in dev2_pilot.FORBIDDEN_CHILD_FLAGS:
        assert flag not in command


def test_db_name_from_dsn_ignores_credentials_and_refuses_query() -> None:
    assert dev2_pilot.db_name_from_dsn("postgresql://u:p@h:5432/daemon_lme_bench_dev2") == (
        "daemon_lme_bench_dev2"
    )
    # Any query string is refused: libpq honours a dbname parameter there.
    assert dev2_pilot.db_name_from_dsn("postgresql://u:p@h/daemon_lme_bench_dev2?sslmode=x") is None
    assert dev2_pilot.db_name_from_dsn("") is None
    assert dev2_pilot.check_run_arm_database("postgresql://u:p@h/daemon_lme_bench_dev2") == (
        "daemon_lme_bench_dev2"
    )


@pytest.mark.parametrize(
    "dsn",
    [
        None,
        "",
        "postgresql://u:p@localhost/daemon_lme_bench",
        "postgresql://u:p@localhost/daemon_lme_bench_dev2_backup",
        "postgresql://u:p@localhost/postgres",
    ],
)
def test_run_arm_refuses_a_wrong_database_before_spawning(tmp_path: Path, dsn: str | None) -> None:
    spawned: list[Sequence[str]] = []
    with pytest.raises(ValueError):
        dev2_pilot.run_arm_command(
            **_run_arm_kwargs(
                tmp_path,
                database_url=dsn,
                spawn=lambda command: spawned.append(command) or _FakeProcess(),
            )
        )
    assert spawned == []


def test_run_arm_refuses_a_dirty_tree_before_spawning(tmp_path: Path) -> None:
    spawned: list[Sequence[str]] = []
    with pytest.raises(ValueError, match="clean git tree"):
        dev2_pilot.run_arm_command(
            **_run_arm_kwargs(
                tmp_path,
                status_porcelain=" M tests/benchmark_longmemeval/dev2_pilot.py\n",
                spawn=lambda command: spawned.append(command) or _FakeProcess(),
            )
        )
    assert spawned == []


def test_interrupt_run_refuses_an_existing_checkpoint_without_resume(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    dev2_pilot.checkpoint_path(output).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="--resume"):
        dev2_pilot.check_interrupt_target(output, interrupt_after_completed=1, resume=False)
    dev2_pilot.check_interrupt_target(output, interrupt_after_completed=1, resume=True)
    dev2_pilot.check_interrupt_target(output, interrupt_after_completed=None, resume=False)


def test_run_arm_refuses_a_checkpoint_without_resume_before_spawning(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()
    dev2_pilot.checkpoint_path(output).write_text("{}", encoding="utf-8")
    spawned: list[Sequence[str]] = []
    with pytest.raises(ValueError, match="--resume"):
        dev2_pilot.run_arm_command(
            **_run_arm_kwargs(
                tmp_path,
                interrupt_after_completed=2,
                spawn=lambda command: spawned.append(command) or _FakeProcess(),
            )
        )
    assert spawned == []


def test_run_arm_wires_real_readers_and_writes_the_record(tmp_path: Path) -> None:
    dataset = tmp_path / "arm.json"
    spawned: list[Sequence[str]] = []
    spend_sql: list[str] = []

    def fetchrow(sql: str) -> Mapping[str, Any] | None:
        spend_sql.append(sql)
        return {"settled_microusd": 1_000_000, "held_microusd": 0}

    record, exit_code = dev2_pilot.run_arm_command(
        **_run_arm_kwargs(
            tmp_path,
            spawn=lambda command: spawned.append(command) or _FakeProcess(),
            fetchrow_factory=lambda: fetchrow,
        )
    )

    assert exit_code == 0
    assert record["run_arm"]["stop_reason"] is None
    assert record["code_sha"] == "c" * 40
    assert record["arm_dataset_sha256"] == hashlib.sha256(dataset.read_bytes()).hexdigest()
    assert record["started_utc"] == record["ended_utc"] == "2026-11-04T00:00:00+00:00"
    assert json.loads((tmp_path / "record.json").read_text(encoding="utf-8")) == record
    assert spend_sql == [SPEND_SQL] * 3
    assert spawned == [
        dev2_pilot.child_command(
            arm_dataset=tmp_path / "arm.json",
            output_dir=tmp_path / "out",
            concurrency=2,
            python=sys.executable,
        )
    ]


def test_run_arm_exits_nonzero_when_a_stop_reason_is_set(tmp_path: Path) -> None:
    record, exit_code = dev2_pilot.run_arm_command(
        **_run_arm_kwargs(
            tmp_path,
            cap_usd=1.0,
            margin_usd=0.5,
            fetchrow_factory=lambda: lambda sql: {"settled_microusd": 900_000, "held_microusd": 0},
        )
    )
    assert record["run_arm"]["stop_reason"] == STOP_SPEND
    assert exit_code == 1


def test_write_record_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    dev2_pilot.write_record(path, {"a": 1})
    with pytest.raises(FileExistsError):
        dev2_pilot.write_record(path, {"a": 2})


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://b:b@127.0.0.1:55432/%64aemon_lme_bench",
        "postgresql://b:b@127.0.0.1:55432/%64aemon_lme_bench_dev2",
        "postgresql://b:b@127.0.0.1:55432/daemon_lme_bench_dev2?dbname=daemon_lme_bench",
        "postgresql://b:b@127.0.0.1:55432/daemon_lme_bench",
    ],
)
def test_run_arm_database_guard_refuses_encoded_and_overridden_names(dsn: str) -> None:
    with pytest.raises(ValueError):
        dev2_pilot.check_run_arm_database(dsn)


def test_run_arm_database_guard_accepts_the_exact_name() -> None:
    dsn = "postgresql://b:b@127.0.0.1:55432/daemon_lme_bench_dev2"
    assert dev2_pilot.check_run_arm_database(dsn) == "daemon_lme_bench_dev2"


def test_output_dir_must_be_outside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError):
        dev2_pilot.check_output_outside_repo(repo / "out", repo)
    dev2_pilot.check_output_outside_repo(tmp_path / "out", repo)


def test_rate_limit_count_reads_what_the_real_handler_writes() -> None:
    record = routing_log.build_record(
        "attempt_outcome", {"outcome": "failed", "failure_category": "rate_limited"}
    )
    stream = io.StringIO()
    handler = safe_logging.SafeHandler(stream)
    handler.emit(
        logging.makeLogRecord(
            {"levelno": logging.INFO, "_daemon_event": {"kind": "routing", "fields": record}}
        )
    )
    handler.emit(logging.makeLogRecord({"levelno": logging.INFO, "msg": "other"}))
    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    assert dev2_pilot.count_rate_limits(lines) == {"rate_limited": 1}
    assert dev2_pilot.log_lines_seen_routing_events(lines) == 1


def test_child_failure_is_reported_as_a_stop_not_success() -> None:
    process = FakeProcess(polls=1, exit_code=1)
    result = dev2_pilot.run_arm(
        command=["ingest"],
        spawn=lambda _command: process,
        read_spend_usd=lambda: 0.0,
        read_log_lines=lambda: [],
        read_checkpoint=lambda: _checkpoint(["a"]),
        sample_resources=lambda: {},
        clock=lambda: 0.0,
        sleep=lambda _seconds: None,
        poll_seconds=1.0,
        stop_rule=StopRule(cap_usd=6.0, margin_usd=1.0, max_rate_limited=10),
        send_interrupt=lambda _process: None,
    )
    assert result["exit_code"] == 1
    assert result["stop_reason"] == dev2_pilot.STOP_CHILD_FAILED


def test_spend_gate_wins_a_poll_that_also_satisfies_the_planned_interrupt() -> None:
    """Hard-stop gates are evaluated first: $9.00 spend beats a due resume interrupt."""
    harness = Harness(
        spend=(0.0, 9.0),
        checkpoint=_checkpoint(["s1"]),
        process=FakeProcess(polls=10, exit_code=1),
    )
    result = harness.run(
        stop_rule=StopRule(cap_usd=8.5, margin_usd=0.0, max_rate_limited=10),
        interrupt_after_completed=1,
        expected_sessions={"q1": 1},
    )
    assert result["stop_reason"] == dev2_pilot.STOP_SPEND
    assert result["interrupted_for_resume"] is False
    assert [item["kind"] for item in result["interrupts"]] == ["stop"]
    # A non-None stop reason is what makes run_arm_command exit 1.
    assert result["exit_code"] == 1


def test_throughput_counts_only_sessions_completed_in_this_invocation() -> None:
    """170 already complete, 185 at the end and 300 s elapsed is 180 sessions/hour."""
    before = [f"s{index}" for index in range(170)]
    after = [f"s{index}" for index in range(185)]
    reads = {"count": 0}

    def read_checkpoint() -> Mapping[str, Any]:
        reads["count"] += 1
        # The first read is the invocation-start snapshot; later reads are the run.
        return _checkpoint(before if reads["count"] == 1 else after)

    moments = [0.0] + [300.0] * 8

    def clock() -> float:
        return moments.pop(0) if len(moments) > 1 else 300.0

    result = Harness(process=FakeProcess(polls=1)).run(read_checkpoint=read_checkpoint, clock=clock)

    assert result["wall_seconds"] == 300.0
    assert result["completed_sessions"] == 185
    assert result["completed_sessions_this_invocation"] == 15
    assert result["sessions_per_hour"] == 180.0


def test_resume_appends_to_the_child_log_and_keeps_earlier_routing_events(
    tmp_path: Path,
) -> None:
    """A second invocation appends: earlier lines survive and the guard tally spans both."""
    log = tmp_path / "child.log"
    routing = _rate_limited_lines(1)[0]
    spawn = dev2_pilot.make_spawn(log)

    for _ in range(2):
        process = spawn([sys.executable, "-c", f"print({routing!r})"])
        assert process.wait() == 0

    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines.count(routing) == 2
    assert dev2_pilot.count_rate_limits(lines)["rate_limited"] == 2
    separators = [line for line in lines if line.startswith(dev2_pilot.CHILD_LOG_SEPARATOR)]
    assert len(separators) == 2
    # The separator is one line per invocation and is not a routing event.
    assert dev2_pilot.log_lines_seen_routing_events(lines) == 2


def test_final_spend_over_the_cap_is_a_stop_when_no_poll_saw_it() -> None:
    """7.40 at the last poll and 8.70 on the final read is still a hard stop."""
    harness = Harness(spend=(0.0, 7.4, 8.7), process=FakeProcess(polls=1))
    result = harness.run(stop_rule=StopRule(cap_usd=9.5, margin_usd=1.0, max_rate_limited=10))
    assert result["stop_reason"] == STOP_SPEND
    assert result["interrupted_for_resume"] is False
    # The child had already exited, so the final gate interrupts nothing.
    assert result["interrupts"] == []
    assert result["spend_usd"] == pytest.approx(8.7)


def test_resume_whose_drain_crosses_the_cap_is_a_stop_not_a_clean_resume() -> None:
    harness = Harness(
        spend=(0.0, 5.2, 8.7),
        checkpoint=_checkpoint(["s0"]),
        process=FakeProcess(polls=2),
    )
    result = harness.run(
        stop_rule=StopRule(cap_usd=9.5, margin_usd=1.0, max_rate_limited=10),
        interrupt_after_completed=1,
        expected_sessions={"q1": 1},
    )
    assert result["interrupted_for_resume"] is False
    assert result["stop_reason"] == STOP_SPEND
    assert [item["kind"] for item in result["interrupts"]] == ["resume"]


def test_final_readings_under_the_cap_keep_a_clean_run_clean() -> None:
    harness = Harness(spend=(0.0, 8.4, 8.4), process=FakeProcess(polls=1))
    result = harness.run(stop_rule=StopRule(cap_usd=9.5, margin_usd=1.0, max_rate_limited=10))
    assert result["stop_reason"] is None
    assert result["interrupted_for_resume"] is False


def test_run_arm_command_exits_nonzero_when_only_the_final_read_crosses_the_cap(
    tmp_path: Path,
) -> None:
    # cap 8.50, margin 1.00: the 7.40 polls are clean, the 8.70 final read is not.
    amounts = iter([7_400_000, 7_400_000, 8_700_000])

    def fetchrow(sql: str) -> Mapping[str, Any]:
        return {"settled_microusd": next(amounts), "held_microusd": 0}

    record, exit_code = dev2_pilot.run_arm_command(
        **_run_arm_kwargs(tmp_path, fetchrow_factory=lambda: fetchrow)
    )
    assert record["run_arm"]["stop_reason"] == STOP_SPEND
    assert exit_code == 1
    assert json.loads((tmp_path / "record.json").read_text(encoding="utf-8")) == record


def test_acquire_benchmark_run_lock_requires_an_exact_true() -> None:
    calls: list[tuple[str, int]] = []

    async def granted(sql: str, key: int) -> bool:
        calls.append((sql, key))
        return True

    async def missing(sql: str, key: int) -> None:
        return None

    async def refused_flag(sql: str, key: int) -> bool:
        return False

    async def refused(sql: str, key: int) -> bool:
        raise RuntimeError("connection lost")

    assert 0 < BENCHMARK_RUN_LOCK_KEY < 2**63
    assert asyncio.run(acquire_benchmark_run_lock(granted)) is True
    assert calls == [(RUN_LOCK_SQL, BENCHMARK_RUN_LOCK_KEY)]
    assert asyncio.run(acquire_benchmark_run_lock(missing)) is False
    assert asyncio.run(acquire_benchmark_run_lock(refused_flag)) is False
    with pytest.raises(RuntimeError, match="connection lost"):
        asyncio.run(acquire_benchmark_run_lock(refused))


def test_a_busy_run_lock_spawns_nothing_and_exits_nonzero(tmp_path: Path) -> None:
    spawned: list[Sequence[str]] = []
    lock = _FakeRunLock(acquired=False)
    record, exit_code = dev2_pilot.run_arm_command(
        **_run_arm_kwargs(
            tmp_path,
            run_lock=lock,
            spawn=lambda command: spawned.append(command) or _FakeProcess(),
        )
    )
    assert spawned == []
    assert exit_code == 1
    assert record["run_arm"]["stop_reason"] == STOP_RUN_LOCK_BUSY
    assert lock.acquire_calls == 1
    assert lock.releases == 1
    assert json.loads((tmp_path / "record.json").read_text(encoding="utf-8")) == record


def test_a_run_lock_that_raises_spawns_nothing_and_exits_nonzero(tmp_path: Path) -> None:
    spawned: list[Sequence[str]] = []
    lock = _FakeRunLock(error=RuntimeError("lock unavailable"))
    record, exit_code = dev2_pilot.run_arm_command(
        **_run_arm_kwargs(
            tmp_path,
            run_lock=lock,
            spawn=lambda command: spawned.append(command) or _FakeProcess(),
        )
    )
    assert spawned == []
    assert exit_code == 1
    assert record["run_arm"]["stop_reason"] == STOP_RUN_LOCK_BUSY
    assert lock.acquire_calls == 1
    assert lock.releases == 1


def test_an_acquired_run_lock_runs_the_child_and_is_released(tmp_path: Path) -> None:
    spawned: list[Sequence[str]] = []
    lock = _FakeRunLock()
    record, exit_code = dev2_pilot.run_arm_command(
        **_run_arm_kwargs(
            tmp_path,
            run_lock=lock,
            spawn=lambda command: spawned.append(command) or _FakeProcess(),
        )
    )
    assert exit_code == 0
    assert record["run_arm"]["stop_reason"] is None
    assert len(spawned) == 1
    assert lock.acquire_calls == 1
    assert lock.releases == 1


def test_the_run_lock_is_released_when_the_child_fails(tmp_path: Path) -> None:
    lock = _FakeRunLock()
    record, exit_code = dev2_pilot.run_arm_command(
        **_run_arm_kwargs(
            tmp_path,
            run_lock=lock,
            spawn=lambda command: FakeProcess(polls=1, exit_code=1),
        )
    )
    assert exit_code == 1
    assert record["run_arm"]["stop_reason"] == dev2_pilot.STOP_CHILD_FAILED
    assert lock.releases == 1
