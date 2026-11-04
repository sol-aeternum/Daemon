"""Synthetic tests for the dev2 pilot selection, arms, volumes and runner.

Every row here is invented: no dataset, provider, database or network access.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_longmemeval import dev2_pilot, holdout
from tests.benchmark_longmemeval.dev2_pilot import (
    ALGORITHM,
    ARM_SIZES,
    PILOT_SIZE,
    STOP_EXTRACTION,
    STOP_RATE_LIMIT,
    STOP_SPEND,
    StopRule,
    assign_arms,
    checkpoint_invariants,
    count_rate_limits,
    failed_rate,
    pilot_volume,
    run_arm,
    select_pilot,
    session_invariants,
    spend_projection,
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


def test_bench_db_script_parses_and_guards_the_db_name() -> None:
    text = SCRIPT.read_text()
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    assert 'DB_NAME="${BENCH_DB_NAME:-daemon_lme_bench}"' in text
    assert '[[ "$DB_NAME" == daemon_lme_bench* ]] || die' in text
    assert dev2_pilot.MANIFEST_NAME == "pilot_manifest.json"


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
        return self.exit_code


def _spend_reader(values: Sequence[float]) -> Any:
    state = {"index": 0}

    def read() -> float:
        index = state["index"]
        state["index"] = min(index + 1, len(values) - 1)
        return values[index]

    return read


def _checkpoint(sessions: Sequence[str], failures: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "results": {
            "q1": {"sessions": list(sessions), "failures": list(failures)},
        }
    }


def _extract(
    checkpoint: Mapping[str, Any], question_id: str
) -> tuple[Sequence[str], Sequence[str]]:
    record = checkpoint["results"][question_id]
    return record.get("sessions", ()), record.get("failures", ())


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
        self.checkpoint: Mapping[str, Any] = checkpoint or {}
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
    line = '{"event": "attempt_outcome", "failure_category": "rate_limited"}'
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
    )
    assert result["interrupted_for_resume"] is True
    assert result["stop_reason"] is None
    assert len(harness.sent) == 1
    assert [item["kind"] for item in result["interrupts"]] == ["resume"]


def test_normal_completion_records_no_stop_reason() -> None:
    harness = Harness(
        checkpoint=_checkpoint(["s0", "s1"]),
        process=FakeProcess(polls=2),
    )
    result = harness.run()
    assert result["stop_reason"] is None
    assert result["interrupted_for_resume"] is False
    assert result["interrupts"] == []
    assert harness.process.interrupts == 0
    assert result["completed_sessions"] == 2
    assert result["sessions_per_hour"] > 0


def test_second_sigint_only_after_grace() -> None:
    harness = Harness(process=FakeProcess(polls=200, ignore_interrupts=4))
    result = harness.run(
        poll_seconds=10.0,
        grace_seconds=20.0,
        stop_rule=StopRule(cap_usd=6.0, margin_usd=1.0, max_rate_limited=10),
        read_checkpoint=lambda: _checkpoint(["s0"]),
        interrupt_after_completed=1,
    )
    kinds = [item["kind"] for item in result["interrupts"]]
    assert kinds[0] == "resume"
    assert kinds.count("grace") == 1
    assert len(harness.sent) == 2


def test_session_invariants_counts_and_duplicates() -> None:
    checkpoint = _checkpoint(["a", "a", "b"], ["c"])
    invariants = session_invariants(checkpoint, ["q1"], extract=_extract)
    assert invariants["total_sessions"] == 3
    assert invariants["failed_sessions"] == 1
    assert invariants["completed_sessions"] == 2
    assert invariants["duplicate_sessions"] == 1
    assert invariants["questions"]["q1"]["duplicate_sessions"] == 1
    assert failed_rate(invariants) == 1 / 3


def test_checkpoint_invariants_reads_every_question() -> None:
    checkpoint = {
        "results": {
            "q1": {"sessions": ["a"], "failures": []},
            "q2": {"sessions": ["b", "c"], "failures": ["d"]},
        }
    }
    invariants = checkpoint_invariants(checkpoint, extract=_extract)
    assert invariants["total_sessions"] == 3
    assert invariants["failed_sessions"] == 1


def test_verify_resume_passes_clean_resume() -> None:
    before = session_invariants(_checkpoint(["a", "b"]), ["q1"], extract=_extract)
    after = session_invariants(_checkpoint(["a", "b", "c"]), ["q1"], extract=_extract)
    assert verify_resume(before, after, 3) == []


def test_verify_resume_flags_duplicate_lost_mismatch_and_failures() -> None:
    before = session_invariants(_checkpoint(["a", "b"]), ["q1"], extract=_extract)
    duplicated = session_invariants(_checkpoint(["a", "b", "a"], ["bad"]), ["q1"], extract=_extract)
    violations = verify_resume(before, duplicated, 4)
    assert any("duplicated" in item for item in violations)
    assert any("does not match" in item for item in violations)
    assert any("extraction_failed" in item for item in violations)

    lost = session_invariants(_checkpoint(["a"]), ["q1"], extract=_extract)
    lost_violations = verify_resume(before, lost, 1)
    assert any("lost" in item for item in lost_violations)

    per_question = verify_resume(before, lost, {"q1": 1})
    assert not any("does not match" in item for item in per_question)


def test_count_rate_limits_tolerates_junk_lines() -> None:
    lines = [
        "",
        "not json at all",
        '{"event": "attempt_outcome", "failure_category": "rate_limited"}',
        '{"event": "attempt_outcome", "failure_category": "rate_limited"}',
        '{"event": "attempt_outcome", "failure_category": "timeout"}',
        '{"event": "other", "failure_category": "rate_limited"}',
        '{"event": "attempt_outcome"}',
        '{"event": "attempt_outcome", "failure_category": 5}',
        "[1, 2, 3]",
    ]
    counts = count_rate_limits(lines)
    assert counts == {"rate_limited": 2, "timeout": 1}
    assert all(isinstance(value, int) for value in counts.values())
