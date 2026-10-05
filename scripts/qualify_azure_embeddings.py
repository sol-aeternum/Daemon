"""Opt-in fictional continuation; production wire/parser, no accounts or DB.

Defaults to offline preparation. --execute --approved uses the remaining ORIGINAL
embedding approval, never a reset. Independent review is required before dispatch.
Isolated evaluation approval does not enable the application's unapproved route.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
import uuid

import httpx

from orchestrator import compute_runtime as runtime
from orchestrator.entitlements import EntitlementService
from orchestrator.entitlements.policy import OperatorReview, load_inference_policy
from orchestrator.memory import embedding_adapter as adapter
from scripts import embedding_qualification_support as support

ROUTE_ID = "azure-openrouter-small-1024"
OWNER = uuid.UUID(int=4452026)
POLICY = support.ROOT / "config/inference_policy.production.json"
HTTP_TRANSPORT = httpx.AsyncHTTPTransport
HTTP_CLIENT = httpx.AsyncClient
RECEIPT_PARSER = adapter.validate_receipt
EXPECTED_PROVIDER = {
    "only": ["azure"],
    "order": ["azure"],
    "allow_fallbacks": False,
    "require_parameters": True,
    "data_collection": "deny",
    "zdr": True,
    "max_price": {"prompt": 0.02},
}


def evaluation_policy():
    """Process-local fictional approval; public evidence is checked per live send."""
    policy = load_inference_policy(POLICY)
    route = policy.embedding_route(ROUTE_ID)
    if route is None:
        raise ValueError("Evaluation route missing")
    adapter.validate_adapter_route(route)
    route = replace(
        route,
        approved=True,
        availability="verified",
        account_prompt_logging_disabled=True,
        free_model_training_opt_out=True,
        review=OperatorReview(
            reviewer="fictional-only evaluation",
            reviewed_at=datetime(2026, 10, 4, tzinfo=UTC),
            review_expires_at=None,
            evidence=("Owner's cumulative fictional #445 embedding approval; NOT a runtime grant",),
        ),
    )
    requirements = replace(policy.requirements, require_zdr_attestation=False)
    policy = replace(policy, requirements=requirements, embedding_routes={ROUTE_ID: route})
    options = route.transport_payload(requirements)["extra_body"]["provider"]
    if options != EXPECTED_PROVIDER or route.max_batch_items < 64:
        raise ValueError("Frozen evaluation transport changed")
    return policy, route


class FictionalAccount:
    """In-memory scope only; never connects to a database or real owner account."""

    async def reserve(self, user_id, bound, **kwargs):
        if user_id != OWNER or kwargs["route_id"] != ROUTE_ID or not kwargs["background"]:
            raise ValueError("Unexpected evaluation account binding")
        if type(bound) is not int or bound <= 0:
            raise ValueError("Invalid evaluation account hold")
        return type("FictionalReservation", (), {"id": uuid.uuid4()})()

    async def settle(self, reservation, actual, **kwargs):
        if type(actual) is not int or actual < 0:
            raise ValueError("Invalid evaluation settlement")


@contextmanager
def isolated(policy, transport, parser):
    scope = runtime.ComputeScope(
        OWNER, cast(EntitlementService, FictionalAccount()), background=True
    )
    token = runtime._scope.set(scope)
    try:
        with (
            patch.object(adapter, "load_inference_policy", return_value=policy),
            patch.object(runtime, "load_inference_policy", return_value=policy),
            patch.object(adapter.httpx, "AsyncHTTPTransport", return_value=transport),
            patch.object(adapter, "validate_receipt", parser),
        ):
            yield
    finally:
        runtime._scope.reset(token)


class ControlledTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        *,
        live: bool = False,
        ledger: dict[str, Any] | None = None,
        ledger_path: Path | None = None,
        frozen: dict[str, Any] | None = None,
    ):
        self.live, self.ledger, self.ledger_path, self.frozen = live, ledger, ledger_path, frozen
        self.batch = ""
        self.requests: dict[str, Any] = {}
        self.attempt: dict[str, Any] | None = None
        self.network = HTTP_TRANSPORT(retries=0, trust_env=False) if live else None

    async def evidence(self):
        async with HTTP_CLIENT(
            timeout=30,
            trust_env=False,
            follow_redirects=False,
            transport=HTTP_TRANSPORT(retries=0, trust_env=False),
        ) as client:
            sources = {}
            for name, url in {
                "zdr": f"{adapter.ENDPOINT}/endpoints/zdr",
                "providers": "https://openrouter.ai/api/frontend/v1/all-providers",
                "endpoint": f"{adapter.ENDPOINT}/models/{adapter.MODEL}/endpoints",
            }.items():
                response = await client.get(url)
                response.raise_for_status()
                sources[name] = (response.json(), hashlib.sha256(response.content).hexdigest())
        rows = sources["zdr"][0]["data"]
        if not any(
            row.get("model_id") == adapter.MODEL and row.get("tag") == "azure" for row in rows
        ):
            raise ValueError("Exact embedding route left the ZDR list")
        providers = sources["providers"][0]["data"]
        azure = [row for row in providers if row.get("slug") == "azure"]
        if len(azure) != 1 or any(
            azure[0]["dataPolicy"].get(key) is not False
            for key in ("training", "retainsPrompts", "trainingOpenRouter", "canPublish")
        ):
            raise ValueError("Embedding provider privacy baseline changed")
        endpoints = [
            row for row in sources["endpoint"][0]["data"]["endpoints"] if row.get("tag") == "azure"
        ]
        if len(endpoints) != 1 or endpoints[0].get("status") != 0:
            raise ValueError("Embedding endpoint unavailable")
        price = Decimal(endpoints[0]["pricing"]["prompt"])
        if not price.is_finite() or price < 0 or price > Decimal("0.00000002"):
            raise ValueError("Embedding price ceiling changed")
        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "sha256": {k: v[1] for k, v in sources.items()},
        }

    async def handle_async_request(self, request):
        if str(request.url) != f"{adapter.ENDPOINT}/embeddings" or request.method != "POST":
            raise ValueError("Unexpected evaluation dispatch destination")
        body = json.loads(request.content)
        if self.batch in self.requests:
            raise ValueError("Hidden retry or split request refused")
        if body["provider"] != EXPECTED_PROVIDER or body["model"] != adapter.MODEL:
            raise ValueError("Evaluation routing controls changed")
        self.requests[self.batch] = body
        if not self.live:
            return httpx.Response(
                200,
                json={
                    "model": adapter.MODEL,
                    "provider": "Azure",
                    "data": [
                        {"index": i, "embedding": [1.0] + [0.0] * 1023}
                        for i in range(len(body["input"]))
                    ],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        if self.frozen is None or body != self.frozen["requests"].get(self.batch):
            raise ValueError("Actual wire payload differs from frozen request")
        if self.ledger is None or self.ledger_path is None:
            raise ValueError("Live evaluation requires a cumulative ledger")
        evidence = await self.evidence()
        bound = sum(adapter.input_bound(text) for text in body["input"])
        self.attempt = support.reserve_durable(self.ledger_path, self.ledger, self.batch, bound)
        self.attempt.update(payload_sha256=support.digest(body), public_evidence=evidence)
        support.write_ledger(self.ledger_path, self.ledger)
        assert self.network is not None
        return await self.network.handle_async_request(request)

    async def aclose(self):
        if self.network is not None:
            await self.network.aclose()


async def prepare():
    policy, route = evaluation_policy()
    fixture, documents = support.fixtures()
    requests = {}
    for batch, texts in (
        ("documents", [row["text"] for row in documents]),
        ("queries", [row["query"] for row in fixture["scenarios"]]),
    ):
        transport = ControlledTransport()
        transport.batch = batch
        with isolated(policy, transport, RECEIPT_PARSER):
            await adapter.embed(texts, route_id=ROUTE_ID, api_key="fictional-placeholder")
        requests.update(transport.requests)
    return policy, route, fixture, documents, requests


def frozen_identity(requests, parent):
    paths = (
        support.FIXTURE,
        POLICY,
        Path(__file__),
        Path(support.__file__),
        support.ROOT / "orchestrator/memory/embedding_adapter.py",
        support.ROOT / "orchestrator/compute_runtime.py",
        support.ROOT / "orchestrator/entitlements/policy.py",
    )
    return {
        "approval": "memory-445-20261004-fictional-only",
        "parent_sha256": support.PARENT_SHA256,
        "parent_reservations": parent,
        "criteria": support.CRITERIA,
        "requests": requests,
        "sha256": {
            str(path.relative_to(support.ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths
        },
    }


async def execute(parent_path: Path, credential_env_file: str):
    policy, route, fixture, documents, requests = await prepare()
    # Locks cover original identity, all reservations, dispatch and final scoring.
    with support.cumulative_ledger(parent_path) as (path, parent):
        frozen = frozen_identity(requests, parent)
        ledger = support.open_followup(path, frozen)
        trial = {"frozen": frozen, "attempts": []}
        for batch, body in requests.items():
            support.reserve(trial, batch, sum(adapter.input_bound(text) for text in body["input"]))
        from orchestrator.config import Settings

        settings_options: dict[str, Any] = {"_env_file": credential_env_file}
        key = Settings(**settings_options).openrouter_api_key
        if not key:
            raise ValueError("Evaluation credentials unavailable")
        for batch, body in requests.items():
            previous = [attempt for attempt in ledger["attempts"] if attempt["batch"] == batch]
            if previous:
                if previous[0].get("payload_sha256") != support.digest(body):
                    raise ValueError("Retained payload identity changed")
                RECEIPT_PARSER(previous[0]["receipt"], route, len(body["input"]))
                continue
            transport = ControlledTransport(
                live=True, ledger=ledger, ledger_path=path, frozen=frozen
            )
            transport.batch = batch

            def parser(payload, actual_route, count):
                if transport.attempt is None:
                    raise ValueError("No durable reservation before receipt")
                # Receipt belongs to fictional inputs; preserve only the required
                # response fields, never headers or arbitrary provider errors.
                if isinstance(payload, dict):
                    transport.attempt["receipt"] = {
                        k: payload[k]
                        for k in ("model", "provider", "data", "usage")
                        if k in payload
                    }
                    support.write_ledger(path, ledger)
                return RECEIPT_PARSER(payload, actual_route, count)

            try:
                with isolated(policy, transport, parser):
                    await adapter.embed(body["input"], route_id=ROUTE_ID, api_key=key)
                assert transport.attempt is not None
                transport.attempt["outcome"] = "valid"
                support.write_ledger(path, ledger)
            except BaseException:
                # Every post-send attempt stays charged; no implicit replay.
                if transport.attempt is not None:
                    support.write_ledger(path, ledger)
                raise
        by_batch = {attempt["batch"]: attempt for attempt in ledger["attempts"]}
        vectors = {
            batch: RECEIPT_PARSER(row["receipt"], route, len(requests[batch]["input"]))[0]
            for batch, row in by_batch.items()
        }
        ledger["scores"] = support.score(
            fixture,
            documents,
            vectors["documents"],
            vectors["queries"],
            query_texts=requests["queries"]["input"],
        )
        support.write_ledger(path, ledger)
        print(json.dumps(ledger["scores"], indent=2))
        return ledger["scores"]["retrieval_pass"]


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--credential-env-file")
    args = parser.parse_args()
    if args.execute:
        if not args.approved or args.parent is None or not args.credential_env_file:
            parser.error(
                "Execution requires --approved, immutable --parent and credential env file"
            )
        return 0 if await execute(args.parent, args.credential_env_file) else 1
    _, _, _, documents, requests = await prepare()
    print(
        json.dumps(
            {
                "mode": "offline preparation; NO provider calls",
                "documents": len(documents),
                "queries": support.CRITERIA["queries"],
                "requests": len(requests),
                "criteria": support.CRITERIA,
                "input_bound": sum(
                    adapter.input_bound(text)
                    for body in requests.values()
                    for text in body["input"]
                ),
                "requests_sha256": support.digest(requests),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
