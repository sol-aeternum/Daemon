"""Offline fictional judge screen checks; HTTP is always mocked, no credentials."""

from __future__ import annotations

from argparse import Namespace
import copy
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from orchestrator import compute_runtime as runtime, model_routing
from orchestrator.memory import equivalence
from scripts import qualify_memory_judge as screen


def response_for(batch, *, fail_negative=False, fail_positive=False):
    rows = screen.facts(batch)[1]
    verdicts = []
    for row, candidate in zip(rows, batch["candidates"], strict=True):
        positive = candidate["equivalent"]
        equivalent = (positive and not fail_positive) or (not positive and fail_negative)
        verdicts.append(
            {"candidate_id": str(row["id"]), "verdict": "equivalent" if equivalent else "distinct"}
        )
    return {
        "id": "gen-fictional",
        "model": "openai/gpt-6-luna",
        "provider": "Azure",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 100,
            "total_tokens": 200,
            "completion_tokens_details": {"reasoning_tokens": 10},
            "cost": 0.0001,
        },
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": json.dumps({"verdicts": verdicts})},
            }
        ],
    }


class FictionalServer:
    def __init__(self, batches, path, mode="pass"):
        self.batches, self.path, self.mode = batches, path, mode
        self.posts, self.gets = [], []

    def __call__(self, request):
        if request.method == "GET":
            assert "authorization" not in request.headers
            self.gets.append(str(request.url))
            if str(request.url) == screen.attestation.ZDR_LISTING_URL:
                data = {"data": [{"model_id": "openai/gpt-6-luna", "tag": "azure/eu"}]}
            else:
                data = {
                    "data": [
                        {
                            "slug": "azure",
                            "dataPolicy": {"training": False, "retainsPrompts": False},
                        }
                    ]
                }
            if self.mode == "attestation":
                data = {"data": []}
            return httpx.Response(200, json=data)
        assert request.method == "POST"
        assert str(request.url) == f"{screen.BASE}/chat/completions"
        assert request.headers["authorization"] == "Bearer fictional-test-secret"
        # Reservation and exact fictional request already exist on disk at send.
        ledger = json.loads(self.path.read_text())
        attempt = ledger["attempts"][-1]
        assert attempt["outcome"] == "uncertain"
        assert attempt["request"] == json.loads(request.content)
        assert "fictional-test-secret" not in self.path.read_text()
        body = attempt["request"]
        assert body["provider"] == screen.configured_route()[2]
        assert body["response_format"] == {"type": "json_object"}
        assert body["max_tokens"] == 2000 and body["stream"] is False
        assert body["reasoning_effort"] == "medium"
        assert body["usage"] == {"include": True}
        assert set(body) == {
            "model",
            "provider",
            "messages",
            "max_tokens",
            "response_format",
            "reasoning_effort",
            "stream",
            "usage",
        }
        self.posts.append(request)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("fictional-test-secret", request=request)
        if self.mode in {"403", "302"}:
            return httpx.Response(int(self.mode), headers={"Location": "https://example.org"})
        batch = next(b for b in self.batches if b["id"] == attempt["batch"])
        data = response_for(
            batch, fail_negative=self.mode == "negative", fail_positive=self.mode == "positive"
        )
        if self.mode == "identity":
            data["provider"] = "OpenAI"
        if self.mode == "invalid":
            data["choices"][0]["message"]["content"] = "{invalid"
        if self.mode == "refusal":
            data["choices"][0]["message"]["refusal"] = "refused"
        if self.mode == "length":
            data["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=data)


@pytest.mark.asyncio
async def test_frozen_fictional_fixture_and_conservative_preflight(monkeypatch):
    import orchestrator.config

    monkeypatch.setattr(orchestrator.config, "get_settings", lambda: pytest.fail("credential read"))
    prepared = await screen.prepare()
    batches, requests, bounds, frozen = prepared[1:]
    assert len(batches) == 12
    assert sum(c["equivalent"] for b in batches for c in b["candidates"]) == 12
    assert screen.NEGATIVES <= {c["category"] for b in batches for c in b["candidates"]}
    assert all(
        any(c["equivalent"] for c in b["candidates"])
        and any(not c["equivalent"] for c in b["candidates"])
        for b in batches
    )
    assert sum(bounds.values()) < screen.MAX_INPUT
    assert 12 * screen.OUTPUT <= screen.MAX_OUTPUT
    assert sum((screen.cost(b) for b in bounds.values()), Decimal(0)) < screen.MAX_USD
    assert frozen["prompt_sha256"] == screen.digest(equivalence._PROMPT)
    for params in requests.values():
        assert params["messages"][0]["content"] == equivalence._PROMPT
        assert runtime._request_bound(params).bound > len(json.dumps(params["messages"]).encode())


@pytest.mark.parametrize("bound", [True, 0, -1, "2000"])
def test_invalid_reservations(bound):
    with pytest.raises(ValueError):
        screen.reserve({"attempts": []}, "one", bound)


def test_global_request_input_output_and_cost_caps(monkeypatch):
    ledger = {"attempts": []}
    for index in range(24):
        screen.reserve(ledger, str(index), 1)
    assert sum(a["output"] for a in ledger["attempts"]) == 48_000
    with pytest.raises(ValueError, match="cap"):
        screen.reserve(ledger, "25", 1)
    ledger = {"attempts": []}
    screen.reserve(ledger, "all-input", 100_000)
    with pytest.raises(ValueError, match="cap"):
        screen.reserve(ledger, "extra-input", 1)
    monkeypatch.setattr(screen, "MAX_USD", Decimal(".001"))
    with pytest.raises(ValueError, match="cap"):
        screen.reserve({"attempts": []}, "cost", 100)


def test_output_cap_is_independent_and_utf8_bound_is_conservative(monkeypatch):
    monkeypatch.setattr(screen, "MAX_OUTPUT", 1999)
    with pytest.raises(ValueError, match="cap"):
        screen.reserve({"attempts": []}, "output", 1)
    params = {
        "messages": [{"role": "user", "content": "🐢" * 80}],
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    assert runtime._request_bound(params).bound >= len(("🐢" * 80).encode()) + 512


def test_uncertain_reservation_never_released_or_repeated(tmp_path):
    ledger = {"frozen": {"test": True}, "attempts": []}
    attempt = screen.reserve(ledger, "uncertain", 2000)
    path = tmp_path / "judge.json"
    screen.write_json(path, ledger)
    for outcome in ("uncertain", "transport_or_identity_failure"):
        attempt["outcome"] = outcome
        screen.write_json(path, ledger)
        with pytest.raises(ValueError, match="resume refused"):
            screen.open_ledger(path, {"test": True})
        with pytest.raises(ValueError, match="already attempted"):
            screen.reserve(ledger, "uncertain", 2000)
        assert ledger["attempts"][0]["input"] == 2000


def test_ledger_freeze_corruption_and_exclusive_lock(tmp_path):
    path = tmp_path / "judge.json"
    with screen.exclusive_ledger(path):
        with pytest.raises(BlockingIOError), screen.exclusive_ledger(path):
            pytest.fail("Lock admitted a concurrent runner")
        ledger = screen.open_ledger(path, {"fixture": "a", "prompt": "b", "policy": "c"})
        attempt = screen.reserve(ledger, "one", 1000)
        attempt["outcome"] = "valid"
        screen.write_json(path, ledger)
    for field in ("fixture", "prompt", "policy"):
        frozen = dict(ledger["frozen"], **{field: "changed"})
        with pytest.raises(ValueError, match="changed"):
            screen.open_ledger(path, frozen)
    for field, value in (("input", -1), ("usd", "NaN"), ("output", 1)):
        broken = copy.deepcopy(ledger)
        broken["attempts"][0][field] = value
        screen.write_json(path, broken)
        with pytest.raises((ValueError, ArithmeticError)):
            screen.open_ledger(path, ledger["frozen"])


@pytest.mark.parametrize("model", ["openai/gpt-6-luna"])
@pytest.mark.parametrize("provider", ["Azure", "azure/eu"])
def test_exact_normal_receipt_identity(model, provider):
    receipt = response_for(screen.fixtures()[0])
    receipt.update(model=model, provider=provider)
    screen.validate_identity(receipt, 2000)


@pytest.mark.parametrize(
    "updates",
    [
        {"model": "openai/gpt-6-luna-pro"},
        {"model": "gpt-6-luna"},
        {"model": "gpt-6-luna-unknown-date"},
        {"provider": "Azure (US)"},
        {"provider": None},
        {"usage": {}},
        {"usage": {"prompt_tokens": True, "completion_tokens": 100, "total_tokens": 101}},
        {"usage": {"prompt_tokens": 2001, "completion_tokens": 100, "total_tokens": 2101}},
        {"usage": {"prompt_tokens": 1, "completion_tokens": 2001, "total_tokens": 2002}},
        {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 4}},
        {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "cost": 1}},
        {
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 2,
                "total_tokens": 3,
                "completion_tokens_details": {"reasoning_tokens": 3},
            }
        },
    ],
)
def test_receipt_identity_and_usage_fail_closed(updates):
    with pytest.raises(ValueError):
        screen.validate_identity(dict(response_for(screen.fixtures()[0]), **updates), 2000)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["pass", "negative", "positive", "invalid", "refusal", "length"])
