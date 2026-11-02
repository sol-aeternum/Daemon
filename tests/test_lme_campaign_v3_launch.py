"""B2b3: fictional-only CLI, qualification and real-guard launch integration."""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import copy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call as mock_call

import pytest

from orchestrator.eval import bench_db, campaign_guard as guard, campaign_launch as launch
from orchestrator.eval import campaign_restore as restore, campaign_retry as retry
from orchestrator.eval import campaign_v3 as v3, campaign_v3_pilot as pilot, fact_harness
from orchestrator.eval import campaign_validation as validation
from orchestrator.eval.campaign_layout import layout
from tests.test_lme_campaign_guard import response, send
from tests.test_lme_campaign_launch import qualified_artifacts
from tests.test_lme_campaign_retry import call, fake_time, llm
from tests.test_lme_campaign_v3_harness import completed_row
from tests.test_lme_campaign_v3_pilot import approved as approved, write


def artifacts(arm="A0", corpus=None):
    legacy, dataset, manifest, checkpoint, _ = qualified_artifacts()
    rows = []
    for entry, old in zip(dataset, legacy, strict=True):
        entry["question"] = "fictional question " + entry["question_id"]
        entry["question_date"] = "2023/05/02 (Tue) 10:00"
        row = completed_row()
        row.update(
            question_id=entry["question_id"],
            question=entry["question"],
            reference=entry["answer"],
            question_type=entry["question_type"],
            category=old["category"],
            official_majority=old["official_judgment"],
            generic_strict_majority=old["judgment"],
        )
        for sample in row["samples"]:
            sample.update(judgment=old["judgment"], official_judgment=old["official_judgment"])
        rows.append(row)
    if arm == "A1":
        rows[0]["samples"][0].update(
            judgment="incorrect",
            official_judgment="incorrect",
            hypothesis="different fictional answer",
        )
    # The B2b1 production summary paths, not a handwritten sampled manifest.
    manifest["phases"]["evaluate"].update(fact_harness._v3_evaluate_summary(rows))
    score = fact_harness.build_score_payload(rows, layout=layout("v3"))
    manifest["phases"]["score"] = copy.deepcopy(score)
    if corpus:
        manifest["ingest_snapshot"]["sha256"] = corpus[0]
        for key in ("state_fingerprint_start", "state_fingerprint_end"):
            manifest["phases"]["evaluate"][key]["sha256"] = corpus[1]
    return rows, dataset, manifest, checkpoint, score


def test_qualification_accepts_b2b1_manifest_and_score():
    evidence = artifacts()
    qualified = pilot.qualification(
        *evidence, corpus=(validation.SNAPSHOT_SHA256, validation.STATE_SHA256)
    )
    assert qualified["v3"] == v3.v3_summaries(evidence[0])
    assert qualified["v3"]["samples"]["judged"] == 150
    assert qualified["v3"]["majority"]["judged"] == 50
    assert qualified["extraction_failures"] == 0


# Each unchanged manifest/checkpoint/artifact check has its own refusal and
# cross-check against the legacy verifier on the very same corrupted field.
COMMON_CORRUPTIONS = [
    ("score_errors", 2, ("phases", "score", "error_count"), 1),
    ("artifact_errors", 4, ("error_count",), 1),
    ("score_count", 2, ("phases", "score", "result_count"), 150),
    ("artifact_count", 4, ("result_count",), 150),
    *[
        (p + "_incomplete", 3, ("phases", p, "status"), "running")
        for p in ("ingest", "evaluate", "score")
    ],
    (
        "extraction_failed",
        3,
        ("phases", "ingest", "results"),
        {"x": {"status": "extraction_failed"}},
    ),
    ("empty_ingest", 3, ("phases", "ingest", "results"), {}),
    (
        "checkpoint_rollback",
        3,
        ("phases", "ingest", "ingest_rollbacks"),
        [{"restored_pre_session_state": False}],
    ),
    ("manifest_rollback", 2, ("ingest_rollbacks",), [{"restored_pre_session_state": False}]),
    ("unqualified", 2, ("baseline_qualified",), False),
    ("disqualified", 2, ("disqualifications",), ["fictional failure"]),
    ("code_pin_changes", 2, ("code_pin_changes",), ["changed"]),
    ("ingest_source", 2, ("ingest_source", "baseline_qualified"), False),
    ("dirty", 2, ("git", "dirty"), True),
    ("dataset", 2, ("dataset", "sha256"), "wrong"),
    ("snapshot", 2, ("ingest_snapshot", "sha256"), "wrong"),
    ("substrate", 2, ("substrate",), "wrong"),
    ("state_start", 2, ("phases", "evaluate", "state_matches_ingest_at_start"), False),
    ("state_unchanged", 2, ("phases", "evaluate", "state_unchanged_by_evaluate"), False),
    *[
        (k, 2, ("phases", "evaluate", k, "sha256"), "wrong")
        for k in ("state_fingerprint_start", "state_fingerprint_end")
    ],
    ("retrieval", 2, ("phases", "evaluate", "retrieval_modes"), {"lexical": 50}),
    *[
        (p + "_fingerprint", 2, ("phases", "evaluate", "provider_fingerprints", p), {})
        for p in ("answer", "judge")
    ],
    ("evaluate_summary", 2, ("phases", "evaluate", "official_judgments"), {}),
    ("score_summary", 2, ("phases", "score", "dual_judgments"), {}),
    ("artifact_summary", 4, ("dual_judgments",), {}),
]


