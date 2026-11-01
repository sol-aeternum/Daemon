"""Unselected layout helpers, with independent legacy serial-execution oracles."""

import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from orchestrator.eval import campaign_guard as guard, campaign_launch as launch
from orchestrator.eval import campaign_fresh as fresh, campaign_recovery as recovery
from orchestrator.eval import campaign_retry as retry, campaign_final_verdict as final
from orchestrator.eval.campaign_layout import LEGACY, V3, CampaignLayout, layout
from tests.test_lme_campaign_contract import (
    fixed_approval as fixed_approval,
    receipt_directory,
    synthetic_plan,
)


def test_literal_slot_sequences_and_distinct_stride_and_arm_count() -> None:
    assert LEGACY == (
        ("embedding", None),
        ("answer", None),
        ("judge", None),
        ("official_judge", None),
    )
    assert V3 == (
        ("embedding", None),
        ("answer", 1),
        ("judge", 1),
        ("official_judge", 1),
        ("answer", 2),
        ("judge", 2),
        ("official_judge", 2),
        ("answer", 3),
        ("judge", 3),
        ("official_judge", 3),
    )
    assert layout("legacy").slots_per_question == 4
    assert layout("legacy").receipts_per_arm(50) == 200
    assert layout("v3").slots_per_question == 10
    assert layout("v3").receipts_per_arm(50) == 500


def test_legacy_full_grid_against_literal_oracle_and_manifest(tmp_path: Path) -> None:
    legacy = layout("legacy")
    state = receipt_directory(tmp_path, 800)
    manifest = recovery._receipt_manifest(tmp_path, state)
    assert len(manifest) == 800
    for arm in range(4):
        for question in range(50):
            for slot in range(4):
                # Local literal oracle, NOT the production helper/formula as expected.
                expected = arm * 200 + question * 4 + slot
                assert (
                    legacy.receipt_index(arm, question, slot, question_count=50, arm_count=4)
                    == expected
                )
                assert legacy.inverse(expected, question_count=50, arm_count=4) == (
                    arm,
                    question,
                    slot,
                )
                entry = state["receipts"][expected]
                assert legacy.identity(
                    ("A0", "A1", "A2", "C0")[arm], f"synthetic-{question}", slot, "a" * 64
                ) == {k: v for k, v in entry.items() if k != "artifact"}


@pytest.mark.parametrize("arm_count", [2, 4])
def test_v3_full_grid(arm_count: int) -> None:
    candidate = layout("v3")
    seen = set()
    for arm in range(arm_count):
        for question in range(50):
            for slot in range(10):
                number = candidate.receipt_index(
                    arm, question, slot, question_count=50, arm_count=arm_count
                )
                assert number == arm * 500 + question * 10 + slot
                assert candidate.inverse(number, question_count=50, arm_count=arm_count) == (
                    arm,
                    question,
                    slot,
                )
                seen.add(number)
    assert seen == set(range(arm_count * 500))


def test_exact_identity_forms_and_same_request_in_two_samples() -> None:
    base = {
        "arm": "A0",
        "question_id": "synthetic-0",
        "stage": "embedding",
        "request_sha256": "a" * 64,
    }
    assert layout("legacy").identity("A0", "synthetic-0", 0, "a" * 64) == base
    assert layout("v3").identity("A0", "synthetic-0", 0, "a" * 64) == base
    for slot, stage in enumerate(("answer", "judge", "official_judge"), start=1):
        assert layout("legacy").identity("A0", "synthetic-0", slot, "a" * 64) == {
            **base,
            "stage": stage,
        }
    first = layout("v3").identity("A0", "synthetic-0", 1, "a" * 64)
    second = layout("v3").identity("A0", "synthetic-0", 4, "a" * 64)
    assert first == {**base, "stage": "answer", "sample": 1}
    assert second == {**base, "stage": "answer", "sample": 2}
    assert guard._hash(first) != guard._hash(second)


