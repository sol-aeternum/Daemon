"""Synthetic replacement lineage and nonblocking send pacing; no live services."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.eval import campaign_guard as guard, campaign_launch as launch
from tests.test_lme_campaign_guard import active, plan, request, response, send


@pytest.mark.asyncio
async def test_llm_start_spacing_awaits_only_remaining_time(tmp_path, monkeypatch):
    clock = [0.0]
    waits = []

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(guard, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(guard, "asyncio", SimpleNamespace(sleep=sleep))
    with active(tmp_path):
        send("embedding")
        assert waits == []
        for stage in ("answer", "judge", "official_judge"):
            async with guard.campaign_llm_call(stage, request(stage)) as receipt:
                receipt.complete(response(stage))
            if stage == "answer":
                clock[0] += 7  # Slow calls already satisfy the next start interval.
        assert waits == [5.0, 5.0]


@pytest.mark.asyncio
async def test_stop_during_pacing_never_dispatches(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "time", SimpleNamespace(monotonic=lambda: 0.0))
    instance = guard.Campaign(tmp_path / "stopped", plan(), len)

    async def sleep(_):
        instance.stop("synthetic external stop")

    monkeypatch.setattr(guard, "asyncio", SimpleNamespace(sleep=sleep))
    with pytest.raises(guard.CampaignStopped):
        with instance.activate():
            instance.start_arm("A0", verified_pins=plan().pins, restore={"synthetic": True})
            instance.begin_question("synthetic-0")
            send("embedding")
            async with guard.campaign_llm_call("answer", request("answer")):
                pytest.fail("Stopped pacing reached provider boundary")
    assert instance.state["pending"] is None
    assert instance._pacing is False


def history(tmp_path: Path):
    old = plan().payload()
    for key in (
        "llm_start_interval_seconds",
        "prior_restore_attempts",
        "maximum_cumulative_restore_attempts",
    ):
        old.pop(key)
    old.update(prior_reserved_usd="1.7019050", cumulative_reserved_usd="6.0519050")
    old["arms"] = list(old["arms"])
    old["question_ids"] = list(old["question_ids"])
    artifact = tmp_path / "immutable-artifact"
    artifact.write_text("synthetic")
    pin = {"path": str(artifact), "sha256": launch.digest_file(artifact)}
    old["pins"].update(
        {
            name: dict(pin)
            for name in ("snapshot", "ingest_checkpoint", "ingest_manifest", "restore_script")
        }
    )
    old["pins"].update(
        effective_config={},
        tokenizer_sha256="tokenizer",
        original_allow_connections=True,
        server_identifier="synthetic-server",
        database_oid=123,
        catalogue_sha256="historical",
    )
    failed = dict(
        plan=old,
        status="stopped_reconciliation_required",
        failure_admission="closed",
        restore_attempts=1,
        restore_pending=None,
        restore_receipts=[{}],
        receipts=[{}] * 6,
        arm="A0",
        arm_index=0,
        pending=dict(arm="A0", stage="judge", question_id=old["question_ids"][1]),
    )
    previous, ledger = tmp_path / "previous.json", tmp_path / "failed.json"
    previous.write_text(json.dumps(old))
    ledger.write_text(json.dumps(failed))
    lineage = {
        name: {"path": str(path), "sha256": launch.digest_file(path)}
        for name, path in (("previous_plan", previous), ("failed_ledger", ledger))
    }
    return old, lineage


def test_recovery_rejects_changed_parent_and_old_plan(tmp_path):
    old, lineage = history(tmp_path)
    assert launch.verify_replacement_lineage(lineage) == old
    Path(lineage["failed_ledger"]["path"]).write_text("{}")
    with pytest.raises(guard.CampaignStopped, match="changed"):
        launch.verify_replacement_lineage(lineage)
    with pytest.raises(guard.CampaignStopped, match="Plan has changed"):
        launch.load_plan(Path(lineage["previous_plan"]["path"]))


def test_replacement_has_one_fixed_successor_directory(tmp_path):
    _, lineage = history(tmp_path)
    replacement = guard.CampaignPlan(
        plan().question_ids, plan().input_limits, {"replacement_lineage": lineage}
    )
    expected = tmp_path.with_name(tmp_path.name + "-replacement")
    launch.verify_replacement_destination(replacement, expected)
    with pytest.raises(guard.CampaignStopped, match="fixed successor"):
        launch.verify_replacement_destination(replacement, tmp_path / "another-campaign")


@pytest.mark.asyncio
@pytest.mark.parametrize("open_target", [False, True])
async def test_replacement_prepare_uses_only_readonly_control(tmp_path, monkeypatch, open_target):
    old, lineage = history(tmp_path)
    monkeypatch.setattr(launch, "SNAPSHOT_SHA256", old["pins"]["snapshot"]["sha256"])
    monkeypatch.setattr(
        launch, "load_dev", lambda _: [{"question_id": q} for q in old["question_ids"]]
    )
    monkeypatch.setattr(launch, "environment", lambda: {})
    monkeypatch.setattr(launch, "tokenizer", lambda: (None, "tokenizer"))
    monkeypatch.setattr(launch, "verify_common", lambda *_: {})
    monkeypatch.setattr(launch, "rates", lambda: {})
    monkeypatch.setattr(launch, "container_identity", lambda: {})
    control = SimpleNamespace(close=AsyncMock())
    connect = AsyncMock(return_value=control)
    monkeypatch.setattr(launch.asyncpg, "connect", connect)
    monkeypatch.setattr(launch, "verify_server", AsyncMock())
    verify_sessions = AsyncMock()
    monkeypatch.setattr(launch, "verify_sessions", verify_sessions)
    monkeypatch.setattr(
        launch,
        "target_identity",
        AsyncMock(
            return_value={
                "system_identifier": "synthetic-server",
                "oid": 123,
                "datallowconn": open_target,
            }
        ),
    )
    args = argparse.Namespace(
        output=tmp_path / "replacement.json",
        control_sha="control",
        candidate_sha="candidate",
        **{name: Path(pin["path"]) for name, pin in lineage.items()},
        **{name + "_sha256": pin["sha256"] for name, pin in lineage.items()},
    )
    if open_target:
        with pytest.raises(guard.CampaignStopped, match="admission differs"):
            await launch.prepare_replacement(args)
        assert not args.output.exists()
    else:
        await launch.prepare_replacement(args)
        result = launch.load_plan(args.output).payload()
        assert result["cumulative_reserved_usd"] == "10.4019050"
        assert result["prior_restore_attempts"] == 1
        assert result["maximum_cumulative_restore_attempts"] == 5
        assert result["llm_start_interval_seconds"] == 5
        verify_sessions.assert_awaited_once_with(control, set())
    connect.assert_awaited_once_with(
        launch.CONTROL_DSN,
        timeout=10,
        command_timeout=30,
        server_settings={"default_transaction_read_only": "on"},
    )
    control.close.assert_awaited_once()