@pytest.mark.parametrize(
    "name,index,path,value", COMMON_CORRUPTIONS, ids=[c[0] for c in COMMON_CORRUPTIONS]
)
def test_qualification_refusal_parity(name, index, path, value):
    reasons = {
        "score_errors": "Score errors",
        "artifact_errors": "Score errors",
        "score_count": "Score count differs",
        "artifact_count": "Score count differs",
        "ingest_incomplete": "ingest incomplete",
        "evaluate_incomplete": "evaluate incomplete",
        "score_incomplete": "score incomplete",
        "extraction_failed": "Ingest extraction failures",
        "empty_ingest": "Ingest extraction failures",
        "checkpoint_rollback": "Unproven ingest rollback",
        "manifest_rollback": "Unproven ingest rollback",
        "unqualified": "Run unqualified",
        "disqualified": "Run unqualified",
        "code_pin_changes": "Code changed during run",
        "ingest_source": "Ingest source unqualified",
        "dirty": "Dirty execution code",
        "dataset": "Manifest dataset differs",
        "snapshot": "Snapshot differs",
        "substrate": "Wrong substrate",
        "state_start": "State mismatch override",
        "state_unchanged": "State mutated during evaluate",
        "state_fingerprint_start": "Protected fingerprint differs",
        "state_fingerprint_end": "Protected fingerprint differs",
        "retrieval": "Retrieval mode/count differs",
        "answer_fingerprint": "Provider fingerprints absent",
        "judge_fingerprint": "Provider fingerprints absent",
    }
    for evidence, sampled in ((qualified_artifacts(), False), (artifacts(), True)):
        target = evidence[index]
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = copy.deepcopy(value)
        summaries = {
            "evaluate_summary": "Evaluate v3 summary differs"
            if sampled
            else "Evaluate official summary differs",
            "score_summary": "Score manifest v3 summary differs"
            if sampled
            else "Score manifest dual summary differs",
            "artifact_summary": "Score artifact v3 summary differs"
            if sampled
            else "Score artifact dual summary differs",
        }
        reason = summaries[name] if name in summaries else reasons[name]
        with pytest.raises(guard.CampaignStopped, match=reason):
            if sampled:
                pilot.qualification(
                    *evidence, corpus=(validation.SNAPSHOT_SHA256, validation.STATE_SHA256)
                )
            else:
                validation.qualification(*evidence)


@pytest.mark.parametrize(
    "index,path",
    [
        (2, ("phases", "evaluate", "v3_summaries")),
        (2, ("phases", "score", "v3_summaries")),
        (4, ("v3_summaries",)),
    ],
)
def test_v3_summary_refusals(index, path):
    evidence = artifacts()
    target = evidence[index]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = {}
    with pytest.raises(guard.CampaignStopped, match="summary differs"):
        pilot.qualification(*evidence, corpus=(validation.SNAPSHOT_SHA256, validation.STATE_SHA256))


@pytest.mark.parametrize(
    "bad", ["sample_status", "sample_error", "coverage", "majority", "disagreement"]
)
def test_v3_row_level_refusals(bad):
    evidence = artifacts()
    rows = evidence[0]
    if bad == "sample_status":
        rows[0]["samples"][0]["official_judge_status"] = "failed"
    elif bad == "sample_error":
        rows[0]["samples"][0]["error"] = "synthetic"
    elif bad == "coverage":
        rows.pop()
    elif bad == "majority":
        rows[0]["official_majority"] = "incorrect"
    else:
        for row in rows:
            for sample in row["samples"]:
                sample["judgment"] = (
                    "incorrect" if sample["official_judgment"] == "correct" else "correct"
                )
            row["generic_strict_majority"] = row["samples"][0]["judgment"]
    reason = {
        "sample_status": "V3 official judge is not completed",
        "sample_error": "V3 sample has an error",
        "coverage": "V3 coverage requires exactly one row per frozen dev ID",
        "majority": "V3 official majority differs",
        "disagreement": "Judge disagreement exceeds 20 percent",
    }[bad]
    with pytest.raises(guard.CampaignStopped, match=reason):
        pilot.qualification(*evidence, corpus=(validation.SNAPSHOT_SHA256, validation.STATE_SHA256))


class Connection:
    def __init__(self):
        self.closed = False
        self.execute = AsyncMock()
        self.fetchval = AsyncMock(return_value=0)

    async def close(self):
        self.closed = True

    @asynccontextmanager
    async def transaction(self, **options):
        assert options == {"isolation": "repeatable_read", "readonly": True}
        yield