@pytest.mark.parametrize(
    "name,slots",
    [
        ("unknown", LEGACY),
        ("legacy", V3),
        ("v3", LEGACY),
        ("legacy", LEGACY[:-1]),
        ("legacy", LEGACY + (LEGACY[0],)),
        ("legacy", (LEGACY[1], LEGACY[0], *LEGACY[2:])),
        ("legacy", (("embedding", 1), *LEGACY[1:])),
        ("v3", V3[:-1]),
        ("v3", V3 + (V3[1],)),
        ("v3", (V3[0], V3[2], V3[1], *V3[3:])),
        ("v3", (V3[0], ("answer", None), *V3[2:])),
        ("v3", (V3[0], ("answer", 0), *V3[2:])),
        ("v3", (V3[0], ("answer", 4), *V3[2:])),
        ("v3", (("embedding", 1), *V3[1:])),
        ("v3", (V3[0], ("answer", True), *V3[2:])),
    ],
)
def test_reject_noncanonical_layout(name: str, slots: tuple) -> None:
    with pytest.raises(ValueError, match="non-canonical"):
        CampaignLayout(name, slots)


def test_reject_unknown_layout_name() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        layout("schema-1")


def test_reject_non_tuple_slots_with_canonical_contents() -> None:
    class CanonicalList(list[tuple[str, int | None]]):
        def __eq__(self, other: object) -> bool:
            return tuple(self) == other

    # Equality cannot cause this rejection: isolate the exact tuple-type guard.
    slots: Any = CanonicalList(LEGACY)
    assert slots == LEGACY
    with pytest.raises(ValueError, match="^Unknown or non-canonical campaign layout$"):
        CampaignLayout("legacy", slots)


@pytest.mark.parametrize("name", ["legacy", "v3"])
@pytest.mark.parametrize(
    "arm,question,slot,questions,arms,error",
    [
        (-1, 0, 0, 50, 4, "Invalid arm index"),
        (4, 0, 0, 50, 4, "Invalid arm index"),
        (0, -1, 0, 50, 4, "Invalid question index"),
        (0, 50, 0, 50, 4, "Invalid question index"),
        (0, 0, -1, 50, 4, "Invalid slot index"),
        (0, 0, 10, 50, 4, "Invalid slot index"),
        (0, 0, 0, 0, 4, "Invalid question count"),
        (0, 0, 0, 50, 0, "Invalid arm count"),
        (True, 0, 0, 50, 4, "Invalid arm index"),
    ],
)
def test_index_bounds(
    name: str, arm: int, question: int, slot: int, questions: int, arms: int, error: str
) -> None:
    with pytest.raises(ValueError, match=f"^{error}$"):
        layout(name).receipt_index(arm, question, slot, question_count=questions, arm_count=arms)


@pytest.mark.parametrize("number", [-1, 800, True])
def test_inverse_bounds(number: int) -> None:
    with pytest.raises(ValueError, match="^Invalid receipt index$"):
        layout("legacy").inverse(number, question_count=50, arm_count=4)


@pytest.mark.parametrize("arms,number", [(2, 1000), (4, 2000)])
def test_v3_inverse_upper_bounds(arms: int, number: int) -> None:
    assert layout("v3").inverse(number - 1, question_count=50, arm_count=arms) == (arms - 1, 49, 9)
    with pytest.raises(ValueError, match="^Invalid receipt index$"):
        layout("v3").inverse(number, question_count=50, arm_count=arms)


def test_identity_bounds() -> None:
    with pytest.raises(ValueError, match="^Invalid slot index$"):
        layout("legacy").identity("A0", "q", 4, "a" * 64)
    with pytest.raises(ValueError, match="^Invalid slot index$"):
        layout("v3").identity("A0", "q", 10, "a" * 64)


