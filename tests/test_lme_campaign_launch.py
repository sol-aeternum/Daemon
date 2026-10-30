"""Offline gates and a separately owned TCP-disabled PostgreSQL16 restore fixture."""

from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from urllib.parse import quote
import uuid

import asyncpg
import pytest

from orchestrator.eval import campaign_guard as guard, campaign_restore as restore
from orchestrator.eval.campaign_sql import validate_sql
from orchestrator.eval.campaign_validation import paired_result
from orchestrator.eval import campaign_validation as validation
from tests.benchmark_longmemeval.native_fixture import NativePostgres
from tests.test_lme_campaign_guard import plan as synthetic_plan


@pytest.mark.parametrize(
    "changed",
    [
        None,
        "database_url",
        "embedding_route_id",
        "daemon_encryption_key",
        "daemon_auth_pepper",
        "openrouter_api_key",
    ],
)
def test_environment_checks_happen_without_provider_or_database_work(monkeypatch, changed):
    from orchestrator.eval import campaign_launch as launch
    from orchestrator.config import Settings
    from orchestrator.entitlements import policy as policy_module

    settings = Settings.model_construct(
        database_url=restore.DSN,
        embedding_route_id="azure-openrouter-small-1024",
        daemon_encryption_key="fictional",
        daemon_auth_pepper="fictional",
        openrouter_api_key="fictional",
    )
    if changed:
        setattr(
            settings, changed, None if "key" in changed or "pepper" in changed else "unapproved"
        )
    policy = policy_module.parse_inference_policy(
        json.loads((launch.ROOT / "config/inference_policy.production.json").read_bytes())
    )
    monkeypatch.setattr(launch.fact_harness, "get_settings", lambda: settings)
    monkeypatch.setattr(policy_module, "load_inference_policy", lambda: policy)
    monkeypatch.setattr(
        launch.fact_harness, "effective_config", lambda harness: {"synthetic": harness}
    )
    if changed:
        with pytest.raises(guard.CampaignStopped):
            launch.environment()
    else:
        assert launch.environment()["synthetic"]["benchmark_mode"] is True


def qualified_artifacts():
    types = list(validation.CATEGORY_MAP)
    dataset = [
        {
            "question_id": f"q{i}",
            "question": f"fictional {i}",
            "answer": "fixture",
            "question_type": types[i % len(types)],
        }
        for i in range(50)
    ]
    rows = [
        {
            "question_id": row["question_id"],
            "question": row["question"],
            "reference": row["answer"],
            "question_type": row["question_type"],
            "category": validation.CATEGORY_MAP[row["question_type"]],
            "official_judge_status": "completed",
            "judgment": "correct" if i < 30 else "incorrect",
            "official_judgment": "correct" if i < 30 else "incorrect",
        }
        for i, row in enumerate(dataset)
    ]
    dual = validation.score_dual_judgments(rows)
    score = {"dual_judgments": copy.deepcopy(dual), "error_count": 0, "result_count": 50}
    checkpoint: dict[str, Any] = {
        "phases": {phase: {"status": "completed"} for phase in ("ingest", "evaluate", "score")}
    }
    checkpoint["phases"]["ingest"]["results"] = {"synthetic": {"status": "complete"}}
    manifest = {
        "baseline_qualified": True,
        "disqualifications": [],
        "git": {"dirty": False},
        "dataset": {"sha256": validation.DEV_SHA256},
        "substrate": "fact",
        "ingest_snapshot": {"sha256": validation.SNAPSHOT_SHA256},
        "ingest_source": {"baseline_qualified": True},
        "phases": {
            "evaluate": {
                "official_judgments": copy.deepcopy(dual["official"]),
                "state_matches_ingest_at_start": True,
                "state_unchanged_by_evaluate": True,
                "state_fingerprint_start": {"sha256": validation.STATE_SHA256},
                "state_fingerprint_end": {"sha256": validation.STATE_SHA256},
                "retrieval_modes": {validation.RETRIEVAL_MODE: 50},
                "provider_fingerprints": {"answer": {"synthetic": 50}, "judge": {"synthetic": 50}},
            },
            "score": copy.deepcopy(score),
        },
    }
    return rows, dataset, manifest, checkpoint, score