@pytest.fixture
def preparation(approved, monkeypatch):
    root = approved.directory.parent
    dataset = artifacts()[1]
    source = root / "source.json"
    write(source, {"baseline_qualified": True, "disqualifications": []})
    ingest = root / "ingest.json"
    write(ingest, {"dataset_sha256": validation.DEV_SHA256})
    args = argparse.Namespace(
        v3_pilot=True,
        fresh_campaign=False,
        candidate=None,
        candidate_sha=None,
        historical_remap=approved.lineage,
        snapshot=Path(approved.plan.pins["snapshot"]["path"]),
        control_sha="control",
        ingest=ingest,
        cost_preflight=root / "cost.json",
        output=root / "plan.json",
    )
    monkeypatch.setattr(launch, "git_state", lambda _: {"sha": "control", "dirty": False})
    monkeypatch.setattr(launch, "pilot_checkout_clean", Mock())
    monkeypatch.setattr(launch, "load_dev", lambda _: dataset)
    monkeypatch.setattr(launch, "environment", lambda: {"synthetic": True})
    monkeypatch.setattr(launch, "pilot_common", lambda _: {})
    monkeypatch.setattr(launch, "verify_common", Mock(side_effect=AssertionError("No candidate")))
    monkeypatch.setattr(launch.fact_harness, "memory_formatter_identity", lambda: {"id": "control"})
    monkeypatch.setattr(
        launch,
        "tokenizer",
        lambda: (SimpleNamespace(name="o200k_base", encode_ordinary=list), "tokenizer"),
    )
    historical = {**approved.plan.pins["historical_remap"], "source_manifest": str(source)}
    monkeypatch.setattr(launch, "_historical_remap_pins", lambda _: historical)
    cost = {
        "provider_calls": 0,
        "writes_or_restores": 0,
        "bindings": pilot.bindings("control", historical["lineage_sha256"], "tokenizer"),
        "questions": [
            {"question_id": r["question_id"], "reader_input_token_bound": 500} for r in dataset
        ],
    }
    write(args.cost_preflight, cost)
    connection = Connection()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(launch.asyncpg, "connect", connect)
    monkeypatch.setattr(launch, "rates", lambda: {})
    monkeypatch.setattr(launch, "container_identity", lambda: {})
    monkeypatch.setattr(launch, "verify_server", AsyncMock())
    identity = {"datallowconn": True, "system_identifier": "synthetic", "oid": 1}
    monkeypatch.setattr(launch, "target_identity", AsyncMock(return_value=identity))
    fingerprint = {"sha256": validation.STATE_SHA256}
    monkeypatch.setattr(
        bench_db, "fingerprint_benchmark_users", AsyncMock(return_value=fingerprint)
    )
    monkeypatch.setattr(launch, "catalogue", AsyncMock(return_value="synthetic"))
    prior = Mock(wraps=pilot.verify_prior)
    monkeypatch.setattr(pilot, "verify_prior", prior)

    def render(snapshot, directory, digest):
        assert snapshot == args.snapshot and digest == validation.digest_file(snapshot)
        path = directory / "restore.sql"
        path.write_text("-- synthetic SQL, never executed\n")
        return path

    renderer = Mock(side_effect=render)
    monkeypatch.setattr(launch, "render_archive", renderer)
    return SimpleNamespace(
        args=args,
        cost=cost,
        historical=historical,
        prior=prior,
        renderer=renderer,
        connect=connect,
        connection=connection,
        fingerprint=fingerprint,
        identity=identity,
        dataset=dataset,
    )


@pytest.mark.asyncio
async def test_prepare_exact_pins_allocation_real_script_and_exclusive_write(preparation, capsys):
    p = preparation
    await launch.prepare(p.args)
    plan = launch.load_plan(p.args.output)
    assert set(plan.pins) == pilot.PIN_KEYS
    assert plan.pins["arm_shas"] == {"A0": "control", "A1": "control"}
    assert plan.pins["v3_campaign"] == {"approval": pilot.APPROVAL, "prior": pilot.PRIOR}
    script = Path(plan.pins["restore_script"]["path"])
    assert plan.pins["restore_script"]["sha256"] == validation.digest_file(script)
    assert plan.pins["restore_script"]["sha256"] != "0" * 64
    assert plan.payload()["allocated_usd"] == "3.7010500"
    p.prior.assert_called_with()
    assert all(record == mock_call() for record in p.prior.call_args_list)
    assert plan.pins["v3_campaign"]["prior"] == pilot.PRIOR
    assert p.connect.await_count == 2 and p.connection.closed
    assert all(
        c.kwargs["server_settings"]["default_transaction_read_only"] == "on"
        for c in p.connect.await_args_list
    )
    printed = json.loads(capsys.readouterr().out)
    assert printed["retry_budget_usd"] == "1.10"
    assert printed["cumulative_reserved_usd"] == "34.5396990"
    before = p.args.output.read_bytes()
    with pytest.raises(guard.CampaignStopped, match="already exists"):
        await launch.prepare(p.args)
    assert p.args.output.read_bytes() == before


@pytest.mark.asyncio
async def test_d2_d6_payload_refusal_leaves_no_artifacts_then_rerun(preparation):
    p = preparation
    for row in p.cost["questions"]:
        row["reader_input_token_bound"] = 1_000_000
    write(p.args.cost_preflight, p.cost)
    before = set(p.args.output.parent.iterdir())
    with pytest.raises(guard.CampaignStopped, match="base cap"):
        await launch.prepare(p.args)
    assert set(p.args.output.parent.iterdir()) == before
    p.renderer.assert_not_called()
    for row in p.cost["questions"]:
        row["reader_input_token_bound"] = 500
    write(p.args.cost_preflight, p.cost)
    await launch.prepare(p.args)
    assert launch.load_plan(p.args.output).pins["restore_script"]["path"] != "<pending>"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [validation.STATE_SHA256, "synthetic-remapped-state", "other"])
async def test_prepare_target_state(preparation, state):
    p = preparation
    p.fingerprint["sha256"] = state
    if state == "other":
        before = set(p.args.output.parent.iterdir())
        with pytest.raises(guard.CampaignStopped, match="protected fingerprint"):
            await launch.prepare(p.args)
        p.renderer.assert_not_called()
        assert not p.args.output.exists()
        assert set(p.args.output.parent.iterdir()) == before
        p.fingerprint["sha256"] = validation.STATE_SHA256
        await launch.prepare(p.args)
        assert launch.load_plan(p.args.output).pins["original_allow_connections"] is True
    else:
        await launch.prepare(p.args)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["provider_calls", "writes_or_restores", "bindings", "questions"])
