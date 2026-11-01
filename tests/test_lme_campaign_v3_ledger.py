"""B2b1 integration is test-injected, serial, fictional and transport/DB-free."""

from __future__ import annotations

from contextlib import asynccontextmanager
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from orchestrator.eval import campaign_guard as guard, campaign_retry, fact_harness
from orchestrator.eval.campaign_layout import layout
from tests.test_lme_campaign_contract import fixed_approval as fixed_approval, synthetic_plan
from tests.test_lme_campaign_v3_harness import FakeTransport, completed_row, runner

V3 = layout("v3")
PILOT = ("A0", "A1")


def campaign(tmp_path, *, arms=PILOT):
    return guard.Campaign(
        tmp_path / "campaign", synthetic_plan(), lambda _: 1, layout=V3, arms=arms
    )


def ready_state(c):
    # Only for continuation rejection tests; integration below produces real handoffs.
    c.directory.mkdir()
    state = {
        "status": "ready_next_arm",
        "plan": c.plan,
        "plan_sha256": c._plan_hash,
        "arm_index": 1,
        "question": None,
        "arm": None,
        "layout": "v3",
        "arms": list(PILOT),
    }
    c.state_path.write_text(json.dumps(state))
    return state


@pytest.mark.parametrize("arms", [("A0",), ("A1", "A0"), ("A0", "C0"), ("A0", "A1", "C0")])
def test_constructor_rejects_noncanonical_arm_sequences(tmp_path, arms):
    with pytest.raises(guard.CampaignStopped, match="arm sequence"):
        campaign(tmp_path, arms=arms)


@pytest.mark.parametrize("arms", [PILOT, ("A0", "A1", "A2", "C0")])
def test_arm_sequence_is_outside_plan_and_persisted_only_for_v3(tmp_path, arms):
    c = campaign(tmp_path, arms=arms)
    legacy = guard.Campaign(tmp_path / "legacy", synthetic_plan(), len)
    assert c.arms == arms
    assert c.plan == legacy.plan and c._plan_hash == legacy._plan_hash
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with c.activate():
            assert c.state["arms"] == list(arms) and c.state["layout"] == "v3"
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with legacy.activate():
            assert "arms" not in legacy.state and "layout" not in legacy.state


def test_pilot_start_arm_rejects_candidate_name(tmp_path):
    c = campaign(tmp_path)
    with pytest.raises(guard.CampaignStopped, match="Unexpected or repeated arm"):
        with c.activate():
            c.start_arm("C0", verified_pins=c.plan["pins"], restore={"fictional": True})


@pytest.mark.parametrize(
    "bad", ["default", "legacy", "v3_on_legacy", "unknown", "arms", "missing_arms"]
)
def test_continuation_layout_and_arm_mismatch_is_read_only(tmp_path, bad):
    c = campaign(tmp_path)
    state = ready_state(c)
    if bad in {"default", "legacy"}:
        c = guard.Campaign(
            c.directory,
            synthetic_plan(),
            len,
            layout=layout("legacy") if bad == "legacy" else None,
        )
    elif bad == "v3_on_legacy":
        del state["layout"]
        del state["arms"]
    elif bad == "unknown":
        state["layout"] = "future"
    elif bad == "arms":
        state["arms"] = ["A0", "A1", "A2", "C0"]
    elif bad == "missing_arms":
        del state["arms"]
    c.state_path.write_text(json.dumps(state))
    before = c.state_path.read_bytes()
    with pytest.raises(guard.CampaignStopped, match="clean completed-arm"):
        with c.activate():
            pytest.fail("Mismatched continuation admitted")
    assert c.state_path.read_bytes() == before


@pytest.mark.parametrize(
    "pin",
    [
        "fresh_campaign",
        "recovery_lineage",
        "retry_recovery_lineage",
        "final_verdict_recovery_lineage",
        "replacement_lineage",
        "capability",
    ],
)
def test_lineage_and_retry_incompatible_before_verification_or_calls(tmp_path, monkeypatch, pin):
    c = campaign(tmp_path)
    if pin == "capability":
        # Guards future capability implementations, not only today's lineage keys.
        monkeypatch.setattr(campaign_retry, "retry_capable", lambda _: True)
    else:
        c.plan["pins"][pin] = {}
        c._plan_hash = guard._hash(c.plan)
    recovery = Mock(side_effect=AssertionError("Lineage verification must not be reached"))
    monkeypatch.setattr(c, "_recovery_check", recovery)
    with pytest.raises(guard.CampaignStopped, match="V3 cannot use"):
        with c.activate():
            pytest.fail("Incompatible plan admitted")
    recovery.assert_not_called()
    assert not c.directory.exists()