def test_official_qualification_requires_real_matching_complete_artifacts():
    result = validation.qualification(*qualified_artifacts())
    assert result["official"]["judged"] == 50 and result["official"]["correct"] == 30


@pytest.mark.parametrize(
    "mutation",
    [
        lambda rows, data, m, c, s: rows.pop(),
        lambda rows, data, m, c, s: rows[0].update(official_judgment="partially_correct"),
        lambda rows, data, m, c, s: m["phases"]["score"].pop("dual_judgments"),
        lambda rows, data, m, c, s: m["phases"]["evaluate"].update(
            state_unchanged_by_evaluate=False
        ),
        lambda rows, data, m, c, s: m.update(baseline_qualified=False),
        lambda rows, data, m, c, s: m.update(
            ingest_rollbacks=[{"restored_pre_session_state": False}]
        ),
        lambda rows, data, m, c, s: m["phases"]["evaluate"].update(retrieval_modes={"lexical": 50}),
        lambda rows, data, m, c, s: c["phases"]["score"].update(status="running"),
    ],
)
def test_official_qualification_fails_closed(mutation):
    artifacts = qualified_artifacts()
    mutation(*artifacts)
    with pytest.raises((ValueError, guard.CampaignStopped)):
        validation.qualification(*artifacts)


@pytest.mark.parametrize(
    "payload",
    [
        "\\! touch forbidden\n",
        "\\connect production\n",
        "CREATE DATABASE other;\n",
        "ALTER ROLE bench PASSWORD 'bad';\n",
        "\\include other.sql\n",
        "SELECT 1;\n",
        "CREATE EXTENSION IF NOT EXISTS unapproved WITH SCHEMA public;\n",
    ],
)
def test_sql_scanner_rejects_control_changes(tmp_path, payload):
    path = tmp_path / "restore.sql"
    path.write_text(payload)
    with pytest.raises(guard.CampaignStopped):
        validate_sql(path)


def test_sql_scanner_keeps_copy_payload_out_of_command_parser(tmp_path):
    path = tmp_path / "restore.sql"
    path.write_text(
        "SET statement_timeout = 0;\nCREATE TABLE public.x (id int, text text);\nCOPY public.x (id, text) FROM stdin;\n1\t\\! forbidden-looking fixture data\n2\tDROP DATABASE other;\n\\.\n"
    )
    result = validate_sql(path)
    assert result["copy_lines"] == 2 and result["copy_blocks"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        "COPY public.x (id) FROM stdin;\n1\n",
        "CREATE TABLE public.x (id text DEFAULT 'unterminated);",
        "\\restrict abc\n",
        "/* unterminated",
        "\\.\n",
    ],
)
def test_sql_scanner_rejects_incomplete_and_unmatched_controls(tmp_path, payload):
    path = tmp_path / "restore.sql"
    path.write_text(payload)
    with pytest.raises(guard.CampaignStopped):
        validate_sql(path)


def test_paired_noise_uses_worst_control_pair_not_favorable_comparator():
    rows = [
        {
            "question_id": f"q{i}" + ("_abs" if i == 49 else ""),
            "category": "IE-user",
            "official_judgment": "correct" if i < 30 else "incorrect",
        }
        for i in range(50)
    ]
    arms = {arm: [dict(row) for row in rows] for arm in guard.ARMS}
    arms["A1"][30]["official_judgment"] = "correct"
    arms["A2"][31]["official_judgment"] = "correct"
    for i in (30, 31, 32):
        arms["C0"][i]["official_judgment"] = "correct"
    result = paired_result(arms)
    assert result["F"] == 2 and result["threshold"] == 4
    assert result["net_gain"] == 3 and not result["accepted"]