async def test_full_screen_production_path_and_quality_criteria(tmp_path, mode):
    prepared = await screen.prepare()
    path = tmp_path / "judge.json"
    ledger = screen.open_ledger(path, prepared[4])
    server = FictionalServer(prepared[1], path, mode)
    original = equivalence.guarded_completion
    prior_scope = runtime._scope.get()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(server), follow_redirects=False
    ) as client:
        await screen.screen(client, "fictional-test-secret", path, ledger, prepared)
    assert len(server.posts) == 12  # Quality failure does NOT stop the fixed screen.
    assert len(server.gets) == 24
    assert ledger["passed"] == (mode == "pass")
    assert len(ledger["attempts"]) == 12
    assert equivalence.guarded_completion is original and runtime._scope.get() is prior_scope
    for attempt in ledger["attempts"]:
        assert attempt["outcome"] == (
            "valid" if mode in {"pass", "negative", "positive"} else "invalid_response"
        )
    # Resume completed fixed screen sends nothing, retaining all reservations.
    resumed = screen.open_ledger(path, prepared[4])
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        await screen.screen(client, "fictional-test-secret", path, resumed, prepared)
    assert len(server.posts) == 12


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "403", "302", "identity"])
async def test_transport_identity_stop_no_retry_or_redirect(tmp_path, mode):
    prepared = await screen.prepare()
    path = tmp_path / "judge.json"
    ledger = screen.open_ledger(path, prepared[4])
    server = FictionalServer(prepared[1], path, mode)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(server), follow_redirects=False
    ) as client:
        with pytest.raises(ValueError, match="full reservation retained"):
            await screen.screen(client, "fictional-test-secret", path, ledger, prepared)
    assert len(server.posts) == 1 and len(ledger["attempts"]) == 1
    assert ledger["attempts"][0]["output"] == 2000
    assert "fictional-test-secret" not in path.read_text()
    with pytest.raises(ValueError, match="resume refused"):
        screen.open_ledger(path, prepared[4])


