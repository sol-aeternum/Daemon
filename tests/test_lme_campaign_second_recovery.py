"""Synthetic second-replacement admission/bootstrap/diagnostics; no live services."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.eval import campaign_guard as guard, campaign_launch as launch
from orchestrator.eval import campaign_recovery as recovery, campaign_validation as validation
from tests.longmemeval import evaluate
from tests.test_lme_campaign_guard import plan, send
from tests.test_lme_campaign_launch import qualified_artifacts
from tests.test_lme_campaign_recovery import history

REAL_SOURCE_BRIDGE = recovery.source_bridge


def write(path, value):
    path.write_text(json.dumps(value))


def pin(path):
    return {"path": str(path), "sha256": launch.digest_file(path)}


def receipts(directory, payload, count, partial):
    entries = []
    for i in range(count + int(partial)):
        pending = {
            "arm": guard.ARMS[i // 200],
            "question_id": payload["question_ids"][(i % 200) // 4],
            "stage": guard.STAGES[i % 4],
            "request_sha256": guard._hash({"synthetic": i}),
        }
        name = f"receipt-{i:04d}.json"
        write(directory / name, {"pending": pending, "response": {"synthetic": True}})
        if i < count:
            entries.append({**pending, "artifact": name})
    return entries


@pytest.fixture
def recovered(tmp_path, monkeypatch):
    original, lineage = history(tmp_path)
    rows, dataset, manifest, checkpoint, score = qualified_artifacts()
    ids = [row["question_id"] for row in dataset]
    original["question_ids"] = ids
    original["input_limits"] = {qid: plan().input_limits["synthetic-0"] for qid in ids}
    write(Path(lineage["previous_plan"]["path"]), original)
    lineage["previous_plan"] = pin(Path(lineage["previous_plan"]["path"]))
    old_path = Path(lineage["failed_ledger"]["path"])
    failed = json.loads(old_path.read_text())
    original_directory = tmp_path / "campaign"
    original_directory.mkdir()
    failed.update(plan=original, receipts=receipts(original_directory, original, 6, True))
    failed["pending"] = json.loads((original_directory / "receipt-0006.json").read_bytes())[
        "pending"
    ]
    first_ledger = original_directory / "campaign.json"
    write(first_ledger, failed)
    lineage["failed_ledger"] = pin(first_ledger)
    pins = copy.deepcopy(original["pins"])
    pins.update(
        replacement_lineage=lineage,
        arm_shas={
            arm: recovery.OLD_CANDIDATE if arm == "C0" else recovery.OLD_CONTROL
            for arm in guard.ARMS
        },
    )
    previous = guard.CampaignPlan(tuple(ids), original["input_limits"], pins)
    previous_payload = json.loads(json.dumps(previous.payload()))
    previous_path = tmp_path / "replacement-plan.json"
    write(previous_path, previous_payload)
    directory = tmp_path / "campaign-replacement"
    directory.mkdir()
    a0 = directory / "A0"
    a0.mkdir()
    manifest["git"]["sha"] = recovery.OLD_CONTROL
    (a0 / recovery.A0_FILES[0]).write_text("".join(json.dumps(row) + "\n" for row in rows))
    for name, data in zip(recovery.A0_FILES[1:], (checkpoint, score, manifest), strict=True):
        write(a0 / name, data)
    qualified = {
        "artifacts": {name: launch.digest_file(a0 / name) for name in recovery.A0_FILES},
        **validation.qualification(rows, dataset, manifest, checkpoint, score),
    }
    restored = [
        {
            "arm": arm,
            "fingerprint": {"sha256": validation.STATE_SHA256},
            "owned_pids": list(range(10)),
            "script_sha256": pins["restore_script"]["sha256"],
        }
        for arm in ("A0", "A1")
    ]
    stopped = {
        "plan": previous_payload,
        "plan_sha256": guard._hash(previous_payload),
        "status": "stopped_reconciliation_required",
        "failure_admission": "closed",
        "arm_index": 1,
        "arm": "A1",
        "question_index": 11,
        "question": ids[11],
        "stage_index": 1,
        "restore_attempts": 2,
        "restore_pending": None,
        "restore_receipts": restored,
        "restores": restored,
        "qualifications": [qualified],
        "receipts": receipts(directory, previous_payload, 245, False),
        "pending": {
            "arm": "A1",
            "question_id": ids[11],
            "stage": "answer",
            "request_sha256": guard._hash({"synthetic": 245}),
        },
    }
    ledger_path = directory / "campaign.json"
    write(ledger_path, stopped)
    new_lineage = {
        "previous_replacement_plan": pin(previous_path),
        "stopped_a1_ledger": pin(ledger_path),
    }
    monkeypatch.setattr(launch, "load_dev", lambda _: copy.deepcopy(dataset))
    bridge = {
        "old_control": recovery.OLD_CONTROL,
        "new_control": "new-control",
        "files": {},
        "diff_sha256": "synthetic-source-only",
    }
    monkeypatch.setattr(recovery, "source_bridge", lambda *_: copy.deepcopy(bridge))
    monkeypatch.setattr(launch, "verify_common", lambda *_: {"synthetic-common": "digest"})
    _, metadata = recovery.inspect_lineage(new_lineage)
    new_pins = copy.deepcopy(pins)
    new_pins.update(
        recovery_lineage=new_lineage,
        recovery={**metadata, "source_bridge": bridge},
        common_files=launch.verify_common("new-control", "new-candidate"),
        arm_shas={arm: "new-candidate" if arm == "C0" else "new-control" for arm in guard.ARMS},
    )
    new_plan = guard.CampaignPlan(tuple(ids), original["input_limits"], new_pins)
    return SimpleNamespace(
        plan=new_plan,
        payload=json.loads(json.dumps(new_plan.payload())),
        previous=previous,
        lineage=new_lineage,
        stopped=stopped,
        ledger=ledger_path,
        source=directory,
        destination=directory.with_name(directory.name + "-recovery"),
        dataset=dataset,
        rows=rows,
        metadata=metadata,
        bridge=bridge,
    )


def test_second_budget_is_three_arms_and_no_spendable_capacity(recovered):
    p = recovered.payload
    assert p["arms"] == list(guard.ARMS)
    assert p["execution_arms"] == ["A1", "A2", "C0"]
    assert p["prior_reserved_usd"] == "10.4019050"
    assert p["new_reserved_usd"] == "4.35"
    assert p["cumulative_reserved_usd"] == "14.7519050"
    assert p["separate_capacity_reserved_usd"] == "1"
    assert p["prior_restore_attempts"] == 3
    assert p["maximum_cumulative_restore_attempts"] == 6
    from decimal import Decimal

    assert (
        Decimal(p["allocated_usd"]) * 4
        == Decimal(recovered.previous.payload()["allocated_usd"]) * 3
    )
    limits = copy.deepcopy(recovered.plan.input_limits)
    for value in limits.values():
        value["answer"] = 12000
    with pytest.raises(guard.CampaignStopped, match="Allocations exceed"):
        guard.CampaignPlan(recovered.plan.question_ids, limits, recovered.plan.pins).payload()


@pytest.mark.asyncio
async def test_explicit_preparation_is_offline_and_fixed_output(recovered, monkeypatch):
    forbidden = AsyncMock(
        side_effect=AssertionError("No network/DB during offline recovery prepare")
    )
    monkeypatch.setattr(launch.asyncpg, "connect", forbidden)
    monkeypatch.setattr(launch, "rates", forbidden)
    previous_path = Path(recovered.lineage["previous_replacement_plan"]["path"])
    output = previous_path.with_name(previous_path.stem + "-recovery.json")
    args = argparse.Namespace(
        output=output,
        control_sha="new-control",
        candidate_sha="new-candidate",
        **{name: Path(value["path"]) for name, value in recovered.lineage.items()},
        **{name + "_sha256": value["sha256"] for name, value in recovered.lineage.items()},
    )
    await recovery.prepare_second_replacement(args)
    assert launch.load_plan(output).payload() == recovered.plan.payload()
    with pytest.raises(guard.CampaignStopped, match="already exists"):
        await recovery.prepare_second_replacement(args)
    args.output = output.with_name("another.json")
    with pytest.raises(guard.CampaignStopped, match="fixed successor output"):
        await recovery.prepare_second_replacement(args)
    forbidden.assert_not_called()


@pytest.mark.parametrize(
    "ancestor",
    ["previous_replacement_plan", "stopped_a1_ledger", "original_plan", "original_ledger"],
)
def test_both_recursive_ancestors_hash_bound(recovered, ancestor):
    if ancestor.startswith("original_"):
        key = "previous_plan" if ancestor == "original_plan" else "failed_ledger"
        artifact = recovered.plan.pins["replacement_lineage"][key]
    else:
        artifact = recovered.lineage[ancestor]
    Path(artifact["path"]).write_text("{}")
    with pytest.raises(guard.CampaignStopped, match="changed"):
        recovery.verify_recovery(recovered.payload)


@pytest.mark.parametrize("which", ["original", "replacement", "partial", "a0"])
def test_all_evidence_including_partial_is_hash_bound(recovered, which):
    paths = {
        "original": recovered.source.with_name("campaign") / "receipt-0000.json",
        "replacement": recovered.source / "receipt-0244.json",
        "partial": recovered.source.with_name("campaign") / "receipt-0006.json",
        "a0": recovered.source / "A0" / recovery.A0_FILES[0],
    }
    path = paths[which]
    data = json.loads(path.read_text().splitlines()[0])
    data["injected"] = "synthetic-change"
    write(path, data)
    with pytest.raises(guard.CampaignStopped, match="changed"):
        recovery.verify_recovery(recovered.payload)


def test_absent_partial_not_assumed_but_later_appearance_changes_manifest(recovered):
    recovery.verify_recovery(recovered.payload)
    pending = recovered.source / "receipt-0245.json"
    write(pending, {"pending": recovered.stopped["pending"], "response": {}})
    with pytest.raises(guard.CampaignStopped, match="manifest changed"):
        recovery.verify_recovery(recovered.payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("arm_index", 0),
        ("question_index", 12),
        ("stage_index", 0),
        ("question", "other"),
        ("restore_attempts", 3),
        ("restore_pending", {}),
        ("failure_admission", "unknown"),
        ("qualifications", []),
        ("status", "ready_next_arm"),
        ("arm", "A0"),
    ],
)
def test_wrong_stopped_state_even_with_current_hash_rejected(recovered, field, value):
    recovered.stopped[field] = value
    write(recovered.ledger, recovered.stopped)
    recovered.lineage["stopped_a1_ledger"] = pin(recovered.ledger)
    with pytest.raises(guard.CampaignStopped, match="Stopped A1 state"):
        recovery.inspect_lineage(recovered.lineage)


def test_qualification_is_recomputed_not_a_flag(recovered):
    recovered.stopped["qualifications"][0]["official"]["correct"] = 49
    write(recovered.ledger, recovered.stopped)
    recovered.lineage["stopped_a1_ledger"] = pin(recovered.ledger)
    with pytest.raises(guard.CampaignStopped, match="qualification differs"):
        recovery.inspect_lineage(recovered.lineage)


def test_no_recursive_third_recovery(recovered):
    previous_path = Path(recovered.lineage["previous_replacement_plan"]["path"])
    write(previous_path, recovered.payload)
    lineage = copy.deepcopy(recovered.lineage)
    lineage["previous_replacement_plan"] = pin(previous_path)
    with pytest.raises(guard.CampaignStopped, match="Third recovery"):
        recovery.inspect_lineage(lineage)


@pytest.mark.parametrize("case", ["symlink", "traversal", "wrong_parent"])
def test_recovery_evidence_paths_are_direct_and_exact(recovered, tmp_path, case):
    lineage = copy.deepcopy(recovered.lineage)
    previous = Path(lineage["previous_replacement_plan"]["path"])
    if case == "symlink":
        alternate = tmp_path / "linked.json"
        alternate.symlink_to(previous)
    elif case == "traversal":
        alternate = tmp_path / "campaign" / ".." / previous.name
    else:
        parent = tmp_path / "other"
        parent.mkdir()
        alternate = parent / previous.name
        alternate.write_bytes(previous.read_bytes())
    lineage["previous_replacement_plan"]["path"] = str(alternate)
    with pytest.raises(guard.CampaignStopped, match="path|symlink|parent"):
        recovery.inspect_lineage(lineage)


def test_fixed_destination_and_a0_execution_rejected_before_mutation(recovered, tmp_path):
    with pytest.raises(guard.CampaignStopped, match="fixed successor"):
        launch.verify_replacement_destination(recovered.plan, tmp_path / "another")
    with pytest.raises(guard.CampaignStopped, match="A0 cannot execute"):
        launch.checkout(recovered.plan, "A0")
    assert not recovered.destination.exists()


@pytest.mark.parametrize(
    "failpoint", ["mkdir", "copy", "copy_fsync", "directory_fsync", "state_save"]
)
def test_partial_bootstrap_is_never_resumed(recovered, monkeypatch, failpoint):
    original_open = Path.open
    original_mkdir = Path.mkdir
    original_sync = recovery._sync_directory
    original_write = guard._write
    original_fsync = recovery.os.fsync

    def open_fail(path, *args, **kwargs):
        if path == recovered.destination / "A0" / recovery.A0_FILES[1] and "xb" in args:
            raise OSError("synthetic copy failure")
        return original_open(path, *args, **kwargs)

    def mkdir_fail(path, *args, **kwargs):
        if path == recovered.destination / "A0":
            raise OSError("synthetic mkdir failure")
        return original_mkdir(path, *args, **kwargs)

    def sync_fail(path):
        if path == recovered.destination / "A0":
            raise OSError("synthetic directory sync failure")
        original_sync(path)

    def write_fail(path, data):
        if path == recovered.destination / "campaign.json":
            raise OSError("synthetic initial-state failure")
        original_write(path, data)

    def fsync_fail(fd):
        import stat

        if stat.S_ISREG(recovery.os.fstat(fd).st_mode):
            raise OSError("synthetic copy fsync failure")
        original_fsync(fd)

    with monkeypatch.context() as m:
        if failpoint == "mkdir":
            m.setattr(Path, "mkdir", mkdir_fail)
        elif failpoint == "copy":
            m.setattr(Path, "open", open_fail)
        elif failpoint == "copy_fsync":
            m.setattr(recovery.os, "fsync", fsync_fail)
        elif failpoint == "directory_fsync":
            m.setattr(recovery, "_sync_directory", sync_fail)
        else:
            m.setattr(guard, "_write", write_fail)
        with pytest.raises(OSError):
            with guard.Campaign(recovered.destination, recovered.plan, len).activate():
                pytest.fail("Partial bootstrap exposed a live campaign")
    assert recovered.destination.exists()
    before = {
        str(path): path.read_bytes() for path in recovered.destination.rglob("*") if path.is_file()
    }
    with pytest.raises((guard.CampaignStopped, FileNotFoundError)):
        with guard.Campaign(recovered.destination, recovered.plan, len).activate():
            pytest.fail("Partial bootstrap resumed")
    assert before == {
        str(path): path.read_bytes() for path in recovered.destination.rglob("*") if path.is_file()
    }


def finish_full_arm(instance, recovered, arm):
    instance.pending_restore(arm, {"synthetic": arm})
    receipt = {"synthetic": arm}
    instance.restored(arm, receipt)
    instance.start_arm(arm, verified_pins=recovered.plan.pins, restore=receipt)
    for qid in recovered.plan.question_ids:
        instance.begin_question(qid)
        for stage in guard.STAGES:
            send(stage)
        instance.finish_question(
            {
                "question_id": qid,
                "official_judge_status": "completed",
                "official_judgment": "correct",
            }
        )
    instance.finish_arm(qualification={"synthetic": arm})


def test_three_full_arms_serial_handoffs_with_imported_a0_and_six_restore_cap(recovered):
    for index, arm in enumerate(("A1", "A2", "C0"), 1):
        with guard.Campaign(recovered.destination, recovered.plan, len).activate() as instance:
            assert instance.state["arm_index"] == index
            assert instance.state["restore_attempts"] == index - 1
            assert len(instance.state["receipts"]) == (index - 1) * 200
            assert instance.state["qualifications"][0] == recovered.stopped["qualifications"][0]
            if arm == "A1":
                assert instance.state["restores"] == instance.state["restore_receipts"] == []
                assert instance.state["imported_a0"] == recovered.metadata["inherited_a0"]
            finish_full_arm(instance, recovered, arm)
    saved = json.loads((recovered.destination / "campaign.json").read_text())
    assert saved["status"] == "completed"
    assert len(saved["receipts"]) == 600
    assert saved["restore_attempts"] + recovered.payload["prior_restore_attempts"] == 6
    for name in recovery.A0_FILES:
        assert (recovered.destination / "A0" / name).read_bytes() == (
            recovered.source / "A0" / name
        ).read_bytes()
    with pytest.raises(guard.CampaignStopped):
        with guard.Campaign(recovered.destination, recovered.plan, len).activate():
            pytest.fail("Completed successor replayed")


@pytest.mark.parametrize("case", ["ancestor", "imported", "bridge"])
def test_pre_mutation_rechecks_lineage_import_and_bridge(recovered, monkeypatch, case):
    instance = guard.Campaign(recovered.destination, recovered.plan, len)
    with pytest.raises(guard.CampaignStopped, match="changed"):
        with instance.activate():
            if case == "ancestor":
                recovered.ledger.write_text("{}")
            elif case == "imported":
                (recovered.destination / "A0" / recovery.A0_FILES[0]).write_text("{}")
            else:
                monkeypatch.setattr(recovery, "source_bridge", lambda *_: {})
            instance.pending_restore("A1", {"synthetic": True})
    assert instance.state["restore_attempts"] == 0
    assert instance.state["restore_pending"] is None


def test_handoff_rechecks_imported_a0(recovered):
    with guard.Campaign(recovered.destination, recovered.plan, len).activate() as instance:
        finish_full_arm(instance, recovered, "A1")
    path = recovered.destination / "A0" / recovery.A0_FILES[2]
    path.write_text("{}")
    before = instance.state_path.read_bytes()
    with pytest.raises(guard.CampaignStopped, match="Imported A0 changed"):
        with guard.Campaign(recovered.destination, recovered.plan, len).activate():
            pytest.fail("Tampered import accepted")
    assert before == instance.state_path.read_bytes()


@pytest.mark.asyncio
async def test_launch_rejects_ancestor_tamper_before_services(recovered, tmp_path, monkeypatch):
    plan_path = tmp_path / "new-plan.json"
    write(plan_path, recovered.payload)
    forbidden = AsyncMock(side_effect=AssertionError("Service access after lineage tamper"))
    monkeypatch.setattr(launch.asyncpg, "connect", forbidden)
    monkeypatch.setattr(launch, "rates", forbidden)
    recovered.ledger.write_text("{}")
    with pytest.raises(guard.CampaignStopped, match="ancestor changed"):
        await launch.run_arm(
            argparse.Namespace(
                plan=plan_path,
                campaign=recovered.destination,
                arm="A1",
                action_review=tmp_path / "unused-review.json",
            )
        )
    forbidden.assert_not_called()
    assert not recovered.destination.exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("arm_index", 1),
        ("restore_attempts", 0),
        ("question_index", 49),
        ("qualifications", []),
        ("receipts", []),
    ],
)
def test_ready_handoff_cannot_reset_recovery_offset(recovered, field, value):
    with guard.Campaign(recovered.destination, recovered.plan, len).activate() as instance:
        finish_full_arm(instance, recovered, "A1")
    state = json.loads(instance.state_path.read_bytes())
    state[field] = value
    write(instance.state_path, state)
    before = instance.state_path.read_bytes()
    with pytest.raises(guard.CampaignStopped, match="Recovery handoff"):
        with guard.Campaign(recovered.destination, recovered.plan, len).activate():
            pytest.fail("Reset recovery offset accepted")
    assert before == instance.state_path.read_bytes()


def test_finish_cannot_duplicate_arm_without_new_execution(recovered):
    with pytest.raises(guard.CampaignStopped, match="full new execution"):
        with guard.Campaign(recovered.destination, recovered.plan, len).activate() as instance:
            finish_full_arm(instance, recovered, "A1")
            instance.finish_arm(qualification={"synthetic": "duplicate"})


def test_empty_recovery_activation_is_not_a_clean_handoff(recovered):
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with guard.Campaign(recovered.destination, recovered.plan, len).activate():
            pass
    state = json.loads((recovered.destination / "campaign.json").read_bytes())
    assert state["arm_index"] == 1
    assert state["status"] == "stopped_reconciliation_required"


@pytest.mark.parametrize(
    "changed", ["orchestrator/memory/retrieval.py", "providers/new.py", "config/policy.json"]
)
def test_source_bridge_rejects_other_runtime_changes(monkeypatch, changed):
    monkeypatch.setattr(launch, "git_bytes", lambda *_: b"synthetic-source")
    monkeypatch.setattr(recovery, "_git", lambda *_: changed.encode() + b"\n")
    with pytest.raises(guard.CampaignStopped, match="other runtime"):
        REAL_SOURCE_BRIDGE(recovery.OLD_CONTROL, "new-control")


def test_source_bridge_binds_exact_diff_and_each_file_hash(monkeypatch):
    monkeypatch.setattr(launch, "git_bytes", lambda sha, path: (sha + path).encode())

    def git(*args):
        if "--name-only" in args:
            if args[0] == "diff":
                return "\n".join(recovery.BRIDGE_FILES).encode()
            return b"" if args[-1].endswith("/campaign_recovery.py") else b"exists"
        return b"synthetic-exact-binary-diff"

    monkeypatch.setattr(recovery, "_git", git)
    bridge = REAL_SOURCE_BRIDGE(recovery.OLD_CONTROL, "new-control")
    import hashlib

    assert bridge["diff_sha256"] == hashlib.sha256(b"synthetic-exact-binary-diff").hexdigest()
    assert set(bridge["files"]) == set(recovery.BRIDGE_FILES)
    assert bridge["files"]["orchestrator/eval/campaign_recovery.py"]["before"] is None
    for path, hashes in bridge["files"].items():
        assert hashes["after"] == hashlib.sha256(("new-control" + path).encode()).hexdigest()


@pytest.mark.parametrize("case", ["a0", "without_restore", "repeat", "exhausted"])
def test_recovery_cannot_execute_a0_skip_or_repeat_restore(recovered, case):
    with pytest.raises(guard.CampaignStopped):
        with guard.Campaign(recovered.destination, recovered.plan, len).activate() as instance:
            if case in {"a0", "without_restore"}:
                instance.start_arm(
                    "A0" if case == "a0" else "A1",
                    verified_pins=recovered.plan.pins,
                    restore={"synthetic": True},
                )
            elif case == "repeat":
                instance.pending_restore("A1", {"synthetic": True})
                instance.pending_restore("A1", {"synthetic": True})
            else:
                instance.state.update(arm_index=4, restore_attempts=3)
                instance.pending_restore("C0", {"synthetic": True})


def test_diagnostics_static_bounded_cycle_safe_and_no_properties():
    secret = "https://fictional.invalid/?api_key=synthetic-secret"

    class HostileError(Exception):
        def __str__(self):
            raise AssertionError("Must never serialize exceptions")

        @property
        def status_code(self):
            raise AssertionError("Must not inspect properties")

    APIError = type("APIError", (Exception,), {})
    cause = APIError("Provider returned an empty response " + secret)
    cause.__dict__.update(status_code=502, headers={"Authorization": secret})
    exc = HostileError(secret)
    exc.__cause__ = cause
    cause.__cause__ = exc
    data = recovery.failure_diagnostics(exc)
    assert data == {
        "classification": "provider_empty_response",
        "cause_chain": [
            {"exception_type": "UnknownError", "http_status_code": None},
            {"exception_type": "APIError", "http_status_code": 502},
        ],
    }
    assert "secret" not in json.dumps(data)
    for code in (True, "502", 99, 600):
        cause.__dict__["status_code"] = code
        assert recovery.failure_diagnostics(cause)["cause_chain"][0]["http_status_code"] is None
    cause.args = ("empty response " + secret,)
    assert recovery.failure_diagnostics(cause)["classification"] == "provider_error"
    tail = APIError("synthetic")
    for _ in range(10):
        newer = APIError("synthetic")
        newer.__cause__ = tail
        tail = newer
    assert len(recovery.failure_diagnostics(tail)["cause_chain"]) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("persistence_failure", [False, True])
async def test_real_hook_failure_artifact_is_separate_and_pending_retained(
    tmp_path, monkeypatch, persistence_failure
):
    import litellm
    from tests.test_lme_campaign_guard import campaign

    APIError = type("APIError", (Exception,), {})
    error = APIError("synthetic-secret Provider returned an empty response")
    error.__dict__["status_code"] = 502
    mock = AsyncMock(side_effect=error)
    monkeypatch.setattr(litellm, "acompletion", mock)
    config = SimpleNamespace(
        timeout_s=1, base_url=guard.BASE_URL, api_key="synthetic-secret", extra_headers={}
    )
    monkeypatch.setattr(
        evaluate, "get_settings", lambda: SimpleNamespace(get_provider_config=lambda _: config)
    )
    monkeypatch.setattr(guard, "LLM_START_INTERVAL", 0)
    instance = campaign(tmp_path)
    original_write = guard._write

    def fail_write(path, value):
        if path.name.startswith("failure-"):
            raise OSError("synthetic diagnostic durability failure")
        original_write(path, value)

    with pytest.raises(OSError if persistence_failure else evaluate.BenchmarkProviderError):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            send("embedding")
            if persistence_failure:
                monkeypatch.setattr(guard, "_write", fail_write)
            await evaluate._call_llm_with_provider_config(
                guard.LLM_MODEL, [{"role": "user", "content": "fictional"}], 0, 256, "answer"
            )
    assert mock.await_count == 1
    state = json.loads(instance.state_path.read_bytes())
    assert state["pending"]["stage"] == "answer"
    assert len(state["receipts"]) == 1
    assert not (instance.directory / "receipt-0001.json").exists()
    if not persistence_failure:
        artifact = instance.directory / "failure-0001.json"
        data = json.loads(artifact.read_bytes())
        assert data["pending"] == state["pending"]
        assert data["diagnostics"]["classification"] == "provider_empty_response"
        assert "synthetic-secret" not in artifact.read_text()
    assert instance._stopped


def test_inactive_failure_details_is_default_inert():
    with guard.campaign_call("unqualified", {}) as receipt:
        receipt.failure_details(RuntimeError("synthetic-secret"))