async def test_prepare_preflight_refusals(preparation, key):
    p = preparation
    p.cost[key] = 1 if key.endswith(("calls", "restores")) else ({} if key == "bindings" else [])
    write(p.args.cost_preflight, p.cost)
    with pytest.raises(guard.CampaignStopped, match="preflight"):
        await launch.prepare(p.args)
    p.connect.assert_not_awaited()


def test_pilot_common_is_control_only(monkeypatch):
    read = Mock(return_value=b"synthetic source\n")
    monkeypatch.setattr(launch, "git_bytes", read)
    assert launch.pilot_common("control") == dict.fromkeys(
        launch.COMMON, "b2c70bba7bf0ef43959341e03b923efd23fed1a0b3ed1895736736d88a5b39b0"
    )
    assert read.call_args_list == [(("control", path),) for path in launch.COMMON]


@pytest.mark.parametrize("fault", ["untracked", "dotenv", None])
def test_pilot_control_requires_clean_checkout(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    monkeypatch.setattr(
        launch.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(stdout="?? runtime.py" if fault == "untracked" else "")),
    )
    if fault == "dotenv":
        (tmp_path / ".env").write_text("# fictional, no credentials\n")
    if fault:
        reason = (
            "Dotenv checkout is not eligible"
            if fault == "dotenv"
            else "Untracked or changed pilot control checkout"
        )
        with pytest.raises(guard.CampaignStopped, match=reason):
            launch.pilot_checkout_clean()
    else:
        launch.pilot_checkout_clean()


def test_pilot_evidence_is_exclusive(tmp_path):
    path = tmp_path / "evidence.json"
    launch.write_pilot_evidence(path, {"initial": True})
    before = path.read_bytes()
    with pytest.raises(FileExistsError, match="Pilot evidence already exists: .*evidence.json"):
        launch.write_pilot_evidence(path, {"replacement": True})
    assert path.read_bytes() == before
    assert set(tmp_path.iterdir()) == {path}


def test_pilot_evidence_temp_unlink_failure_never_masks_outcome(tmp_path, monkeypatch):
    path = tmp_path / "report.json"

    def stuck(temporary):
        raise OSError("injected unlink failure")

    monkeypatch.setattr(launch.os, "unlink", stuck)
    launch.write_pilot_evidence(path, {"value": True})
    assert json.loads(path.read_bytes()) == {"value": True}
    failure = OSError("injected link failure")
    monkeypatch.setattr(launch.os, "link", Mock(side_effect=failure))
    with pytest.raises(OSError, match="injected link failure") as error:
        launch.write_pilot_evidence(tmp_path / "other.json", {"value": False})
    assert error.value is failure


def test_pilot_evidence_write_failure_leaves_no_artifacts(tmp_path, monkeypatch):
    failure = OSError("injected evidence write failure")

    def broken_dump(value, output, **kwargs):
        output.write('{"truncated":')
        raise failure

    monkeypatch.setattr(launch.json, "dump", broken_dump)
    with pytest.raises(OSError, match="injected evidence write failure") as error:
        launch.write_pilot_evidence(tmp_path / "plan.json", {"value": True})
    assert error.value is failure
    assert list(tmp_path.iterdir()) == []