@pytest.mark.asyncio
async def test_current_attestation_required_without_production_db(tmp_path):
    prepared = await screen.prepare()
    path = tmp_path / "judge.json"
    ledger = screen.open_ledger(path, prepared[4])
    server = FictionalServer(prepared[1], path, "attestation")
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(ValueError, match="attestation failed"):
            await screen.screen(client, "fictional-test-secret", path, ledger, prepared)
    assert server.posts == [] and ledger["attempts"] == []


@pytest.mark.asyncio
async def test_default_dry_run_and_explicit_approval_never_read_credentials(
    tmp_path, monkeypatch, capsys
):
    import orchestrator.config

    monkeypatch.setattr(
        orchestrator.config, "Settings", lambda **kw: pytest.fail("credential read")
    )
    args = Namespace(
        execute=False, approved=False, credential_env_file=None, ledger=tmp_path / "judge.json"
    )
    assert await screen.run(args)
    assert not args.ledger.exists()
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    args.execute = True
    with pytest.raises(ValueError, match="requires --approved"):
        await screen.run(args)


@pytest.mark.asyncio
async def test_execution_settings_selection_and_no_hidden_transport_retry(
    tmp_path, monkeypatch, capsys
):
    import orchestrator.config

    path = tmp_path / "judge.json"
    prepared = await screen.prepare()
    server = FictionalServer(prepared[1], path)
    options, transports = [], []

    def fake_settings(**kw):
        options.append(kw)
        return SimpleNamespace(
            daemon_inference_policy=str(screen.POLICY),
            daemon_model_routing=None,
            openrouter_api_key="fictional-test-secret",
        )

    def fake_transport(**kw):
        transports.append(kw)
        return httpx.MockTransport(server)

    monkeypatch.setattr(orchestrator.config, "Settings", fake_settings)
    monkeypatch.setattr(screen.httpx, "AsyncHTTPTransport", fake_transport)
    args = Namespace(execute=True, approved=True, credential_env_file=None, ledger=path)
    assert await screen.run(args)
    assert options == [{"_env_file": None}] and transports == [{"retries": 0}]
    assert "fictional-test-secret" not in capsys.readouterr().out
    # Selection errors are detected before a credential is even accessed.
    for policy, routing in ((None, None), (str(screen.POLICY), "other-routing.json")):
        monkeypatch.setattr(
            orchestrator.config,
            "Settings",
            lambda **kw: SimpleNamespace(
                daemon_inference_policy=policy, daemon_model_routing=routing
            ),
        )
        with pytest.raises(ValueError, match="Settings do not select"):
            await screen.run(args)
    assert len(server.posts) == 12