@pytest.mark.parametrize("mode", ["fresh", "recovery", "retry", "final"])
def test_existing_valid_lineage_payloads_are_also_blocked(tmp_path, fixed_approval, mode):
    c = guard.Campaign(tmp_path / mode, synthetic_plan(mode), len, layout=V3, arms=PILOT)
    with pytest.raises(guard.CampaignStopped, match="V3 cannot use"):
        with c.activate():
            pytest.fail("Legacy lineage reached v3")


@pytest.mark.parametrize("bad", ["receipts", "qualification", "arm", "legacy_stride"])
def test_finish_arm_rejects_incomplete_v3_evidence(tmp_path, bad):
    c = campaign(tmp_path)
    with pytest.raises(guard.CampaignStopped, match="V3 arm lacks"):
        with c.activate():
            c.start_arm("A0", verified_pins=c.plan["pins"], restore={"fictional": True})
            c.state.update(question_index=50, receipts=[{}] * 500)
            if bad == "receipts":
                c.state["receipts"].pop()
            elif bad == "legacy_stride":
                c.state["receipts"] = [{}] * 200
            elif bad == "qualification":
                c.state["qualifications"] = [{"unexpected": True}]
            else:
                c.state["arm"] = "C0"
            c.finish_arm(qualification={"synthetic": True})


@pytest.mark.asyncio
async def test_attach_is_unreachable_without_retry_pin(tmp_path):
    from orchestrator.eval.campaign_restore import attached_pool

    c = campaign(tmp_path)
    with pytest.raises(guard.CampaignStopped, match="Attach is only approved"):
        with c.activate():
            async with attached_pool(c):
                pytest.fail("V3 attach admitted")


def summary_rows():
    rows = []
    for i in range(50):
        row = completed_row()
        row["question_id"] = f"synthetic-{i}"
        # Official majority correct, generic majority wrong: scoring must be official.
        row["generic_strict_majority"] = "incorrect"
        for sample in row["samples"]:
            sample["judgment"] = "incorrect"
            for call in ("answer", "judge", "official_judge"):
                sample[f"{call}_usage"] = {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                }
                sample[f"{call}_fingerprint"] = f"fp-{call}-{sample['sample']}"
        rows.append(row)
    return rows


def test_score_uses_official_majorities_and_exact_rates(capsys):
    from tests.longmemeval.evaluate import print_results

    rows = summary_rows()
    payload = fact_harness.build_score_payload(rows, layout=V3)
    assert payload["strict_accuracy"] == {"numerator": 50, "denominator": 50, "display": "1"}
    assert payload["accuracy"]["IE-user"] == payload["strict_accuracy"]
    assert payload["dual_judgments"]["generic"]["correct"] == 0
    assert payload["dual_judgments"]["official"]["correct"] == 50
    assert payload["v3_summaries"]["samples"]["correct"] == 150
    print_results(rows, payload["accuracy"], layout=V3)
    assert "Official majority: 50/50 (1)" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["error", "sample_error", "incomplete", "status", "missing_row"])
def test_manifest_marks_invalid_rows_without_scoring_them(bad, monkeypatch):
    rows = summary_rows()
    if bad == "error":
        rows[0]["error"] = "fictional"
    elif bad == "sample_error":
        rows[0]["samples"][1]["error"] = "fictional"
    elif bad == "incomplete":
        rows[0]["samples"].pop()
    elif bad == "status":
        rows[0]["samples"][1]["official_judge_status"] = "failed"
    else:
        rows.pop()
    assert fact_harness._v3_evaluate_summary(rows) == {
        "v3_summary_status": "invalid_incomplete_rows",
        "official_judgments": None,
    }
    with pytest.raises(guard.CampaignStopped):
        fact_harness.build_score_payload(rows, layout=V3)