def test_pilot_evidence_byte_exact_durable_publish(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    value = {"z": "snow \u2603", "a": 1}
    expected = b'{\n  "a": 1,\n  "z": "snow \\u2603"\n}\n'
    events = []
    fsync, link, unlink = launch.os.fsync, launch.os.link, launch.os.unlink

    def synced(fd):
        events.append("fsync")
        return fsync(fd)

    def published(temporary, final):
        assert Path(temporary).parent == path.parent
        assert Path(temporary).stat().st_mode & 0o777 == 0o600
        assert Path(temporary).read_bytes() == expected
        events.append("link")
        return link(temporary, final)

    def removed(temporary):
        events.append("unlink")
        return unlink(temporary)

    monkeypatch.setattr(launch.os, "fsync", synced)
    monkeypatch.setattr(launch.os, "link", published)
    monkeypatch.setattr(launch.os, "unlink", removed)
    launch.write_pilot_evidence(path, value)
    assert path.read_bytes() == expected
    assert path.stat().st_mode & 0o777 == 0o600
    assert set(tmp_path.iterdir()) == {path}
    assert events == ["fsync", "link", "unlink", "fsync"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["render", "second_payload", "verify", "plan_write"])
async def test_late_prepare_failure_cleans_only_owned_directory_and_reruns(
    preparation, monkeypatch, stage
):
    p = preparation
    before = set(p.args.output.parent.iterdir())
    directory = p.args.output.parent / (p.args.output.stem + "-restore-script")
    failure = RuntimeError("injected " + stage)
    with monkeypatch.context() as patch:
        if stage == "render":

            def broken_render(snapshot, directory, digest):
                (directory / "partial.sql").write_text("-- partial fictional script\n")
                raise failure

            patch.setattr(launch, "render_archive", broken_render)
        elif stage == "second_payload":
            original = guard.CampaignPlan.payload
            calls = []

            def payload(plan):
                calls.append(plan)
                if len(calls) == 2:
                    raise failure
                return original(plan)

            patch.setattr(guard.CampaignPlan, "payload", payload)
        elif stage == "verify":
            patch.setattr(pilot, "verify", Mock(side_effect=failure))
        else:
            patch.setattr(launch, "write_pilot_evidence", Mock(side_effect=failure))
        with pytest.raises(RuntimeError, match="injected " + stage) as error:
            await launch.prepare(p.args)
        assert error.value is failure
    assert not directory.exists() and not p.args.output.exists()
    assert set(p.args.output.parent.iterdir()) == before
    await launch.prepare(p.args)
    plan = launch.load_plan(p.args.output)
    assert directory.is_dir()
    assert plan.pins["restore_script"]["sha256"] == validation.digest_file(
        directory / "restore.sql"
    )


@pytest.mark.asyncio
async def test_failure_after_plan_publication_keeps_referenced_script(preparation, monkeypatch):
    p = preparation
    directory = p.args.output.parent / (p.args.output.stem + "-restore-script")
    failure = OSError("injected directory fsync failure after link")
    monkeypatch.setattr(launch, "_sync_directory", Mock(side_effect=failure))
    with pytest.raises(OSError, match="directory fsync failure after link") as error:
        await launch.prepare(p.args)
    assert error.value is failure
    assert p.args.output.exists() and directory.is_dir()
    plan = json.loads(p.args.output.read_bytes())
    assert plan["pins"]["restore_script"]["sha256"] == validation.digest_file(
        directory / "restore.sql"
    )


@pytest.mark.asyncio
async def test_concurrent_plan_writer_still_cleans_owned_script(preparation, monkeypatch):
    p = preparation
    directory = p.args.output.parent / (p.args.output.stem + "-restore-script")
    link = launch.os.link

    def racing_link(temporary, final):
        Path(final).write_text('{"concurrent": true}\n')
        return link(temporary, final)

    monkeypatch.setattr(launch.os, "link", racing_link)
    with pytest.raises(FileExistsError, match="Pilot evidence already exists"):
        await launch.prepare(p.args)
    assert not directory.exists()
    assert json.loads(p.args.output.read_bytes()) == {"concurrent": True}


@pytest.mark.asyncio
async def test_prepare_never_removes_preexisting_script_directory(preparation):
    p = preparation
    directory = p.args.output.parent / (p.args.output.stem + "-restore-script")
    directory.mkdir()
    preserved = directory / "preserved.sql"
    preserved.write_text("-- pre-existing fictional script\n")
    before = set(p.args.output.parent.iterdir())
    with pytest.raises(FileExistsError):
        await launch.prepare(p.args)
    assert set(p.args.output.parent.iterdir()) == before
    assert preserved.read_text() == "-- pre-existing fictional script\n"
    assert set(directory.iterdir()) == {preserved}
    p.renderer.assert_not_called()


def test_bindings_shape_and_preflight_validation(monkeypatch):
    monkeypatch.setattr(fact_harness, "memory_formatter_identity", lambda: {"id": "control"})
    bindings = pilot.bindings("control", "remap", "tokenizer")
    assert set(bindings) == {
        "mode",
        "dev_sha256",
        "control_sha",
        "control_formatter",
        "remap_lineage_sha256",
        "caps",
        "rates",
        "tokenizer_sha256",
    }
    assert bindings["mode"] == "pilot"
    good = {
        "provider_calls": 0,
        "writes_or_restores": 0,
        "bindings": bindings,
        "questions": [{"question_id": "q", "reader_input_token_bound": 10}],
    }
    pilot.validate_preflight(good, bindings, ["q"])
    for bound in (True, 0, -1, 1.5):
        bad = copy.deepcopy(good)
        bad["questions"][0]["reader_input_token_bound"] = bound
        with pytest.raises(
            guard.CampaignStopped, match="Missing or invalid integer usage/allocation"
        ):
            pilot.validate_preflight(bad, bindings, ["q"])


CLI_PREP = [
    "prepare",
    "--snapshot",
    "s",
    "--ingest",
    "i",
    "--cost-preflight",
    "c",
    "--output",
    "o",
    "--control-sha",
    "control",
    "--historical-remap",
    "h",
]
CLI_COST = [
    "cost-preflight",
    "--output",
    "o",
    "--control-sha",
    "control",
    "--historical-remap",
    "h",
]


@pytest.mark.parametrize(
    "base,extra",
    [
        (CLI_PREP, ["--candidate", "retrieval"]),
        (CLI_PREP, ["--candidate", "noted-dates"]),
        (CLI_PREP, ["--candidate-sha", "candidate"]),
        (CLI_PREP, ["--fresh-campaign"]),
        (CLI_COST, ["--fresh"]),
        (CLI_COST, ["--candidate-sha", "candidate"]),
        (CLI_COST, ["--fresh-campaign"]),
        (CLI_COST, ["--candidate", "retrieval"]),
        (["prepare-replacement"], []),
        (["prepare-retry-recovery"], []),
    ],
)
def test_cli_mixed_modes_refused(monkeypatch, base, extra):
    monkeypatch.setattr("sys.argv", ["campaign_launch", *base, "--v3-pilot", *extra])
    monkeypatch.setattr(launch.asyncio, "run", Mock(side_effect=AssertionError("CLI executed")))
    with pytest.raises(SystemExit) as exc:
        launch.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("base", [CLI_PREP, CLI_COST])
def test_cli_pilot_dispatch_without_candidate(monkeypatch, base):
    seen = []

    async def operation(args):
        seen.append(args)

    monkeypatch.setattr(launch, "prepare", operation)
    monkeypatch.setattr(launch, "fresh_cost_preflight", operation)
    monkeypatch.setattr("sys.argv", ["campaign_launch", *base, "--v3-pilot"])
    launch.main()
    assert len(seen) == 1 and seen[0].v3_pilot and seen[0].candidate_sha is None


@pytest.mark.asyncio
async def test_preflight_readonly_copy_and_no_plaintext(preparation, monkeypatch):
    from cryptography.fernet import Fernet

    p = preparation
    lineage = json.loads(p.args.historical_remap.read_bytes())
    lineage["copy"] = {"db": "daemon_lme_bench_fictional_copy", "oid": 42}
    write(p.args.historical_remap, lineage)
    monkeypatch.setitem(
        pilot.APPROVAL,
        "historical_remap_lineage_sha256",
        validation.digest_file(p.args.historical_remap),
    )
    saved = p.args.output.parent / "saved.jsonl"
    saved.write_text(
        "\n".join(
            json.dumps({"question_id": r["question_id"], "memory_context": ""}) for r in p.dataset
        )
    )
    monkeypatch.setattr(launch, "PHASE_D_A0_RESULTS", saved)
    key = Fernet.generate_key()
    monkeypatch.setattr(
        fact_harness,
        "get_settings",
        lambda: SimpleNamespace(daemon_encryption_key=key.decode()),
    )
    monkeypatch.setattr(launch, "DSN", "postgresql://fictional/daemon_lme_bench")
    p.connection.fetchrow = AsyncMock(return_value={"name": lineage["copy"]["db"], "oid": 42})
    p.connection.fetchval.return_value = "on"
    p.connection.fetch = AsyncMock(
        return_value=[
            {
                "user_id": bench_db.question_user_id(p.dataset[0]["question_id"]),
                "content": Fernet(key).encrypt(b"Fictional plaintext sentinel").decode(),
                "category": "fact",
                "tier": "l1",
                "status": "active",
                "valid_to": None,
                "valid_from": datetime(2023, 5, 1, tzinfo=UTC),
                "created_at": datetime(2023, 5, 1, tzinfo=UTC),
                "local_only": False,
            }
        ]
    )
    p.fingerprint["sha256"] = lineage["remapped_state_sha256"]
    args = argparse.Namespace(
        v3_pilot=True,
        candidate_sha=None,
        control_sha="control",
        historical_remap=p.args.historical_remap,
        output=p.args.output,
    )
    await launch.fresh_cost_preflight(args)
    result = json.loads(args.output.read_bytes())
    assert result["provider_calls"] == result["writes_or_restores"] == 0
    assert result["transaction_read_only"] is True and result["plaintext_exported"] is False
    assert len(result["questions"]) == 50
    assert result["bindings"] == pilot.bindings(
        "control", validation.digest_file(args.historical_remap), "tokenizer"
    )
    assert p.connect.await_args.args[0].endswith("/daemon_lme_bench_fictional_copy")
    assert '"memory_context"' not in args.output.read_text()
    assert "Fictional plaintext sentinel" not in args.output.read_text()
    assert result["rows_examined"] == 1
    p.connection.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["lineage", "candidate", "source", "target"])