@pytest.mark.asyncio
async def test_reservation_fsync_failure_prevents_dispatch(tmp_path, monkeypatch):
    prepared = await screen.prepare()
    path = tmp_path / "judge.json"
    ledger = screen.open_ledger(path, prepared[4])
    server = FictionalServer(prepared[1], path)

    def failed_write(*args):
        raise OSError("fictional fsync failure")

    monkeypatch.setattr(screen, "write_json", failed_write)
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(OSError, match="fsync failure"):
            await screen.screen(client, "fictional-test-secret", path, ledger, prepared)
    assert server.posts == []


def test_configured_profile_cannot_qualify_another_candidate_or_preset():
    routing, route, provider, preset = screen.configured_route()
    raw = json.loads(screen.ROUTING.read_text())
    background = next(p for p in raw["profiles"] if p["profile"] == "background")
    background["groups"][0]["models"].append("openrouter/z-ai/glm-5.3-flash")
    other = model_routing.parse_model_routing(raw)
    with patch.object(model_routing, "load_model_routing", return_value=other):
        with pytest.raises(ValueError, match="exclusively"):
            screen.configured_route()
    assert route.route_id == screen.ROUTE and preset == {"reasoning_effort": "medium"}


def test_sanitized_receipt_has_no_headers_keys_or_secret():
    data = {
        "content": "fictional-test-secret",
        "extra_headers": {"Authorization": "bad"},
        "api_key": "bad",
        "usage": {"total_tokens": 1},
        "choices": [{"content": "fictional"}],
    }
    sanitized = screen.sanitize(data, "fictional-test-secret")
    assert sanitized == {
        "content": "[redacted]",
        "usage": {"total_tokens": 1},
        "choices": [{"content": "fictional"}],
    }