@pytest.mark.asyncio
async def test_full_two_arm_v3_campaign_manifests_score_and_clean_continuation(
    tmp_path, monkeypatch, capsys
):
    fake = FakeTransport(monkeypatch, qualified=True)
    dataset = [
        {
            "question_id": f"synthetic-{i}",
            "question": "Fictional cat?",
            "answer": "Miso",
            "question_type": "single-session-user",
            "question_date": "2023/05/20 (Sat) 00:00",
        }
        for i in range(50)
    ]
    cls = fact_harness.LongMemEvalFactRunner
    monkeypatch.setattr(cls, "_preflight_database_guard", staticmethod(lambda: None))
    monkeypatch.setattr(cls, "load_dataset", lambda self: dataset)
    monkeypatch.setattr(
        fact_harness, "fingerprint_benchmark_users", AsyncMock(return_value={"sha256": "fictional"})
    )
    monkeypatch.setattr(
        fact_harness, "summarize_compute_spend", AsyncMock(return_value={"fictional": True})
    )
    manifests = []

    @asynccontextmanager
    async def phase(self, *args):
        manifest = {"phases": {}, "invocations": [{"phase": "evaluate", "outcome": "running"}]}
        manifests.append(manifest)
        yield None, SimpleNamespace(), manifest
        fact_harness.save_manifest(self.resolved_manifest_path(), manifest)

    @asynccontextmanager
    async def compute(*args, **kwargs):
        yield None

    monkeypatch.setattr(cls, "_phase", phase)
    monkeypatch.setattr(fact_harness, "account_compute", compute)
    c = campaign(tmp_path)
    for index, arm in enumerate(PILOT):
        destination = tmp_path / arm
        destination.mkdir()
        target = runner(destination)
        checkpoint = fact_harness.load_runner_checkpoint(target.checkpoint_path, dataset_path=None)
        checkpoint["phases"]["ingest"]["status"] = "completed"
        fact_harness.save_runner_checkpoint(target.checkpoint_path, checkpoint)
        c = campaign(tmp_path)
        with c.activate():
            c.start_arm(arm, verified_pins=c.plan["pins"], restore={"fictional": arm})
            rows = await target.evaluate()
            summary = target.score()
            assert summary["result_count"] == 50
            assert summary["strict_accuracy"] == {
                "numerator": 25,
                "denominator": 50,
                "display": "1/2",
            }
            c.finish_arm(qualification={"synthetic": True})
        saved = json.loads(c.state_path.read_bytes())
        assert saved["layout"] == "v3" and saved["arms"] == list(PILOT)
        assert len(saved["receipts"]) == (index + 1) * 500
        assert len(saved["qualifications"]) == index + 1
        assert saved["status"] == ("ready_next_arm" if index == 0 else "completed")
        evaluated = manifests[-1]["phases"]["evaluate"]
        assert evaluated["v3_summary_status"] == "completed"
        assert evaluated["v3_summaries"]["samples"]["judged"] == 150
        assert evaluated["official_judgments"]["correct"] == 25
        assert evaluated["official_judge_failed"] == 0
        # Literal 50 * sums of the three distinct sample prompt usages (104/105/106 average).
        assert evaluated["answer_usage"] == {
            "prompt_tokens": 15600,
            "completion_tokens": 150,
            "total_tokens": 15750,
        }
        assert evaluated["judge_usage"] == {
            "prompt_tokens": 15750,
            "completion_tokens": 150,
            "total_tokens": 15900,
        }
        assert evaluated["official_judge_usage"] == {
            "prompt_tokens": 15900,
            "completion_tokens": 150,
            "total_tokens": 16050,
        }
        assert evaluated["provider_fingerprints"] == {
            "answer": {"fp-1": 50, "fp-4": 50, "fp-7": 50},
            "judge": {"fp-2": 50, "fp-5": 50, "fp-8": 50},
        }
        assert evaluated["official_judge_fingerprints"] == {"fp-3": 50, "fp-6": 50, "fp-9": 50}
        assert len(rows) == 50
        if index == 0:
            before = c.state_path.read_bytes()
            with pytest.raises(guard.CampaignStopped):
                with guard.Campaign(c.directory, synthetic_plan(), len).activate():
                    pytest.fail("V3 continued without injection")
            assert c.state_path.read_bytes() == before
    assert len(fake.calls) == 900 and fake.retrieval.await_count == 100
    assert len(list(c.directory.glob("receipt-*.json"))) == 1000
    with pytest.raises(guard.CampaignStopped):
        with campaign(tmp_path).activate():
            pytest.fail("A completed pilot must not activate a third time")
    assert "Official majority: 25/50 (1/2)" in capsys.readouterr().out


def test_legacy_score_payload_and_print_are_unchanged(tmp_path, monkeypatch, capsys):
    from tests.longmemeval.evaluate import print_results

    rows = [
        {
            "question_id": "fictional",
            "category": "IE-user",
            "judgment": "correct",
            "hypothesis": "Miso",
        }
    ]
    monkeypatch.setattr(fact_harness, "utc_now_iso", lambda: "fixed")
    default = fact_harness.build_score_payload(rows)
    explicit = fact_harness.build_score_payload(copy.deepcopy(rows), layout=layout("legacy"))
    assert json.dumps(default, sort_keys=True) == json.dumps(explicit, sort_keys=True)
    print_results(rows, default["accuracy"])
    first = capsys.readouterr().out
    print_results(rows, default["accuracy"], layout=layout("legacy"))
    assert capsys.readouterr().out == first