@pytest.fixture
def pg16(tmp_path):
    resources = os.environ.get("OPENCODE_WORK_TEST_DIR")
    if not resources:
        pytest.skip("requires owned PG16 fixture tools in registered resources")
    tools = Path(resources) / "pg16-tools-20261029"
    if not (tools / "fixture-tools.json").is_file():
        pytest.skip("owned cached PG16 tools not provisioned")
    fixture = NativePostgres(tools, Path(resources) / ("lme-pg16-" + uuid.uuid4().hex))
    try:
        fixture.start()
        assert fixture.sql("SHOW listen_addresses").strip() == ""
        fixture.sql("CREATE DATABASE daemon_lme_bench")
        fixture.sql("COMMENT ON DATABASE daemon_lme_bench IS 'daemon-longmemeval-benchmark-v1'")
        sql = "CREATE EXTENSION pgcrypto; CREATE EXTENSION vector;"
        for table in ("conversations", "messages", "memories", "memory_extraction_log", "entities"):
            sql += f"CREATE TABLE public.{table} (id uuid PRIMARY KEY, user_id uuid, content text);"
        sql += "INSERT INTO public.memories VALUES ('00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', 'fictional fixture');"
        fixture.sql(sql, database="daemon_lme_bench")
        yield fixture
    finally:
        fixture.close()


def dsn(fixture, database):
    return "postgresql:///" + database + "?user=bench&host=" + quote(fixture.socket, safe="")


@asynccontextmanager
async def prepared(pg16, tmp_path, monkeypatch):
    from orchestrator.eval.bench_db import fingerprint_benchmark_users, question_user_id

    db = dsn(pg16, "daemon_lme_bench")
    control_dsn = dsn(pg16, "postgres")
    c = await asyncpg.connect(db)
    try:
        catalogue = await restore.catalogue(c)
        ids = synthetic_plan().question_ids
        fingerprint = await fingerprint_benchmark_users(c, [question_user_id(qid) for qid in ids])
        server = str(await c.fetchval("SELECT system_identifier FROM pg_control_system()"))
        oid = await c.fetchval("SELECT oid FROM pg_database WHERE datname=current_database()")
    finally:
        await c.close()
    snapshot = tmp_path / "fixture.dump"
    pg16.command(
        "pg_dump",
        "-h",
        pg16.socket,
        "-U",
        "bench",
        "-d",
        "daemon_lme_bench",
        "-Fc",
        "-f",
        str(snapshot),
    )
    monkeypatch.setattr(restore, "DSN", db)
    monkeypatch.setattr(restore, "CONTROL_DSN", control_dsn)
    monkeypatch.setattr(restore, "SNAPSHOT_SHA256", restore.digest_file(snapshot))
    monkeypatch.setattr(restore, "STATE_SHA256", fingerprint["sha256"])
    monkeypatch.setattr(
        restore, "container_identity", lambda: {"id": "owned TCP-disabled synthetic fixture"}
    )

    async def verified(control):
        assert (
            str(await control.fetchval("SELECT system_identifier FROM pg_control_system()"))
            == server
        )

    monkeypatch.setattr(restore, "verify_server", verified)
    original_run, original_popen = subprocess.run, subprocess.Popen

    def translate(args):
        args = list(args)
        if args[0] != "docker":
            return args
        assert args[:4] == ["docker", "exec", "-i", restore.CONTAINER]
        program = args[4]
        assert program in {"pg_restore", "psql"}
        rest = args[5:]
        return [str(pg16.bin / program), *(["-h", pg16.socket] if program == "psql" else []), *rest]

    monkeypatch.setattr(
        restore.subprocess, "run", lambda args, **kw: original_run(translate(args), **kw)
    )
    monkeypatch.setattr(
        restore.subprocess, "Popen", lambda args, **kw: original_popen(translate(args), **kw)
    )
    script_directory = tmp_path / "prepared-script"
    script_directory.mkdir()
    script = restore.render_archive(snapshot, script_directory)
    base = synthetic_plan()
    plan = guard.CampaignPlan(
        base.question_ids,
        base.input_limits,
        {
            "server_identifier": server,
            "database_oid": oid,
            "catalogue_sha256": catalogue,
            "restore_script": {"path": str(script), "sha256": restore.digest_file(script)},
        },
    )
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign|inactive/stopped"):
        with guard.Campaign(tmp_path / "campaign", plan, len).activate() as campaign:
            yield campaign, snapshot, db, control_dsn