def test_direct_body_matches_installed_production_sdk_transform():
    """Pin the preset's wire spelling to the installed production adapter."""
    from litellm.llms.openrouter.chat.transformation import OpenrouterConfig

    _, _, provider, preset = screen.configured_route()
    params = {
        "messages": [{"role": "user", "content": "Fictional parity check"}],
        "max_tokens": screen.OUTPUT,
        "response_format": {"type": "json_object"},
    }
    config = OpenrouterConfig()
    optional = config.map_openai_params(
        {"max_tokens": screen.OUTPUT, "response_format": {"type": "json_object"}, **preset},
        {},
        "openai/gpt-6-luna",
        False,
    )
    optional["extra_body"] = {"provider": provider}
    wire = config.transform_request("openai/gpt-6-luna", params["messages"], optional, {}, {})
    wire["stream"] = False
    assert screen.request_body(params, {"provider": provider, "preset": preset}) == wire


def test_positive_controls_do_not_leak_a_constant_position():
    positions = {
        next(i for i, c in enumerate(batch["candidates"]) if c["equivalent"])
        for batch in screen.fixtures()
    }
    assert len(positions) >= 3


async def fictional_parent(tmp_path):
    """Synthetic historical evidence only; never open the operator's ledger."""
    prepared = await screen.prepare()
    parent = {"frozen": copy.deepcopy(prepared[4]), "attempts": [], "passed": False}
    # Follow-up accepts the historical runner's different hash, and ONLY that
    # difference. The separate source-byte hash pins this whole evidence file.
    parent["frozen"]["hashes"]["scripts/qualify_memory_judge.py"] = "a" * 64
    for batch in prepared[1]:
        name = batch["id"]
        attempt = screen.reserve(parent, name, prepared[3][name])
        attempt.update(
            outcome="valid",
            request=screen.request_body(prepared[2][name], prepared[4]),
            receipt=response_for(batch, fail_positive=name == "injection-json"),
        )
    path = tmp_path / "original.json"
    screen.write_json(path, parent)
    return path, hashlib.sha256(path.read_bytes()).hexdigest(), parent


@pytest.mark.asyncio
async def test_followup_fixture_is_fresh_and_keeps_original_prompt_and_criteria():
    original = await screen.prepare()
    followup = await screen.prepare(followup=True)
    assert hashlib.sha256(screen.FIXTURE.read_bytes()).hexdigest() == (
        "3129ce1886ea7e66002fdac2a45afa39cde21c6348389caf007e67c430f831d5"
    )
    batches, requests, bounds, frozen = followup[1:]
    assert len(batches) == 12
    assert sum(c["equivalent"] for b in batches for c in b["candidates"]) == 10
    assert sum(not any(c["equivalent"] for c in b["candidates"]) for b in batches) == 2
    assert screen.NEGATIVES <= {c["category"] for b in batches for c in b["candidates"]}
    old_texts = {b["incoming"] for b in original[1]} | {
        c["content"] for b in original[1] for c in b["candidates"]
    }
    new_texts = {b["incoming"] for b in batches} | {
        c["content"] for b in batches for c in b["candidates"]
    }
    assert old_texts.isdisjoint(new_texts) and set(original[2]).isdisjoint(requests)
    assert all(p["messages"][0]["content"] == equivalence._PROMPT for p in requests.values())
    for key in ("criteria", "limits", "route", "model", "provider", "preset", "prompt_sha256"):
        assert frozen[key] == original[4][key]
    assert sum(bounds.values()) <= 67_920