async def test_preflight_refuses_before_connect(preparation, monkeypatch, fault):
    p = preparation
    args = argparse.Namespace(
        v3_pilot=True,
        candidate_sha=None,
        control_sha="control",
        historical_remap=p.args.historical_remap,
        output=p.args.output,
    )
    if fault == "lineage":
        monkeypatch.setitem(pilot.APPROVAL, "historical_remap_lineage_sha256", "wrong")
    elif fault == "candidate":
        args.candidate_sha = "candidate"
    elif fault == "source":
        args.control_sha = "wrong"
    else:
        value = json.loads(args.historical_remap.read_bytes())
        value["copy"] = {"db": launch.DSN.rsplit("/", 1)[1]}
        write(args.historical_remap, value)
        monkeypatch.setitem(
            pilot.APPROVAL,
            "historical_remap_lineage_sha256",
            validation.digest_file(args.historical_remap),
        )
    reason = {
        "lineage": "Fresh remap lineage is not approved",
        "candidate": "V3 preflight cannot mix candidate or legacy mode",
        "source": "Fresh preflight must run on the clean control checkout",
        "target": "Preflight must read the copy, not the target",
    }[fault]
    with pytest.raises(guard.CampaignStopped, match=reason):
        await launch.fresh_cost_preflight(args)
    p.connect.assert_not_awaited()


def action_review(path, plan_path, actual_arm, **overrides):
    write(
        path,
        {
            "supervisor": "astra",
            "status": "approved_for_exact_action",
            "plan_sha256": validation.digest_file(plan_path),
            "source_sha": "control",
            "arm": actual_arm,
            "review_session": "synthetic-review",
            "live_state_evidence_sha256": "synthetic-state",
            "reviewed_at": launch.datetime.now(UTC).isoformat(),
            **overrides,
        },
    )