@pytest.mark.asyncio
async def test_native_inplace_restore_keeps_ten_owned_connections_and_admission_closed(
    pg16, tmp_path, monkeypatch
):
    async with prepared(pg16, tmp_path, monkeypatch) as (campaign, snapshot, db, control_dsn):
        async with restore.restored_pool(campaign, "A0", snapshot) as pool:
            assert await pool.fetchval("SELECT count(*) FROM memories") == 1
            control = await asyncpg.connect(control_dsn)
            try:
                assert (await restore.target_identity(control))["datallowconn"] is False
                with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError):
                    await asyncpg.connect(db, timeout=2)
            finally:
                await control.close()
            # More concurrent operations than the fixed size prove acquisitions
            # queue/release correctly without opening any eleventh backend.
            values = await asyncio.gather(*(pool.fetchval("SELECT 1") for _ in range(30)))
            assert values == [1] * 30
            await pool.close()  # Matches the canonical phase's ownership contract.
        assert campaign.state["restore_attempts"] == 1
        assert campaign.state["restore_pending"] is None


@pytest.mark.asyncio
async def test_native_foreign_connection_stops_before_public_schema_replacement(
    pg16, tmp_path, monkeypatch
):
    async with prepared(pg16, tmp_path, monkeypatch) as (campaign, snapshot, db, control_dsn):
        foreign = await asyncpg.connect(db)
        try:
            with pytest.raises(guard.CampaignStopped, match="Unowned"):
                async with restore.restored_pool(campaign, "A0", snapshot):
                    pytest.fail("Foreign producer admitted")
            # Existing foreign session is not terminated; data remains untouched.
            assert await foreign.fetchval("SELECT count(*) FROM memories") == 1
            control = await asyncpg.connect(control_dsn)
            try:
                assert (await restore.target_identity(control))["datallowconn"] is False
            finally:
                await control.close()
            assert campaign.state["restore_pending"]["arm"] == "A0"
        finally:
            await foreign.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["pool", "acquire", "spawn", "handshake", "cancel"])
async def test_native_setup_failure_reseals_initially_disabled_database(
    pg16, tmp_path, monkeypatch, stage
):
    async with prepared(pg16, tmp_path, monkeypatch) as (campaign, snapshot, db, control_dsn):
        observer = await asyncpg.connect(db)
        control = await asyncpg.connect(control_dsn)
        await control.execute("ALTER DATABASE daemon_lme_bench ALLOW_CONNECTIONS false")
        original_pool = restore.asyncpg.create_pool
        original_popen = restore.subprocess.Popen

        async def pool_factory(*args, **kwargs):
            assert (await restore.target_identity(control))["datallowconn"] is True
            if stage == "pool":
                raise RuntimeError("synthetic setup failure")
            if stage == "cancel":
                raise asyncio.CancelledError()
            pool = await original_pool(*args, **kwargs)
            if stage == "acquire":

                class BrokenAcquire:
                    def acquire(self, **kwargs):
                        raise RuntimeError("synthetic setup failure")

                    async def close(self):
                        await pool.close()

                return BrokenAcquire()
            return pool

        def spawn(*args, **kwargs):
            if stage == "spawn":
                raise RuntimeError("synthetic setup failure")
            process: Any = original_popen(*args, **kwargs)
            if stage == "handshake":

                class BadHandshake:
                    def readline(self):
                        return b"invalid PID\n"

                process.stdout = BadHandshake()
            return process

        monkeypatch.setattr(restore.asyncpg, "create_pool", pool_factory)
        monkeypatch.setattr(restore.subprocess, "Popen", spawn)
        try:
            with pytest.raises((RuntimeError, asyncio.CancelledError, guard.CampaignStopped)):
                async with restore.restored_pool(campaign, "A0", snapshot):
                    pytest.fail("Setup failure reached evaluation")
            assert (await restore.target_identity(control))["datallowconn"] is False
            assert await observer.fetchval("SELECT count(*) FROM memories") == 1
            saved = json.loads(campaign.state_path.read_text())
            assert saved["failure_admission"] == "closed"
            assert saved["restore_pending"]["arm"] == "A0"
        finally:
            await observer.close()
            await control.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["identity", "execute", "readback", "open"])