@pytest.mark.asyncio
async def test_followup_immutable_parent_cumulative_accounting_and_resume(tmp_path):
    parent_path, parent_hash, parent = await fictional_parent(tmp_path)
    original_bytes = parent_path.read_bytes()
    path = screen.followup_path(parent_path)
    with screen.exclusive_followup(parent_path):
        prepared = await screen.prepare_followup(parent_path, parent_hash)
        ledger = screen.open_ledger(path, prepared[4])
        server = FictionalServer(prepared[1], path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            await screen.screen(client, "fictional-test-secret", path, ledger, prepared)
    assert ledger["passed"] is True and parent["passed"] is False
    assert parent_path.read_bytes() == original_bytes
    link = ledger["frozen"]["parent"]
    assert link["path"] == str(parent_path.resolve()) and link["sha256"] == parent_hash
    assert link["historical_frozen_sha256"] == screen.digest(parent["frozen"])
    cumulative = link["reservations"] + ledger["attempts"]
    assert len(cumulative) == 24
    assert sum(a["input"] for a in cumulative) == 32_080 + sum(prepared[3].values())
    assert sum(a["output"] for a in cumulative) == 48_000
    assert sum((Decimal(a["usd"]) for a in cumulative), Decimal(0)) < screen.MAX_USD
    with pytest.raises(ValueError, match="cap"):
        screen.reserve(ledger, "extra", 1)
    # Replaying a complete successor neither retries nor drops parent reservations.
    resumed_prepared = await screen.prepare_followup(parent_path, parent_hash)
    resumed = screen.open_ledger(path, resumed_prepared[4])
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        await screen.screen(client, "fictional-test-secret", path, resumed, resumed_prepared)
    assert len(server.posts) == 12 and len(server.gets) == 24
    assert parent_path.read_bytes() == original_bytes
    assert resumed["frozen"]["parent"] == link


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["requests", "input", "output", "usd"])
async def test_followup_caps_include_all_original_reservations(tmp_path, monkeypatch, field):
    parent_path, parent_hash, _ = await fictional_parent(tmp_path)
    prepared = await screen.prepare_followup(parent_path, parent_hash)
    path = screen.followup_path(parent_path)
    ledger = screen.open_ledger(path, prepared[4])
    bound = prepared[3][prepared[1][0]["id"]]
    existing = copy.deepcopy(ledger)
    screen.reserve(existing, "existing-followup", bound)["outcome"] = "valid"
    screen.write_json(path, existing)
    caps = {
        "requests": ("MAX_REQUESTS", 12),
        "input": ("MAX_INPUT", 32_080 + bound - 1),
        "output": ("MAX_OUTPUT", 24_000 + screen.OUTPUT - 1),
        "usd": ("MAX_USD", Decimal(".02954336") + screen.cost(bound) - Decimal(".00000001")),
    }
    attribute, cap = caps[field]
    monkeypatch.setattr(screen, attribute, cap)
    with pytest.raises(ValueError, match="Cumulative approval cap"):
        screen.reserve(ledger, "first-followup", bound)
    with pytest.raises(ValueError, match="Cumulative approval cap"):
        screen.open_ledger(path, prepared[4])
    assert ledger["attempts"] == [] and len(ledger["frozen"]["parent"]["reservations"]) == 12


@pytest.mark.asyncio
async def test_followup_requires_missing_corrupt_changed_parent_and_independent_pin(tmp_path):
    path, expected_hash, _ = await fictional_parent(tmp_path)
    for invalid in ("", "g" * 64, "A" * 64, "0" * 64):
        with pytest.raises(ValueError, match="SHA-256"):
            await screen.prepare_followup(path, invalid)
    original_bytes = path.read_bytes()
    path.write_bytes(original_bytes + b"\n")
    with pytest.raises(ValueError, match="SHA-256"):
        await screen.prepare_followup(path, expected_hash)
    path.write_text("{broken")
    with pytest.raises(ValueError):
        await screen.prepare_followup(path, hashlib.sha256(path.read_bytes()).hexdigest())
    path.unlink()
    with pytest.raises(FileNotFoundError):
        await screen.prepare_followup(path, expected_hash)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption", ["prompt", "fixture", "reservation", "uncertain", "incomplete"]
)
async def test_followup_rejects_inconsistent_historical_chain_even_with_new_pin(
    tmp_path, corruption
):
    path, _, parent = await fictional_parent(tmp_path)
    if corruption == "prompt":
        parent["frozen"]["prompt_sha256"] = "0" * 64
    elif corruption == "fixture":
        parent["frozen"]["hashes"]["tests/fixtures/memory_judge_qualification.json"] = "0" * 64
    elif corruption == "reservation":
        parent["attempts"][0]["input"] += 1
    elif corruption == "uncertain":
        parent["attempts"][0]["outcome"] = "uncertain"
    else:
        parent["attempts"].pop()
    screen.write_json(path, parent)
    with pytest.raises(ValueError):
        await screen.prepare_followup(path, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "identity"])