@pytest.fixture
def execution(preparation, monkeypatch):
    p = preparation
    fake_time(monkeypatch)
    origin = guard.time.monotonic()

    class ReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 11, 2, tzinfo=UTC) + timedelta(
                seconds=guard.time.monotonic() - origin
            )
            return instant.astimezone(tz)

    monkeypatch.setattr(launch, "datetime", ReviewClock)
    transport = llm(monkeypatch, [])

    def reply(**_):
        active = guard.current_campaign()
        assert active is not None
        return response(active.state["pending"]["stage"])

    transport.side_effect = reply
    monkeypatch.setattr(launch, "checkout", Mock())
    monkeypatch.setattr(
        retry, "enabled", Mock(side_effect=AssertionError("No enabled() lookup on v3"))
    )
    monkeypatch.setattr(restore, "attached_pool", Mock(side_effect=AssertionError("No v3 attach")))
    monkeypatch.setattr(launch, "Campaign", Mock(side_effect=AssertionError("Use campaign_for")))
    monkeypatch.setattr(
        launch, "paired_result", Mock(side_effect=AssertionError("No paired verdict"))
    )
    monkeypatch.setattr(
        launch,
        "formatter_exception",
        Mock(side_effect=AssertionError("No pilot formatter exception")),
    )
    factory = Mock(wraps=pilot.campaign_for)
    monkeypatch.setattr(pilot, "campaign_for", factory)
    comparator = Mock(wraps=launch.comparability)
    monkeypatch.setattr(launch, "comparability", comparator)
    restores = []
    fault = {}
    completed = []

    @asynccontextmanager
    async def restored(campaign, arm, snapshot):
        assert snapshot == p.args.snapshot
        restores.append(arm)
        campaign.pending_restore(arm, {"synthetic": arm})
        receipt = {
            "arm": arm,
            "fingerprint": {"sha256": p.historical["state_sha256"]},
            "script_sha256": campaign.plan["pins"]["restore_script"]["sha256"],
            "owned_pids": list(range(10)),
        }
        campaign.restored(arm, receipt)
        campaign.start_arm(arm, verified_pins=campaign.plan["pins"], restore=receipt)
        yield
        completed.append(copy.deepcopy(campaign.state))

    monkeypatch.setattr(launch, "restored_pool", restored)

    class Runner:
        def __init__(self, **kwargs):
            self.output_path: Path = kwargs["output_path"]
            self.checkpoint_path: Path = kwargs["checkpoint_path"]
            self.score_path: Path = kwargs["score_path"]
            self.arm = self.output_path.parent.name
            self.evidence = artifacts(
                self.arm, (p.historical["snapshot_sha256"], p.historical["state_sha256"])
            )

        async def evaluate(self):
            campaign = guard.current_campaign()
            assert isinstance(campaign, guard.Campaign)
            self.output_path.parent.mkdir()
            for row in self.evidence[0]:
                campaign.begin_question(row["question_id"])
                send("embedding")
                for stage, _ in campaign.layout.slots[1:]:
                    await call(stage)
                campaign.finish_question(row)
            manifest = self.evidence[2]
            manifest["git"].update(sha="control")
            manifest["memory_formatter"] = {"id": "control"}
            manifest["ingest_source"]["historical_remap"] = p.historical
            manifest["config_pin"] = (
                "different" if self.arm == "A1" and fault.get("compare") else "same"
            )
            manifest["routes"] = dict.fromkeys(
                ("answer", "judge", "official_judge", "inference_policy", "model_routing"), "same"
            )
            write(self.output_path.parent / "run_manifest.json", manifest)
            write(self.checkpoint_path, self.evidence[3])
            self.output_path.write_text("\n".join(json.dumps(r) for r in self.evidence[0]) + "\n")
            return self.evidence[0]

        def score(self):
            write(self.score_path, self.evidence[4])
            return self.evidence[4]

    monkeypatch.setattr(fact_harness, "LongMemEvalFactRunner", Runner)
    return SimpleNamespace(
        preparation=p,
        transport=transport,
        factory=factory,
        restores=restores,
        completed=completed,
        fault=fault,
        comparator=comparator,
    )


async def run(execution, arm, **review_overrides):
    p = execution.preparation
    review = p.args.output.parent / (arm + "-review.json")
    action_review(review, p.args.output, arm, **review_overrides)
    await launch.run_arm(
        argparse.Namespace(
            plan=p.args.output,
            campaign=Path(pilot.APPROVAL["directory"]),
            arm=arm,
            action_review=review,
        )
    )


@pytest.mark.asyncio
async def test_real_guard_prepare_a0_a1_report_and_reopen(execution):
    p = execution.preparation
    await launch.prepare(p.args)
    await run(execution, "A0")
    directory = Path(pilot.APPROVAL["directory"])
    state = json.loads((directory / "campaign.json").read_bytes())
    assert state["status"] == "ready_next_arm" and len(state["receipts"]) == 500
    assert len(state["qualifications"]) == 1
    assert not (directory / "pilot-report.json").exists()
    p.connection.execute.assert_not_awaited()
    await run(execution, "A1")
    state = json.loads((directory / "campaign.json").read_bytes())
    assert state["status"] == "completed" and len(state["receipts"]) == 1000
    assert len(state["qualifications"]) == 2 and state["final_reopen"] == "completed"
    assert execution.restores == ["A0", "A1"] and execution.factory.call_count == 2
    assert execution.transport.await_count == 900
    assert [len(s["receipts"]) for s in execution.completed] == [500, 1000]
    report = json.loads((directory / "pilot-report.json").read_bytes())
    assert report["mode"] == "pilot" and report["rule"] == v3.RULE_V3
    assert report["matched_sample_flip_rate"]["numerator"] == 1
    assert report["matched_sample_flip_rate"]["denominator"] == 150
    assert report["majority_label_flip_rate"]["numerator"] == 0
    assert report["majority_label_flip_rate"]["denominator"] == 50
    assert report["all_cross_pairs_flip_rate"]["numerator"] == 3
    assert report["all_cross_pairs_flip_rate"]["denominator"] == 450
    assert report["byte_identical_questions"] == {"A0": 50, "A1": 49}
    assert report["passed"] is True and "accepted" not in report
    assert report["plan_sha256"] == validation.digest_file(p.args.output)
    completed_ledger = copy.deepcopy(state)
    completed_ledger.pop("final_reopen")
    assert (
        report["campaign_ledger_sha256"]
        == hashlib.sha256(guard._json(completed_ledger) + b"\n").hexdigest()
    )
    for arm in ("A0", "A1"):
        assert (
            report["artifacts"][arm]
            == state["qualifications"][("A0", "A1").index(arm)]["artifacts"]
        )
    assert not (directory / "paired-result.json").exists()
    p.connection.execute.assert_awaited_once_with(
        "ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS true"
    )
    assert execution.comparator.call_count == 1
    assert execution.comparator.call_args.kwargs == {"formatter": True}