async def test_compensation_never_claims_unknown_admission_closed(monkeypatch, fault):
    calls = []

    async def identity(control):
        calls.append("identity")
        if fault == "identity" or (fault == "readback" and len(calls) > 1):
            raise ConnectionError("synthetic control loss")
        return {"system_identifier": "fixture", "oid": 1, "datallowconn": fault == "open"}

    class Control:
        async def execute(self, sql):
            calls.append("execute")
            if fault == "execute":
                raise ConnectionError("synthetic lost acknowledgment")

    monkeypatch.setattr(restore, "target_identity", identity)
    assert (
        await restore.seal_failed_admission(
            Control(), {"server_identifier": "fixture", "database_oid": 1}
        )
        == "unknown"
    )
    assert calls.count("execute") <= 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "CREATE SCHEMA outside; CREATE VIEW outside.dep AS SELECT * FROM public.memories",
        "REVOKE USAGE ON SCHEMA public FROM PUBLIC",
        "CREATE TYPE public.unapproved_standalone_type AS ENUM ('fixture')",
        "CREATE FUNCTION public.forbid_ddl() RETURNS event_trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'must not execute'; END$$; CREATE EVENT TRIGGER fixture_guard ON ddl_command_start EXECUTE FUNCTION public.forbid_ddl()",
    ],
)
async def test_native_out_of_scope_objects_rejected_before_cascade(
    pg16, tmp_path, monkeypatch, mutation
):
    async with prepared(pg16, tmp_path, monkeypatch) as (campaign, snapshot, db, control_dsn):
        pg16.sql(mutation, database="daemon_lme_bench")
        with pytest.raises(guard.CampaignStopped):
            async with restore.restored_pool(campaign, "A0", snapshot):
                pytest.fail("Unapproved persistent object reached cascade")
        assert campaign.state["restore_pending"]["arm"] == "A0"
        control = await asyncpg.connect(control_dsn)
        try:
            assert (await restore.target_identity(control))["datallowconn"] is False
        finally:
            await control.close()


@pytest.mark.asyncio
async def test_native_failed_sql_rolls_back_replacement_and_retains_pending(
    pg16, tmp_path, monkeypatch
):
    async with prepared(pg16, tmp_path, monkeypatch) as (campaign, snapshot, db, control_dsn):
        original = restore.subprocess.Popen

        class FaultyInput:
            def __init__(self, stream):
                self.stream = stream

            def write(self, data):
                if data.startswith(b"BEGIN;"):
                    data += (
                        b"\nSELECT 1 / 0;\n"  # Deliberate transport fault, not a changed archive.
                    )
                return self.stream.write(data)

            def flush(self):
                return self.stream.flush()

            def close(self):
                return self.stream.close()

        def faulty_process(args, **kwargs):
            process: Any = original(args, **kwargs)
            process.stdin = FaultyInput(process.stdin)
            return process

        monkeypatch.setattr(restore.subprocess, "Popen", faulty_process)
        with pytest.raises(guard.CampaignStopped, match="restore session failed"):
            async with restore.restored_pool(campaign, "A0", snapshot):
                pytest.fail("Failed SQL accepted")
        assert campaign.state["restore_pending"]["arm"] == "A0"
        assert campaign.state["restore_attempts"] == 1
        control = await asyncpg.connect(control_dsn)
        try:
            assert (await restore.target_identity(control))["datallowconn"] is False
        finally:
            await control.close()


def test_canonical_score_manifest_contains_identical_dual_summary(tmp_path):
    from orchestrator.eval import fact_harness
    from tests.test_longmemeval_parity_harness import _runner, _ingested_checkpoint, _write_dataset

    dataset = _write_dataset(tmp_path)
    runner = _runner(tmp_path, dataset)
    runner.checkpoint_path.parent.mkdir()
    checkpoint = _ingested_checkpoint(dataset)
    checkpoint["phases"]["evaluate"]["results"] = {
        "q1": {
            "question_id": "q1",
            "category": "IE-user",
            "judgment": "correct",
            "official_judgment": "incorrect",
            "hypothesis": "fictional fixture",
        }
    }
    runner.checkpoint_path.write_text(json.dumps(checkpoint))
    manifest_path = runner.checkpoint_path.parent / "run_manifest.json"
    manifest_path.write_text(
        json.dumps({"run_id": checkpoint["run_id"], "baseline_qualified": True, "phases": {}})
    )
    result = runner.score()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["phases"]["score"]["dual_judgments"] == result["dual_judgments"]
    assert (
        result["dual_judgments"]
        == fact_harness.build_score_payload(
            list(checkpoint["phases"]["evaluate"]["results"].values())
        )["dual_judgments"]
    )