async def test_followup_unknown_send_keeps_cumulative_reservation_and_refuses_resume(
    tmp_path, mode
):
    parent_path, parent_hash, _ = await fictional_parent(tmp_path)
    original_bytes = parent_path.read_bytes()
    path = screen.followup_path(parent_path)
    prepared = await screen.prepare_followup(parent_path, parent_hash)
    ledger = screen.open_ledger(path, prepared[4])
    server = FictionalServer(prepared[1], path, mode)
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(ValueError, match="full reservation retained"):
            await screen.screen(client, "fictional-test-secret", path, ledger, prepared)
    assert len(server.posts) == 1 and len(ledger["attempts"]) == 1
    assert len(ledger["frozen"]["parent"]["reservations"]) == 12
    assert parent_path.read_bytes() == original_bytes
    with pytest.raises(ValueError, match="resume refused"):
        screen.open_ledger(path, prepared[4])


@pytest.mark.asyncio
async def test_followup_locks_both_paths_and_refuses_alternate_successors(tmp_path, monkeypatch):
    import orchestrator.config

    parent_path, parent_hash, _ = await fictional_parent(tmp_path)
    path = screen.followup_path(parent_path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(parent_path)
    assert screen.followup_path(alias) == path
    with screen.exclusive_followup(alias):
        for locked_path in (parent_path, path):
            with pytest.raises(BlockingIOError), screen.exclusive_ledger(locked_path):
                pytest.fail("Parent or successor lock was not held")
    monkeypatch.setattr(
        orchestrator.config, "Settings", lambda **kw: pytest.fail("credential read")
    )
    args = Namespace(
        execute=True,
        approved=True,
        credential_env_file=None,
        followup_parent=parent_path,
        parent_sha256=parent_hash,
        ledger=tmp_path / "alternative.json",
    )
    with pytest.raises(ValueError, match="fixed"):
        await screen.run(args)
    assert not args.ledger.exists()
    path.symlink_to(args.ledger)
    with pytest.raises(ValueError, match="symlink"):
        screen.followup_path(parent_path)


@pytest.mark.asyncio
async def test_followup_dry_run_and_corrupt_successor_refuse_before_credentials(
    tmp_path, monkeypatch, capsys
):
    import orchestrator.config

    parent_path, parent_hash, _ = await fictional_parent(tmp_path)
    parent_bytes = parent_path.read_bytes()
    path = screen.followup_path(parent_path)
    monkeypatch.setattr(
        orchestrator.config, "Settings", lambda **kw: pytest.fail("credential read")
    )
    args = Namespace(
        execute=False,
        approved=False,
        credential_env_file=None,
        ledger=None,
        followup_parent=parent_path,
        parent_sha256=parent_hash,
    )
    assert await screen.run(args)
    assert json.loads(capsys.readouterr().out)["frozen"]["parent"]["sha256"] == parent_hash
    assert parent_path.read_bytes() == parent_bytes and not path.exists()
    assert not path.with_suffix(path.suffix + ".lock").exists()
    prepared = await screen.prepare_followup(parent_path, parent_hash)
    ledger = screen.open_ledger(path, prepared[4])
    ledger["frozen"]["parent"]["reservations"] = []
    screen.write_json(path, ledger)
    args.execute = args.approved = True
    with pytest.raises(ValueError, match="changed"):
        await screen.run(args)


@pytest.mark.asyncio
async def test_followup_cli_partial_resume_holds_both_locks_through_dispatch(
    tmp_path, monkeypatch, capsys
):
    import orchestrator.config

    parent_path, parent_hash, _ = await fictional_parent(tmp_path)
    original_bytes = parent_path.read_bytes()
    path = screen.followup_path(parent_path)
    prepared = await screen.prepare_followup(parent_path, parent_hash)
    ledger = screen.open_ledger(path, prepared[4])
    first = prepared[1][0]
    attempt = screen.reserve(ledger, first["id"], prepared[3][first["id"]])
    attempt.update(
        outcome="valid",
        request=screen.request_body(prepared[2][first["id"]], prepared[4]),
        receipt=response_for(first),
    )
    screen.write_json(path, ledger)
    server = FictionalServer(prepared[1], path)

    def locked_dispatch(request):
        for locked_path in (parent_path, path):
            with pytest.raises(BlockingIOError), screen.exclusive_ledger(locked_path):
                pytest.fail("Dispatch did not retain both locks")
        return server(request)

    monkeypatch.setattr(
        orchestrator.config,
        "Settings",
        lambda **kw: SimpleNamespace(
            daemon_inference_policy=str(screen.POLICY),
            daemon_model_routing=None,
            openrouter_api_key="fictional-test-secret",
        ),
    )
    monkeypatch.setattr(
        screen.httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(locked_dispatch)
    )
    args = Namespace(
        execute=True,
        approved=True,
        credential_env_file=None,
        ledger=None,
        followup_parent=parent_path,
        parent_sha256=parent_hash,
    )
    assert await screen.run(args)
    assert len(server.posts) == 11 and len(server.gets) == 22
    assert json.loads(capsys.readouterr().out)["ledger"] == str(path)
    assert parent_path.read_bytes() == original_bytes
    assert len(json.loads(path.read_text())["attempts"]) == 12
    assert await screen.run(args)
    assert len(server.posts) == 11


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["uncertain", "transport_or_identity_failure"])
async def test_followup_uncertain_successor_refused_before_credentials(
    tmp_path, monkeypatch, outcome
):
    import orchestrator.config

    parent_path, parent_hash, _ = await fictional_parent(tmp_path)
    path = screen.followup_path(parent_path)
    prepared = await screen.prepare_followup(parent_path, parent_hash)
    ledger = screen.open_ledger(path, prepared[4])
    batch = prepared[1][0]
    screen.reserve(ledger, batch["id"], prepared[3][batch["id"]])["outcome"] = outcome
    screen.write_json(path, ledger)
    before = path.read_bytes()
    monkeypatch.setattr(
        orchestrator.config, "Settings", lambda **kw: pytest.fail("credential read")
    )
    args = Namespace(
        execute=True,
        approved=True,
        credential_env_file=None,
        ledger=None,
        followup_parent=parent_path,
        parent_sha256=parent_hash,
    )
    with pytest.raises(ValueError, match="resume refused"):
        await screen.run(args)
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_corrupt_later_resume_request_prevents_any_new_dispatch(tmp_path):
    parent_path, parent_hash, _ = await fictional_parent(tmp_path)
    path = screen.followup_path(parent_path)
    prepared = await screen.prepare_followup(parent_path, parent_hash)
    ledger = screen.open_ledger(path, prepared[4])
    batch = prepared[1][-1]  # Missing earlier batches must not send before this is checked.
    attempt = screen.reserve(ledger, batch["id"], prepared[3][batch["id"]])
    attempt.update(outcome="valid", request={"changed": True}, receipt=response_for(batch))
    server = FictionalServer(prepared[1], path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
        with pytest.raises(ValueError, match="request differs"):
            await screen.screen(client, "fictional-test-secret", path, ledger, prepared)
    assert server.posts == [] and server.gets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["followup_parent", "parent_sha256"])
async def test_followup_requires_paired_parent_and_pin_before_credentials(
    tmp_path, monkeypatch, missing
):
    import orchestrator.config

    monkeypatch.setattr(
        orchestrator.config, "Settings", lambda **kw: pytest.fail("credential read")
    )
    args = Namespace(
        execute=True,
        approved=True,
        credential_env_file=None,
        ledger=None,
        followup_parent=tmp_path / "nonexistent.json",
        parent_sha256="a" * 64,
    )
    setattr(args, missing, None)
    with pytest.raises(ValueError, match="together"):
        await screen.run(args)
