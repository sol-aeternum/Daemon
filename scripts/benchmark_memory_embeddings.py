"""Isolated, opt-in fictional embedding screen; never used by application routing.

Operator approval: memory #445, 2026-10-04. Hard cumulative limits include
uncertain sends/restarts: 24 embedding requests, 100,000 input tokens, $0.25.
Voyage's retained endpoint is permitted ONLY for these fixed fictional inputs.
No database access, automatic retries, fallback, redirects or runtime policy edits.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import time
from typing import Any

import httpx
import tiktoken
from tokenizers import Tokenizer

BASE = "https://openrouter.ai/api/v1"
MAX_REQUESTS = 24
MAX_TOKENS = 100_000
MAX_USD = Decimal("0.25")
DIMENSIONS = 1024
FIXTURES = (
    "Ari's favorite tea is oolong.",
    "Oolong is Ari's favourite tea.",
    "Ari prefers oolong tea above all other teas.",
    "Ari's favorite tea is green tea.",
    "Ari dislikes oolong tea.",
    "Ari enjoys chamomile tea at bedtime.",
    "Ari's favorite tea was oolong in 2020.",
    "Ari chooses oolong tea only when travelling.",
    "Jules's favorite tea is oolong.",
)
QUERIES = ("What is Ari's favorite tea?", "What is Jules's favorite tea?")
ROUTES = (
    ("voyageai/voyage-4-large", "voyageai", False, "0.12", "documents"),
    ("voyageai/voyage-4-lite", "voyageai", False, "0.02", "queries"),
    ("openai/text-embedding-3-small", "azure", True, "0.02", "both"),
    ("openai/text-embedding-3-large", "azure", True, "0.13", "both"),
    ("google/gemini-embedding-2", "google-vertex/eu", True, "0.22", "both"),
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: Any) -> None:
    """Atomic, durable replacement; ledger ownership is held for the entire run."""
    tmp = path.with_suffix(path.suffix + ".pending")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@contextmanager
def exclusive_ledger(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def reserve(ledger: dict[str, Any], tokens: int, price_per_million: str) -> dict[str, Any]:
    if type(tokens) is not int or tokens <= 0:
        raise ValueError("Invalid token reservation")
    # 10% gateway-fee allowance, above currently advertised credit-purchase fees.
    cost = Decimal(tokens) * Decimal(price_per_million) / 1_000_000 * Decimal("1.10")
    attempts = ledger["attempts"]
    total_tokens = sum(attempt["reserved_tokens"] for attempt in attempts)
    total_cost = sum((Decimal(attempt["reserved_usd"]) for attempt in attempts), Decimal(0))
    if len(attempts) >= MAX_REQUESTS or total_tokens + tokens > MAX_TOKENS:
        raise ValueError("Cumulative request/token cap reached")
    if total_cost + cost > MAX_USD:
        raise ValueError("Cumulative cost cap reached")
    attempt = {
        "at": utc_now(),
        "reserved_tokens": tokens,
        "reserved_usd": str(cost),
        "outcome": "uncertain",
    }
    attempts.append(attempt)
    return attempt


def inputs_for(model: str, kind: str) -> tuple[list[str], str | None]:
    inputs: list[str] = list(FIXTURES if kind == "documents" else QUERIES)
    task = None
    if model.startswith("voyageai/"):
        task = "document" if kind == "documents" else "query"
    elif model.startswith("google/"):
        # Official Embedding 2 asymmetric formatting, not Embedding 001 task_type.
        prefix = "title: none | text: " if kind == "documents" else "task: search result | query: "
        inputs = [prefix + text for text in inputs]
    return inputs, task


def token_bound(
    model: str,
    inputs: list[str],
    task: str | None,
    cache: Path,
    client: httpx.Client,
    artifacts: dict[str, str] | None = None,
) -> int:
    if model.startswith("google/"):
        # No official local Gemini tokenizer. Reserve its FULL advertised context
        # for EACH tiny input, not a chars/4 estimate. Nine docs + two queries =
        # 90,112 reserved tokens. No Gemini retries fit the remaining allowance.
        # Bound depends on the endpoint's independently checked 8192-token limit.
        return 8192 * len(inputs)
    if model.startswith("openai/"):
        encoding = tiktoken.get_encoding("cl100k_base")
        return sum(len(encoding.encode(text, disallowed_special="all")) for text in inputs)
    name = model.removeprefix("voyageai/")
    destination = cache / f"{name}-tokenizer.json"
    if not destination.exists():
        response = client.get(f"https://huggingface.co/voyageai/{name}/resolve/main/tokenizer.json")
        # HF download uses a redirect; inspect/follow only this public artifact,
        # with a separate client and no credentials (never an embedding redirect).
        if response.is_redirect:
            with httpx.Client(follow_redirects=True, timeout=60) as public:
                response = public.get(response.request.url.join(response.headers["location"]))
        response.raise_for_status()
        destination.write_bytes(response.content)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if artifacts is not None:
        if model in artifacts and artifacts[model] != digest:
            raise ValueError("Cached tokenizer changed since previous attempt")
        artifacts[model] = digest
    tokenizer = Tokenizer.from_file(str(destination))
    prefix = (
        "Represent the document for retrieval: "
        if task == "document"
        else "Represent the query for retrieving supporting documents: "
    )
    # Official per-model tokenizer + documented input_type prompt. Extra 128
    # tokens/input safely reserves adapter special-token overhead, never reconciled
    # downward. Tokenizer bytes/digest are kept as reproducibility artifacts.
    return sum(len(tokenizer.encode(prefix + text).ids) + 128 for text in inputs)


# Observed Azure receipt, confirmed by OpenAI's model documentation:
# https://developers.openai.com/api/docs/models/text-embedding-3-small
# https://developers.openai.com/api/docs/models/text-embedding-3-large
# https://ai.google.dev/gemini-api/docs/embeddings
# This is response spelling only, never a request-routing alias or suffix rule.
NATIVE_RECEIPT_MODELS = {
    "openai/text-embedding-3-small": "text-embedding-3-small",
    "openai/text-embedding-3-large": "text-embedding-3-large",
    "google/gemini-embedding-2": "gemini-embedding-2",
}


def receipt_diagnostics(data: dict[str, Any], providers: tuple[str, str]) -> dict[str, Any]:
    provider = data.get("provider")
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    return {
        "receipt_provider": provider
        if isinstance(provider, str) and provider in providers
        else (None if provider is None else "unexpected"),
        "receipt_usage": {
            key: value if type(value := usage.get(key)) is int and value >= 0 else None
            for key in ("prompt_tokens", "total_tokens")
        },
    }


def validate_vectors(data: Any, model: str, count: int) -> list[list[float]]:
    if not isinstance(data, dict):
        raise ValueError("Non-object embedding receipt")
    returned_model = data.get("model")
    if not isinstance(returned_model, str) or (
        returned_model != model and returned_model != NATIVE_RECEIPT_MODELS.get(model)
    ):
        raise ValueError("Returned model differs from pinned model")
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != count:
        raise ValueError("Unexpected embedding count")
    if {row.get("index") for row in rows if isinstance(row, dict)} != set(range(count)):
        raise ValueError("Missing, duplicate or invalid embedding indices")
    vectors = []
    for row in sorted(rows, key=lambda item: item["index"]):
        vector = row.get("embedding")
        if not isinstance(vector, list) or len(vector) != DIMENSIONS:
            raise ValueError("Unexpected embedding dimensions")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
            raise ValueError("Non-finite or non-numeric embedding")
        if sum(value * value for value in vector) == 0:
            raise ValueError("Zero embedding")
        vectors.append(vector)
    return vectors


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Vector-space dimension mismatch")
    return sum(a * b for a, b in zip(left, right, strict=True)) / math.sqrt(
        sum(a * a for a in left) * sum(b * b for b in right)
    )


def scores(documents: list[list[float]], queries: list[list[float]]) -> dict[str, Any]:
    duplicates = [cosine(documents[a], documents[b]) for a, b in ((0, 1), (0, 2), (1, 2))]
    distinct = [cosine(documents[0], documents[index]) for index in range(3, 9)]
    rankings = [
        sorted(range(len(documents)), key=lambda i: cosine(query, documents[i]), reverse=True)
        for query in queries
    ]
    return {
        "duplicate_cosines": duplicates,
        "distinct_cosines": distinct,
        "separation_margin": min(duplicates) - max(distinct),
        "retrieval_rankings": rankings,
        "retrieval_top1": [rankings[0][0] in (0, 1, 2), rankings[1][0] == 8],
        "note": "Tiny diagnostic, not qualification or proof of safe semantic merging; temporal state is not inferred.",
    }


def run(ledger_path: Path, credential_env_file: str | None) -> None:
    with exclusive_ledger(ledger_path):
        ledger = (
            json.loads(ledger_path.read_text())
            if ledger_path.exists()
            else {"approval": "memory-445-20261004-fictional-only", "attempts": [], "results": {}}
        )
        if ledger.get("approval") != "memory-445-20261004-fictional-only":
            raise ValueError("Wrong approval ledger")
        # This is a single-operator evaluation ledger, not a malicious-operator
        # security boundary. Refuse corrupt accounting; no reset/release command.
        if not isinstance(ledger.get("attempts"), list):
            raise ValueError("Corrupt attempt ledger")
        for previous in ledger["attempts"]:
            if (
                type(previous.get("reserved_tokens")) is not int
                or previous["reserved_tokens"] <= 0
                or not Decimal(previous["reserved_usd"]).is_finite()
                or Decimal(previous["reserved_usd"]) <= 0
            ):
                raise ValueError("Corrupt reservation")
        from orchestrator.config import Settings

        options: dict[str, Any] = {"_env_file": credential_env_file} if credential_env_file else {}
        settings = Settings(**options)
        key = settings.openrouter_api_key
        if not key:
            raise ValueError("OpenRouter credentials unavailable")
        # Credentials only accompany embedding/generation URLs on this exact host.
        headers = {"Authorization": f"Bearer {key}"}
        with httpx.Client(
            timeout=60, follow_redirects=False, transport=httpx.HTTPTransport(retries=0)
        ) as client:
            zdr = client.get(f"{BASE}/endpoints/zdr")
            zdr.raise_for_status()
            zdr_pairs = {(row["model_id"], row["tag"]) for row in zdr.json()["data"]}
            ledger["catalog_checked_at"] = utc_now()
            ledger["fixtures"] = list(FIXTURES)
            ledger["queries"] = list(QUERIES)
            for model, provider, require_zdr, price, mode in ROUTES:
                catalog = client.get(f"{BASE}/models/{model}/endpoints")
                catalog.raise_for_status()
                matching = [
                    row
                    for row in catalog.json()["data"]["endpoints"]
                    if row["tag"] == provider and row["status"] == 0
                ]
                if (
                    not matching
                    or Decimal(matching[0]["pricing"]["prompt"]) > Decimal(price) / 1_000_000
                ):
                    raise ValueError("Pinned endpoint absent or above price ceiling")
                if model.startswith("google/") and matching[0]["context_length"] != 8192:
                    raise ValueError("Gemini token-bound context changed")
                if require_zdr and (model, provider) not in zdr_pairs:
                    raise ValueError("Pinned endpoint not currently ZDR-listed")
                kinds = ("documents", "queries") if mode == "both" else (mode,)
                for kind in kinds:
                    identity = f"{model}@{provider}:{kind}:{DIMENSIONS}"
                    if identity in ledger["results"]:
                        continue
                    if any(
                        a.get("identity") == identity and a.get("outcome") == "http_error"
                        for a in ledger["attempts"]
                    ):
                        continue  # Definitively refused route: never repeat it.
                    inputs, task = inputs_for(model, kind)
                    bound = token_bound(
                        model,
                        inputs,
                        task,
                        ledger_path.parent,
                        client,
                        ledger.setdefault("tokenizer_sha256", {}),
                    )
                    provider_options: dict[str, Any] = {
                        "only": [provider],
                        "order": [provider],
                        "allow_fallbacks": False,
                        "require_parameters": True,
                        "data_collection": "deny",
                        "max_price": {"prompt": price},
                    }
                    if require_zdr:
                        provider_options["zdr"] = True
                    payload: dict[str, Any] = {
                        "model": model,
                        "input": inputs,
                        "dimensions": DIMENSIONS,
                        "encoding_format": "float",
                        "provider": provider_options,
                    }
                    if task:
                        payload["input_type"] = task
                    attempt = reserve(ledger, bound, price)
                    attempt.update(
                        identity=identity,
                        payload_sha256=hashlib.sha256(
                            json.dumps(payload, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                    write_json(ledger_path, ledger)  # Reserve BEFORE possible dispatch.
                    started = time.monotonic()
                    try:
                        response = client.post(f"{BASE}/embeddings", headers=headers, json=payload)
                        attempt["latency_seconds"] = time.monotonic() - started
                        attempt["http_status"] = response.status_code
                        if response.status_code != 200:
                            attempt["outcome"] = "http_error"
                            write_json(ledger_path, ledger)
                            print(
                                json.dumps(
                                    {"identity": identity, "http_status": response.status_code}
                                )
                            )
                            # An unsupported route is a result, not permission to
                            # remove constraints or try a different host/parameter.
                            break
                        data = response.json()
                        if isinstance(data, dict):
                            # Public response metadata only, not request headers
                            # or arbitrary provider error bodies. Retain evidence
                            # before validation so a refused receipt is diagnosable.
                            returned_model = data.get("model")
                            attempt["receipt_model"] = (
                                returned_model
                                if (
                                    isinstance(returned_model, str)
                                    and len(returned_model) < 128
                                    and all(c.isalnum() or c in "/:._-" for c in returned_model)
                                )
                                else None
                            )
                            attempt["receipt_keys"] = sorted(
                                k
                                for k in data
                                if k in ("id", "model", "provider", "data", "usage", "object")
                            )
                            rows = data.get("data")
                            attempt["receipt_count"] = len(rows) if isinstance(rows, list) else None
                            attempt.update(
                                receipt_diagnostics(data, (provider, matching[0]["provider_name"]))
                            )
                            write_json(ledger_path, ledger)
                        vectors = validate_vectors(data, model, len(inputs))
                        usage = data.get("usage", {})
                        if not isinstance(usage, dict):
                            raise ValueError("Non-object token usage")
                        reported = usage.get("prompt_tokens", usage.get("total_tokens"))
                        if type(reported) is not int or reported < 0 or reported > bound:
                            raise ValueError("Token usage absent or exceeds reservation")
                        # Some embedding receipts omit provider identity. Never
                        # invent attestation from the requested pin alone.
                        returned_provider = data.get("provider")
                        if returned_provider and returned_provider not in (
                            provider,
                            matching[0]["provider_name"],
                        ):
                            raise ValueError("Unexpected returned provider")
                        result = {
                            "vectors": vectors,
                            "usage": usage,
                            "id": data.get("id"),
                            "model": model,
                            "receipt_model": attempt["receipt_model"],
                            "provider_pin": provider,
                            "provider_attested": bool(returned_provider),
                            "dimension": DIMENSIONS,
                            "task": task,
                            "latency_seconds": attempt["latency_seconds"],
                        }
                        ledger["results"][identity] = result
                        attempt.update(
                            outcome="success",
                            reported_tokens=reported,
                            provider_attested=bool(returned_provider),
                        )
                        write_json(ledger_path, ledger)
                        print(
                            json.dumps(
                                {
                                    "identity": identity,
                                    "reported_tokens": reported,
                                    "latency_seconds": attempt["latency_seconds"],
                                    "provider_attested": bool(returned_provider),
                                }
                            )
                        )
                    except (httpx.HTTPError, ValueError, KeyError, TypeError):
                        write_json(ledger_path, ledger)
                        # Leave uncertain attempt's full hold in place; no retry.
                        raise RuntimeError(
                            "Benchmark stopped: invalid or uncertain receipt"
                        ) from None
            comparisons = {}
            for label, doc_model, query_model, provider in (
                (
                    "voyage-asymmetric",
                    "voyageai/voyage-4-large",
                    "voyageai/voyage-4-lite",
                    "voyageai",
                ),
                (
                    "openai-small",
                    "openai/text-embedding-3-small",
                    "openai/text-embedding-3-small",
                    "azure",
                ),
                (
                    "openai-large",
                    "openai/text-embedding-3-large",
                    "openai/text-embedding-3-large",
                    "azure",
                ),
                (
                    "gemini-eu",
                    "google/gemini-embedding-2",
                    "google/gemini-embedding-2",
                    "google-vertex/eu",
                ),
            ):
                docs = ledger["results"].get(f"{doc_model}@{provider}:documents:{DIMENSIONS}")
                queries = ledger["results"].get(f"{query_model}@{provider}:queries:{DIMENSIONS}")
                if docs and queries:
                    comparisons[label] = scores(docs["vectors"], queries["vectors"])
                    comparisons[label]["provider_attested"] = (
                        docs["provider_attested"] and queries["provider_attested"]
                    )
            ledger["comparisons"] = comparisons
            write_json(ledger_path, ledger)
            print(
                json.dumps(
                    {
                        "requests": len(ledger["attempts"]),
                        "reserved_tokens": sum(a["reserved_tokens"] for a in ledger["attempts"]),
                        "reserved_usd": str(
                            sum(
                                (Decimal(a["reserved_usd"]) for a in ledger["attempts"]), Decimal(0)
                            )
                        ),
                        "comparisons": comparisons,
                    },
                    indent=2,
                )
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-approved-fictional-screen", action="store_true")
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--credential-env-file")
    args = parser.parse_args()
    if not args.execute_approved_fictional_screen:
        parser.error("Explicit approved-fictional-screen flag required; no requests sent")
    run(args.ledger, args.credential_env_file)


if __name__ == "__main__":
    main()