@pytest.mark.asyncio
async def test_pilot_refuses_candidate_arm_before_actions(preparation):
    p = preparation
    await launch.prepare(p.args)
    with pytest.raises(guard.CampaignStopped, match="only A0/A1"):
        await launch.run_arm(
            argparse.Namespace(
                plan=p.args.output,
                campaign=Path(pilot.APPROVAL["directory"]),
                arm="C0",
                action_review=p.args.output.parent / "absent-review.json",
            )
        )
    p.connection.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["wrong_arm", "expired", "future", "wrong_source", "wrong_plan"])
async def test_action_review_refusals(execution, fault):
    p = execution.preparation
    await launch.prepare(p.args)
    changes = {
        "wrong_arm": {"arm": "A1"},
        "expired": {"reviewed_at": (launch.datetime.now(UTC) - timedelta(seconds=901)).isoformat()},
        "future": {"reviewed_at": (launch.datetime.now(UTC) + timedelta(seconds=1)).isoformat()},
        "wrong_source": {"source_sha": "wrong"},
        "wrong_plan": {"plan_sha256": "wrong"},
    }[fault]
    # Do not pass the review's wrong arm as the actual arm argument.
    review = p.args.output.parent / "review.json"
    action_review(review, p.args.output, "A0", **changes)
    reason = (
        "Action-state review expired"
        if fault in ("expired", "future")
        else "Action review not bound to this plan/source/arm/state"
    )
    with pytest.raises(guard.CampaignStopped, match=reason):
        await launch.run_arm(
            argparse.Namespace(
                plan=p.args.output,
                campaign=Path(pilot.APPROVAL["directory"]),
                arm="A0",
                action_review=review,
            )
        )
    assert execution.restores == []


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [0, 900])
async def test_action_review_accepts_exact_ttl_boundary(execution, monkeypatch, age):
    p = execution.preparation
    await launch.prepare(p.args)
    accepted = RuntimeError("review accepted before campaign activation")
    factory = Mock(side_effect=accepted)
    monkeypatch.setattr(pilot, "campaign_for", factory)
    with pytest.raises(RuntimeError, match="review accepted before campaign activation") as error:
        await run(
            execution,
            "A0",
            reviewed_at=(launch.datetime.now(UTC) - timedelta(seconds=age)).isoformat(),
        )
    assert error.value is accepted
    factory.assert_called_once()
    assert execution.restores == []


@pytest.mark.asyncio
async def test_a1_comparability_failure_stays_closed(execution):
    p = execution.preparation
    await launch.prepare(p.args)
    await run(execution, "A0")
    execution.fault["compare"] = True
    with pytest.raises(guard.CampaignStopped, match="Comparator config_pin"):
        await run(execution, "A1")
    directory = Path(pilot.APPROVAL["directory"])
    assert not (directory / "pilot-report.json").exists()
    p.connection.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["status", "pending", "restore_pending", "identity", "oid", "connections", "original"]
)
async def test_final_reopen_guards_leave_target_closed(execution, monkeypatch, fault):
    p = execution.preparation
    await launch.prepare(p.args)
    await run(execution, "A0")
    original_writer = launch.write_pilot_evidence
    original_loader = launch.load_plan
    loaded = []

    def load(path):
        plan = original_loader(path)
        loaded.append(plan)
        return plan

    monkeypatch.setattr(launch, "load_plan", load)

    def report(path, value):
        original_writer(path, value)
        if path.name != "pilot-report.json":
            return
        if fault in ("status", "pending", "restore_pending"):
            ledger = path.parent / "campaign.json"
            state = json.loads(ledger.read_bytes())
            state[fault] = "not completed" if fault == "status" else {"unresolved": True}
            write(ledger, state)
        elif fault in ("identity", "oid"):
            p.identity["system_identifier" if fault == "identity" else "oid"] = "wrong"
        elif fault == "connections":
            p.connection.fetchval.return_value = 1
        elif fault == "original":
            loaded[-1].pins["original_allow_connections"] = False

    monkeypatch.setattr(launch, "write_pilot_evidence", report)
    expected = {
        "status": "Incomplete campaign",
        "pending": "Incomplete campaign",
        "restore_pending": "Incomplete campaign",
        "identity": "Final target differs",
        "oid": "Final target differs",
        "connections": "Owned pool not closed",
        "original": "Original connection state differs",
    }[fault]
    with pytest.raises(guard.CampaignStopped, match=expected):
        await run(execution, "A1")
    p.connection.execute.assert_not_awaited()