@pytest.mark.parametrize("name", ["legacy", "v3"])
@pytest.mark.parametrize(
    "arm,qid,digest,error",
    [
        ("", "q", "a" * 64, "Invalid arm identity"),
        (True, "q", "a" * 64, "Invalid arm identity"),
        (1, "q", "a" * 64, "Invalid arm identity"),
        ("A0", "", "a" * 64, "Invalid question identity"),
        ("A0", 1, "a" * 64, "Invalid question identity"),
        ("A0", "q", None, "Invalid request SHA-256"),
        ("A0", "q", True, "Invalid request SHA-256"),
        ("A0", "q", "", "Invalid request SHA-256"),
        ("A0", "q", "x" * 64, "Invalid request SHA-256"),
        ("A0", "q", "a", "Invalid request SHA-256"),
    ],
)
def test_identity_string_contract(name: str, arm: Any, qid: Any, digest: Any, error: str) -> None:
    with pytest.raises(ValueError, match=f"^{error}$"):
        layout(name).identity(arm, qid, 0, digest)


def test_import_hygiene_and_explicit_later_wiring() -> None:
    directory = Path(guard.__file__).parent
    imports = []
    for path in directory.glob("campaign_*.py"):
        if path.name == "campaign_layout.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                if "campaign_layout" in (node.module or "") or any(
                    alias.name == "campaign_layout" for alias in node.names
                ):
                    imports.append(path.name)
            elif isinstance(node, ast.Import):
                if any("campaign_layout" in alias.name for alias in node.names):
                    imports.append(path.name)
    # PR B must update three places when explicit version dispatch is wired:
    # this import assertion, the fresh-interpreter sys.modules checks below,
    # and test_bridge_registration_is_deliberately_deferred.
    # B2a wires only the guard's explicit test injection, not bridge/finalisation.
    # B2b1's pure ledger auditor uses canonical slot indices; no production selector.
    assert sorted(imports) == ["campaign_guard.py", "campaign_v3_plan.py"]
    module = ast.parse((directory / "campaign_layout.py").read_text())
    modules = [
        node.module or "" for node in ast.walk(module) if isinstance(node, ast.ImportFrom)
    ] + [
        alias.name
        for node in ast.walk(module)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert all(name.split(".")[0] in sys.stdlib_module_names for name in modules)
    # A genuinely fresh interpreter, not collection's already imported modules.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from orchestrator.eval import campaign_guard as guard; "
            "assert guard.current_campaign() is None; "
            "assert 'orchestrator.eval.campaign_layout' in sys.modules; "
            "assert not any(name.startswith('orchestrator.eval.campaign_') "
            "and name not in {'orchestrator.eval.campaign_guard', "
            "'orchestrator.eval.campaign_layout'} for name in sys.modules)",
        ],
        cwd=directory.parent.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_inert_payload_and_fresh_pin_boundaries(tmp_path: Path, fixed_approval: None) -> None:
    payload = synthetic_plan().payload()
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(payload))
    assert launch.load_plan(path).payload() == payload
    path.write_text(json.dumps({**payload, "campaign_layout": "v3"}))
    with pytest.raises(guard.CampaignStopped, match="changed/unknown fields"):
        launch.load_plan(path)
    pins = copy.deepcopy(dict(synthetic_plan("fresh").pins))
    fresh.validate_mode(pins)
    pins["campaign_layout"] = "v3"
    with pytest.raises(guard.CampaignStopped, match="exact pins"):
        fresh.validate_mode(pins)
    # Legacy/recovery/retry/final-verdict pins are NOT exact-key validated
    # (campaign_guard.py:120). PR B must add explicit version dispatch, never
    # assume an unknown pin or payload["schema"] safely selects a new layout.


def test_bridge_registration_is_deliberately_deferred() -> None:
    path = "orchestrator/eval/campaign_layout.py"
    assert path not in recovery.BRIDGE_FILES
    assert path not in retry.BRIDGE_FILES
    assert path not in final.BRIDGE_FILES
    assert path not in recovery.RUNTIME_PATHS
    assert "orchestrator" in recovery.RUNTIME_PATHS
    # RUNTIME_PATHS covers the added file via its parent: bridges spanning PR A
    # fail closed. PR B must register it; this PR must not edit those lists.
