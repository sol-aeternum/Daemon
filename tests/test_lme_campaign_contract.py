"""Legacy persisted contracts, captured once from origin/main f9809293.

fixtures/campaign_contract.json holds payload/identity digests, accounting and
rounding amounts, initial states, pending/receipt/failure artifacts, and the real
APPROVAL/PRIOR/POLICY values, with producing-function/input provenance. Inline
literals freeze PIN_KEYS, ARMS/STAGES/CAPS, guard holds, recovery execution and
restore fields, RULES/RETRIEVAL_MODE, writer key sets and substrate. Synthetic
plan/request/response builders are local; expectations are never recomputed by
the implementation under test. No temporary-path approval enters a hash golden.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager
import copy
import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from orchestrator.eval import campaign_final_verdict as final, campaign_fresh as fresh
from orchestrator.eval import campaign_guard as guard
from orchestrator.eval import campaign_recovery as recovery, campaign_restore as restore
from orchestrator.eval import campaign_retry as retry, campaign_validation as validation
from tests.test_lme_campaign_second_recovery import recovered as recovered

GOLDEN_PATH = Path(__file__).parent / "fixtures/campaign_contract.json"

# Fixed synthetic approval/prior: payload-only, never opened or used as destinations.
APPROVAL = {
    "id": "synthetic-contract",
    "directory": "/synthetic/campaign",
    "new_reserved_usd": "5.50",
    "maximum_allocated_usd": "4.46",
    "retry_headroom_usd": "1.10",
    "local_maximum_restore_attempts": 4,
    "retry_policy": {
        "maximum_attempts": 3,
        "backoff_seconds": [30, 60],
        "maximum_wait_seconds": 120,
        "minimum_start_interval_seconds": 5,
        "retryable_status": 429,
    },
    "candidate_variable": "noted-dates",
    "acceptance_rule": "2026-10-31-official-v2",
    "historical_remap_lineage_sha256": "synthetic-lineage",
}
PRIOR = {
    "plan_path": "/synthetic/prior.json",
    "plan_sha256": "synthetic-prior",
    "prior_reserved_usd": "21.2519050",
    "prior_restore_attempts": 7,
}
PIN_KEYS = {
    "common_files",
    "arm_shas",
    "formatter",
    "candidate_variable",
    "candidate_formatter",
    "tokenizer_sha256",
    "tokenizer_envelope",
    "server_identifier",
    "database_oid",
    "catalogue_sha256",
    "original_allow_connections",
    "snapshot",
    "ingest_checkpoint",
    "ingest_manifest",
    "route_evidence",
    "effective_config",
    "restore_script",
    "historical_remap",
    "fresh_campaign",
}


def golden() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_bytes())


def legacy_plan() -> guard.CampaignPlan:
    # Copied from test_lme_campaign_guard at f9809293; keep hashed inputs local.
    ids = tuple(f"synthetic-{i}" for i in range(50))
    return guard.CampaignPlan(
        ids,
        {qid: {"embedding": 20, "answer": 500, "judge": 500, "official_judge": 500} for qid in ids},
        {"source": "synthetic-only", "snapshot": "not-an-execution-approval"},
    )


def request(stage: str = "embedding") -> dict[str, Any]:
    # Literal transport inputs, independent even of production model/rate constants.
    if stage == "embedding":
        return {
            "route_id": "azure-openrouter-small-1024",
            "model": "openai/text-embedding-3-small",
            "endpoint": "https://openrouter.ai/api/v1",
            "dimensions": 1024,
            "input": ["fictional query"],
            "provider": {
                "only": ["azure"],
                "order": ["azure"],
                "allow_fallbacks": False,
                "data_collection": "deny",
                "zdr": True,
                "require_parameters": True,
            },
        }
    return {
        "model": "openrouter/openai/gpt-4o-2024-08-06",
        "messages": [{"role": "user", "content": "fictional"}],
        "temperature": 0,
        "max_tokens": {"answer": 256, "judge": 256, "official_judge": 10}[stage],
        "seed": 42,
        "num_retries": 0,
        "api_base": "https://openrouter.ai/api/v1",
        "extra_body": {"provider": {"order": ["azure"], "allow_fallbacks": False}},
    }


def response(stage: str = "embedding") -> dict[str, Any]:
    # Copied from test_lme_campaign_guard at f9809293, not a live provider response.
    if stage == "embedding":
        return {
            "model": "text-embedding-3-small",
            "data": [],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }
    return {
        "model": "openai/gpt-4o-2024-08-06",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "yes"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
    }


def write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value))


def pin(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": validation.digest_file(path)}


def synthetic_plan(mode: str = "legacy", *, embedding_tokens: int = 20) -> guard.CampaignPlan:
    base = legacy_plan()
    limits = {qid: dict(stages) for qid, stages in base.input_limits.items()}
    for item in limits.values():
        item["embedding"] = embedding_tokens
    pins: dict[str, Any] = copy.deepcopy(dict(base.pins))
    if mode in {"recovery", "retry", "final"}:
        pins.update(
            recovery_lineage={
                "previous_replacement_plan": "synthetic",
                "stopped_a1_ledger": "synthetic",
            },
            recovery={"evidence_sha256": {}, "inherited_a0": {}, "source_bridge": {}},
        )
    if mode in {"retry", "final"}:
        pins.update(
            retry_recovery_lineage={"previous_plan": "synthetic", "stopped_ledger": "synthetic"},
            retry_policy=copy.deepcopy(APPROVAL["retry_policy"]),
            retry_recovery={"evidence_sha256": {}, "source_bridge": {}, "inherited": {}},
        )
    if mode == "final":
        pins.update(
            final_verdict_recovery_lineage={
                "previous_plan": "synthetic",
                "stopped_ledger": "synthetic",
                "attempt_record": "synthetic",
                "result": "synthetic",
            },
            final_verdict_recovery={"evidence_sha256": {}, "inherited": {}, "source_bridge": {}},
        )
    if mode == "fresh":
        pins = dict.fromkeys(PIN_KEYS, "synthetic")
        pins.update(
            fresh_campaign={"approval": copy.deepcopy(APPROVAL), "prior": copy.deepcopy(PRIOR)},
            arm_shas={"A0": "control", "A1": "control", "A2": "control", "C0": "candidate"},
            candidate_variable="noted-dates",
            candidate_formatter={"id": "synthetic-candidate"},
            historical_remap={"lineage_sha256": "synthetic-lineage"},
        )
    return guard.CampaignPlan(base.question_ids, limits, pins)


@pytest.fixture
def fixed_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fresh, "APPROVAL", copy.deepcopy(APPROVAL))
    monkeypatch.setattr(fresh, "PRIOR", copy.deepcopy(PRIOR))


@pytest.mark.parametrize("mode", ["legacy", "fresh", "retry", "recovery", "final"])
def test_payload_hashes_and_accounting(mode: str, fixed_approval: None) -> None:
    payload = synthetic_plan(mode).payload()
    expected = golden()["payloads"][mode]
    assert guard._hash(payload) == expected["sha256"]
    for key, value in expected["fields"].items():
        assert payload[key] == value
    if mode == "recovery":
        assert payload["execution_arms"] == ("A1", "A2", "C0")
        assert payload["separate_capacity_reserved_usd"] == "1"
        assert (
            payload["prior_restore_attempts"],
            payload["maximum_cumulative_restore_attempts"],
        ) == (3, 6)
    if mode == "retry":
        assert payload["retry_policy"] == golden()["constants"]["policy"]


def test_embedding_fractional_microdollar_rounds_up() -> None:
    # 1 token costs .02 microdollars, rounded up once per embedding allocation.
    assert (
        synthetic_plan(embedding_tokens=1).payload()["allocated_usd"]
        == golden()["rounding_allocated_usd"]
    )


def test_real_scalar_contracts() -> None:
    expected = golden()["constants"]
    assert fresh.APPROVAL == expected["approval"]
    assert fresh.PRIOR == expected["prior"]
    assert fresh.PIN_KEYS == PIN_KEYS
    assert guard.ARMS == ("A0", "A1", "A2", "C0")
    assert guard.STAGES == ("embedding", "answer", "judge", "official_judge")
    assert guard.CAPS == {"embedding": 0, "answer": 256, "judge": 256, "official_judge": 10}
    assert (str(guard.NEW_HOLD), str(guard.PRIOR_HOLD), str(guard.CAMPAIGN_CEILING)) == (
        "4.35",
        "6.0519050",
        "10.4019050",
    )
    # 14.7519050 is recovery cumulative accounting, NOT a payload ceiling field.
    assert retry.POLICY == expected["policy"]
    assert validation.RULES == ("2026-10-28-official-v1", "2026-10-31-official-v2")
    assert (
        validation.RETRIEVAL_MODE == "embedding:openrouter:azure:openai/text-embedding-3-small:1024"
    )


@pytest.mark.parametrize("stage", ["embedding", "answer", "judge", "official_judge"])
def test_four_key_identity_hashes(stage: str) -> None:
    # Embedding is a receipt-entry identity; the other three are retry identities.
    identity = {
        "arm": "A0",
        "question_id": "synthetic-0",
        "stage": stage,
        "request_sha256": "a" * 64,
    }
    assert guard._hash(identity) == golden()["identity_hashes"][stage]


@contextmanager
def activated(tmp_path: Path, plan: guard.CampaignPlan | None = None) -> Iterator[guard.Campaign]:
    campaign = guard.Campaign(tmp_path / "campaign", plan or synthetic_plan(), len)
    with pytest.raises(guard.CampaignStopped, match="Incomplete campaign"):
        with campaign.activate():
            yield campaign


@pytest.mark.parametrize("mode", ["legacy", "fresh"])
def test_initial_campaign_json_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, fixed_approval: None
) -> None:
    # Isolate only external approval/claim checks; initialize and durable writer are real.
    monkeypatch.setattr(fresh, "verify", lambda *_: {})
    monkeypatch.setattr(fresh, "claim", lambda *_: None)
    with activated(tmp_path, synthetic_plan(mode)) as campaign:
        assert json.loads(campaign.state_path.read_bytes()) == golden()["initial_states"][mode]


def begin(campaign: guard.Campaign) -> None:
    campaign.start_arm("A0", verified_pins=campaign.plan["pins"], restore={"synthetic": True})
    campaign.begin_question("synthetic-0")


def test_before_send_pending_and_embedding_artifact_literals(tmp_path: Path) -> None:
    with activated(tmp_path) as campaign:
        begin(campaign)
        prepared = request()
        campaign.before_send("embedding", prepared)
        assert (
            json.loads(campaign.state_path.read_bytes())["pending"] == golden()["embedding_pending"]
        )
        campaign.receive("embedding", prepared, response())
        assert (
            json.loads((campaign.directory / "receipt-0000.json").read_bytes())
            == golden()["embedding_artifact"]
        )


def test_before_send_is_the_only_four_key_pending_writer() -> None:
    # Source-backed complement to the actual durable pending golden above.
    # Limitation: this scan detects only ast.Assign writers with dict literals.
    tree = ast.parse(Path(guard.__file__).read_text())
    writers = []
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef):
            continue
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
                continue
            if any(ast.unparse(target) == "self.state['pending']" for target in node.targets):
                writers.append(
                    (function.name, {ast.literal_eval(key) for key in node.value.keys if key})
                )
    assert writers == [("before_send", {"arm", "question_id", "stage", "request_sha256"})]


def test_legacy_failure_artifact_literal(tmp_path: Path) -> None:
    with activated(tmp_path) as campaign:
        begin(campaign)
        campaign.before_send("embedding", request())
        campaign.failure_details("embedding", request(), TimeoutError("fictional private detail"))
        assert (
            json.loads((campaign.directory / "failure-0000.json").read_bytes())
            == golden()["failure_artifact"]
        )


@pytest.mark.parametrize("matching", [True, False])
def test_historical_pending_at_195(tmp_path: Path, matching: bool) -> None:
    campaign = guard.Campaign(tmp_path / "campaign", synthetic_plan(), len)
    with pytest.raises(
        guard.CampaignStopped,
        match="Incomplete campaign" if matching else "historical pending hash",
    ):
        with campaign.activate():
            begin(campaign)
            campaign.state.update(receipts=[{"synthetic": i} for i in range(195)], stage_index=3)
            historical = copy.deepcopy(golden()["historical_pending"])
            if not matching:
                historical["request_sha256"] = "b" * 64
            campaign.state["historical_pending"] = historical
            campaign.before_send("official_judge", request("official_judge"))
            assert campaign.state["pending"] == golden()["historical_pending"]


def receipt_directory(directory: Path, count: int, *, arm_offset: int = 0) -> dict[str, Any]:
    """Test-local literal 4/50/200 oracle, deliberately not campaign_layout."""
    entries = []
    for number in range(count):
        arm, within = divmod(number, 200)
        question, slot = divmod(within, 4)
        identity = {
            "arm": ("A0", "A1", "A2", "C0")[arm + arm_offset],
            "question_id": f"synthetic-{question}",
            "stage": ("embedding", "answer", "judge", "official_judge")[slot],
            "request_sha256": "a" * 64,
        }
        artifact = f"receipt-{number:04d}.json"
        write(directory / artifact, {"pending": identity, "response": {}})
        entries.append({**identity, "artifact": artifact})
    return {
        "plan": {"question_ids": [f"synthetic-{i}" for i in range(50)]},
        "receipts": entries,
        "pending": None,
    }


def ledger(count: int) -> dict[str, Any]:
    entries = []
    attempts = []
    for index in range(count):
        identity = {
            "arm": "A0",
            "question_id": f"synthetic-{index // 4}",
            "stage": ("embedding", "answer", "judge", "official_judge")[index % 4],
            "request_sha256": "a" * 64,
        }
        artifact = f"receipt-{index:04d}.json"
        entries.append({**identity, "artifact": artifact})
        if index % 4:
            attempts.append(
                {
                    "identity": identity,
                    "receipt_index": index,
                    "ordinal": 1,
                    "status": "completed",
                    "reserved_usd": "0",
                    "receipt": artifact,
                }
            )
    return {"receipts": entries, "attempts": attempts, "retry_reservations": []}


@pytest.mark.parametrize("index", [1, 2, 3, 4, 199])
def test_fresh_retry_index_boundaries(tmp_path: Path, fixed_approval: None, index: int) -> None:
    state = ledger(index + 1)
    payload = synthetic_plan("fresh").payload()
    if index == 4:
        # Slot 4 is the next embedding receipt; it can never be a retry identity.
        fresh.validate_fresh_attempt_ledger(state, payload, tmp_path, settled=True)
        state["attempts"].append(
            {
                "identity": {k: v for k, v in state["receipts"][4].items() if k != "artifact"},
                "receipt_index": 4,
            }
        )
        with pytest.raises(guard.CampaignStopped, match="retry attempt identity"):
            fresh.validate_fresh_attempt_ledger(state, payload, tmp_path)
        return
    fresh.validate_fresh_attempt_ledger(state, payload, tmp_path, settled=True)
    item = state["attempts"][-1]
    assert item["receipt_index"] == index
    for delta in (-1, 1):
        item["receipt_index"] = index + delta
        with pytest.raises(guard.CampaignStopped, match="receipt index/order"):
            fresh.validate_fresh_attempt_ledger(state, payload, tmp_path)
    item["receipt_index"] = index
    state["attempts"].pop()
    with pytest.raises(guard.CampaignStopped, match="omits/duplicates completed"):
        fresh.validate_fresh_attempt_ledger(state, payload, tmp_path)


def test_embedding_is_not_a_fresh_retry_identity(tmp_path: Path, fixed_approval: None) -> None:
    state = ledger(1)
    state["attempts"] = [
        {
            "identity": {
                "arm": "A0",
                "question_id": "synthetic-0",
                "stage": "embedding",
                "request_sha256": "a" * 64,
            },
            "receipt_index": 0,
            "ordinal": 1,
            "status": "dispatch_pending",
            "reserved_usd": "0",
        }
    ]
    with pytest.raises(guard.CampaignStopped, match="^Fresh retry attempt identity differs$"):
        fresh.validate_fresh_attempt_ledger(state, synthetic_plan("fresh").payload(), tmp_path)


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_fresh_identity_exact_key_set(tmp_path: Path, fixed_approval: None, change: str) -> None:
    state = ledger(2)
    if change == "extra":
        state["attempts"][0]["identity"]["sample"] = 1
    else:
        del state["attempts"][0]["identity"]["request_sha256"]
    with pytest.raises(guard.CampaignStopped, match="retry attempt identity"):
        fresh.validate_fresh_attempt_ledger(state, synthetic_plan("fresh").payload(), tmp_path)


def test_fresh_completed_receipts_cannot_be_duplicated(
    tmp_path: Path, fixed_approval: None
) -> None:
    state = ledger(2)
    state["attempts"].append(copy.deepcopy(state["attempts"][0]))
    with pytest.raises(guard.CampaignStopped, match="ordinal/reset"):
        fresh.validate_fresh_attempt_ledger(state, synthetic_plan("fresh").payload(), tmp_path)


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_recovery_pending_exact_key_set(recovered: Any, change: str) -> None:
    # Existing synthetic lineage fixture; no golden depends on its temporary paths.
    path = recovered.ledger
    state = json.loads(path.read_bytes())
    if change == "extra":
        state["pending"]["sample"] = 1
    else:
        del state["pending"]["request_sha256"]
    write(path, state)
    lineage = copy.deepcopy(recovered.lineage)
    lineage["stopped_a1_ledger"] = pin(path)
    with pytest.raises(guard.CampaignStopped, match="Stopped A1 state differs"):
        recovery.inspect_lineage(lineage)


@pytest.mark.parametrize(
    "offset,index", [(o, i) for o in (0, 1) for i in (0, 3, 4, 199, 200)] + [(0, 600)]
)
def test_manifest_index_boundaries(tmp_path: Path, offset: int, index: int) -> None:
    state = receipt_directory(tmp_path, index + 1, arm_offset=offset)
    assert len(recovery._receipt_manifest(tmp_path, state, arm_offset=offset)) == index + 1
    entry = state["receipts"][index]
    original = copy.deepcopy(entry)
    for delta in (-1, 1):
        # Forge both file and state consistently; serial slot check must still reject.
        slot = (index + delta) % 4
        entry["stage"] = ("embedding", "answer", "judge", "official_judge")[slot]
        write(
            tmp_path / entry["artifact"],
            {"pending": {k: v for k, v in entry.items() if k != "artifact"}},
        )
        with pytest.raises(guard.CampaignStopped, match="frozen serial execution"):
            recovery._receipt_manifest(tmp_path, state, arm_offset=offset)
    entry.update(original)


@pytest.mark.parametrize("sides", ["file", "state", "both"])
def test_manifest_is_equality_not_exact_keys(tmp_path: Path, sides: str) -> None:
    state = receipt_directory(tmp_path, 1)
    path = tmp_path / "receipt-0000.json"
    data = json.loads(path.read_bytes())
    if sides in {"file", "both"}:
        data["pending"]["sample"] = 1
        write(path, data)
    if sides in {"state", "both"}:
        state["receipts"][0]["sample"] = 1
    if sides == "both":
        # Current behaviour, NOT desired v3 validation: no exact key-set check here.
        assert len(recovery._receipt_manifest(tmp_path, state)) == 1
    else:
        with pytest.raises(guard.CampaignStopped, match="frozen serial execution"):
            recovery._receipt_manifest(tmp_path, state)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final_mode,count,accepted",
    [(False, 195, True), (False, 200, False), (True, 200, True), (True, 195, False)],
)
async def test_attach_count_gate_without_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, final_mode: bool, count: int, accepted: bool
) -> None:
    class ReachedContainer(Exception):
        pass

    def sentinel() -> None:
        raise ReachedContainer

    monkeypatch.setattr(retry, "enabled", lambda _: True)
    monkeypatch.setattr(retry, "validate_attribution", lambda *_, **__: None)
    monkeypatch.setattr(final, "before_attach", lambda _: None)
    monkeypatch.setattr(restore, "container_identity", sentinel)
    campaign = guard.Campaign(tmp_path / "campaign", synthetic_plan(), len)
    monkeypatch.setattr(campaign, "_check", lambda: None)
    monkeypatch.setattr(campaign, "_recovery_check", lambda: None)
    if final_mode:
        campaign.plan["pins"]["final_verdict_recovery_lineage"] = {}
    campaign.state = {
        "arm": "A1",
        "arm_index": 1,
        "restore_attempts": 1,
        "restores": [{}],
        "pending": None,
        "receipts": [{}] * count,
    }
    with pytest.raises(ReachedContainer if accepted else guard.CampaignStopped):
        async with restore.attached_pool(campaign):
            pytest.fail("Must not reach database")


@pytest.mark.parametrize("pids", [list(range(10)), list(range(9)), list(range(11)), [0] * 10])
@pytest.mark.parametrize("kind", ["fresh", "retry_restore", "retry_attach"])
def test_restore_and_attach_ten_distinct_pids(pids: list[int], kind: str) -> None:
    receipt: dict[str, Any] = {
        "arm": "A0" if kind == "fresh" else "A1",
        "fingerprint": {"sha256": validation.STATE_SHA256},
        "owned_pids": list(range(10)),
        "script_sha256": "synthetic-script",
    }
    attributed = {"source_ledger": "synthetic-ledger", "receipt": receipt}
    inherited = {"source_ledger": "synthetic-ledger", "restore": receipt}
    plan: dict[str, Any] = {
        "pins": {
            "restore_script": {"sha256": "synthetic-script"},
            "retry_recovery": {"inherited": inherited},
            "database_oid": 7,
            "server_identifier": "synthetic-server",
        }
    }
    state: dict[str, Any] = {
        "restores": [receipt],
        "restore_receipts": [receipt],
        "restore_attempts": 1,
        "imported_prefix": inherited,
        "imported_restore": attributed,
        "attach_receipt": {
            "fingerprint": {"sha256": validation.STATE_SHA256},
            "owned_pids": list(range(10)),
            "inherited_restore": attributed,
            "identity": {"oid": 7, "system_identifier": "synthetic-server"},
        },
    }
    if kind == "retry_attach":
        state["attach_receipt"]["owned_pids"] = pids
    else:
        receipt["owned_pids"] = pids

    def check() -> None:
        if kind == "fresh":
            fresh._restores(state, plan, 1)
        else:
            retry.validate_attribution(state, plan)

    if pids == list(range(10)):
        check()
    else:
        with pytest.raises(
            guard.CampaignStopped,
            match="verified .*receipt differs|verified attach attribution differs",
        ):
            check()


def test_real_receipt_writer_key_sets_and_substrate() -> None:
    # AST-only inspection freezes real writers without executing any DB/restore path.
    # Limitation: writer scans detect only ast.Assign (dict literals for base receipts),
    # not update(), dynamic construction, or other forms of mutation.
    tree = ast.parse(Path(restore.__file__).read_text())
    writers = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "receipt":
                writers.append(
                    {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
                )
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == "attach_receipt"
            ):
                writers.append(
                    {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
                )
    assert sorted(map(sorted, writers)) == sorted(
        map(
            sorted,
            [
                {"arm", "fingerprint", "owned_pids", "script_sha256"},
                {"fingerprint", "owned_pids", "inherited_restore", "identity"},
            ],
        )
    )
    final_attach_fields = [
        ast.unparse(node.targets[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and ast.unparse(node.targets[0]).startswith("state['attach_receipt'][")
    ]
    assert final_attach_fields == ["state['attach_receipt']['final_verdict_recovery_lineage']"]
    tree = ast.parse(Path(validation.__file__).read_text())
    substrate_checks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and ast.unparse(node.left) == "manifest.get('substrate')"
    ]
    assert len(substrate_checks) == 1
    assert ast.literal_eval(substrate_checks[0].comparators[0]) == "fact"
