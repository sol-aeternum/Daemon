"""Synthetic tests for the dev2 pilot selection, arms and volume numbers.

Every row here is invented: no dataset, provider, database or network access.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.benchmark_longmemeval import dev2_pilot, holdout
from tests.benchmark_longmemeval.dev2_pilot import (
    ALGORITHM,
    ARM_SIZES,
    PILOT_SIZE,
    assign_arms,
    pilot_volume,
    select_pilot,
    spend_projection,
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
