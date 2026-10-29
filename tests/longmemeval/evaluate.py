"""LongMemEval retrieval + answering adapter.

Loads LongMemEval questions, retrieves relevant memories from Daemon's
memory store, and uses GPT-4o to generate answers in LongMemEval's
expected JSONL format.

Canonical benchmark entrypoint:
    python -m orchestrator.eval.longmemeval run --dataset /path/to/longmemeval_s.json

Contract:
    - dataset path is explicit and validated before execution
    - results are written to <output-dir>/longmemeval_results.jsonl
    - checkpoint state is written to <output-dir>/longmemeval_checkpoint.json
      unless an explicit checkpoint path is provided
    - benchmark runs always force retrieval logging on

Expected output format (LongMemEval evaluate_qa.py):
    {"question_id": "e47becba", "hypothesis": "Business Administration"}

Category breakdown (IE, MR, TR, KU, ABS):
    - IE (Information Extraction): single-session questions
    - MR (Multi-Session Reasoning): multi-session questions
    - TR (Temporal Reasoning): temporal questions
    - KU (Knowledge Update): knowledge update questions
    - ABS (Abstention): questions ending in _abs

Usage:
    python tests/longmemeval/evaluate.py
    python tests/longmemeval/evaluate.py --dataset /path/to/longmemeval_s.json --limit 10
    python -m orchestrator.eval.longmemeval run --dataset /path/to/longmemeval_s.json
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from pathlib import Path
from typing import Any

import asyncpg
from orchestrator.memory.embedding import embed_query
from orchestrator.config import get_settings
from orchestrator.memory.injection import (
    MemoryContextInputs,
    compose_system_prompt,
    gather_memory_context_inputs,
    render_memory_context,
)
from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.ingest import DATASET_PATH as DEFAULT_DATASET_PATH
from tests.longmemeval.official_judge import (
    OFFICIAL_MAX_TOKENS,
    build_official_prompt,
    official_label,
)

CATEGORY_MAP: dict[str, str] = {
    "single-session-user": "IE-user",
    "single-session-assistant": "IE-assistant",
    "single-session-preference": "IE-preference",
    "multi-session": "MR",
    "temporal-reasoning": "TR",
    "knowledge-update": "KU",
}

CATEGORY_NAMES: dict[str, str] = {
    "IE-user": "Information Extraction (User)",
    "IE-assistant": "Information Extraction (Assistant)",
    "IE-preference": "Information Extraction (Preference)",
    "MR": "Multi-Session Reasoning",
    "TR": "Temporal Reasoning",
    "KU": "Knowledge Update",
    "ABS": "Abstention",
}

ACCURACY_CATEGORIES = [
    "IE-user",
    "IE-assistant",
    "IE-preference",
    "MR",
    "KU",
    "TR",
    "ABS",
]

TEST_USER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
TEST_USER_EMAIL = "longmemeval@daemon.test"

DEFAULT_OUTPUT_DIR = Path("tests/benchmark_results")
RESULTS_FILENAME = "longmemeval_results.jsonl"
CHECKPOINT_FILENAME = "longmemeval_checkpoint.json"

TOP_K_MEMORIES = 5
RETRIEVAL_MIN_SIMILARITY = 0.0

ANSWER_MODEL = "openrouter/openai/gpt-4o"
ANSWER_TEMPERATURE = 0.7
ANSWER_MAX_TOKENS = 256

JUDGE_MODEL = "openrouter/openai/gpt-4o"
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 256
BENCHMARK_SEED = 42
BENCHMARK_ANSWER_MODEL = "openrouter/openai/gpt-4o-2024-08-06"
BENCHMARK_JUDGE_MODEL = "openrouter/openai/gpt-4o-2024-08-06"
# Azure serves the same gpt-4o snapshot; the account's ZDR guardrail removes
# OpenAI's own endpoint, and fallbacks stay off.
BENCHMARK_ANSWER_ENDPOINT_SLUG = "azure"

logger = logging.getLogger(__name__)


class BenchmarkProviderError(RuntimeError):
    """Provider or transport failure in LongMemEval benchmark mode."""


def benchmark_failure_diagnostics(exc: BaseException) -> dict[str, str | int | None]:
    """Retain allowlisted diagnostics, never exception text or response contents."""
    allowed = {
        "RateLimitError",
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "NotFoundError",
        "APITimeoutError",
        "Timeout",
        "TimeoutError",
        "APIConnectionError",
        "ServiceUnavailableError",
        "InternalServerError",
        "APIError",
    }
    kind = "OtherError"
    status = None
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(4):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if type(current).__name__ in allowed:
            kind = type(current).__name__
        code = getattr(current, "status_code", None)
        if type(code) is int and 100 <= code <= 599:
            status = code
        current = current.__cause__
    return {"provider_exception_type": kind, "http_status_code": status}


_BM_METADATA: dict[str, dict[str, str | None]] = {}


def reset_benchmark_tracking() -> None:
    _BM_METADATA.clear()


def get_benchmark_tracking() -> dict[str, dict[str, str | None]]:
    return {key: dict(value) for key, value in _BM_METADATA.items()}


def last_benchmark_fingerprint(key: str) -> str | None:
    """Fingerprint of the latest successful benchmark-mode call for ``key``."""
    return _BM_METADATA.get(key, {}).get("fingerprint")


def resolve_output_paths(
    output_dir: Path,
    checkpoint_path: Path | None = None,
) -> tuple[Path, Path]:
    output_path = output_dir / RESULTS_FILENAME
    effective_checkpoint = checkpoint_path or output_dir / CHECKPOINT_FILENAME
    return output_path, effective_checkpoint


def load_dataset(dataset_path: Path) -> list[dict[str, Any]]:
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    with dataset_path.open() as handle:
        dataset = json.load(handle)

    if not isinstance(dataset, list):
        raise ValueError(f"Dataset must be a JSON list: {dataset_path}")

    return dataset


def load_checkpoint(
    checkpoint_path: Path,
    *,
    dataset_path: Path,
) -> dict[str, dict[str, Any]]:
    if not checkpoint_path.exists():
        return {}

    with checkpoint_path.open() as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError(f"Checkpoint must be a JSON object: {checkpoint_path}")

    checkpoint_dataset = payload.get("dataset_path")
    if checkpoint_dataset and checkpoint_dataset != str(dataset_path):
        raise ValueError(
            "Checkpoint dataset mismatch: "
            f"{checkpoint_path} was created for {checkpoint_dataset}, "
            f"not {dataset_path}"
        )

    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raise ValueError(f"Checkpoint results must be a JSON list: {checkpoint_path}")

    checkpoint_results: dict[str, dict[str, Any]] = {}
    for result in raw_results:
        if not isinstance(result, dict):
            continue
        question_id = result.get("question_id")
        if not isinstance(question_id, str) or not question_id:
            continue
        checkpoint_results[question_id] = result

    return checkpoint_results


def save_checkpoint(
    checkpoint_path: Path,
    *,
    dataset_path: Path,
    results: list[dict[str, Any]],
) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset_path": str(dataset_path),
        "results": results,
    }
    with checkpoint_path.open("w") as handle:
        json.dump(payload, handle, indent=2)


def write_results_jsonl(output_path: Path, results: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as handle:
        for result in results:
            handle.write(json.dumps(result) + "\n")


def _normalize_model_for_provider(model: str) -> str:
    """Normalize model ID for OpenRouter compatibility."""
    # Ensure openrouter prefix
    if not model.startswith("openrouter/"):
        model = f"openrouter/{model}"
    return model


def _capture_benchmark_metadata(response_data: Any, *, key: str) -> None:
    if not isinstance(response_data, dict):
        return

    fingerprint = response_data.get("system_fingerprint")
    model = response_data.get("model")
    normalized_fingerprint = fingerprint if isinstance(fingerprint, str) else None
    normalized_model = model if isinstance(model, str) else None
    choices = response_data.get("choices") or []
    choice = choices[0] if choices else {}
    finish_reason = choice.get("finish_reason")
    raw_output = (choice.get("message") or {}).get("content")

    # Recorded, not enforced: the pinned endpoint rotates system_fingerprint
    # across serving nodes, so each result row carries its own and the
    # manifest reports the distribution.
    _BM_METADATA[key] = {
        "fingerprint": normalized_fingerprint,
        "model": normalized_model,
        "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
        "raw_output": raw_output if isinstance(raw_output, str) else None,
    }


async def _call_llm_with_provider_config(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    bm_call_key: str | None = None,
) -> Any | None:
    """Call LLM with proper OpenRouter provider configuration."""
    import litellm

    # Get settings and provider config
    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")

    is_benchmark = bm_call_key is not None
    if is_benchmark:
        if bm_call_key in {"judge", "official_judge"}:
            model = BENCHMARK_JUDGE_MODEL
        else:
            model = BENCHMARK_ANSWER_MODEL
        temperature = 0.0
    else:
        model = _normalize_model_for_provider(model)

    # Build call parameters
    call_params: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": provider_config.timeout_s,
    }
    if is_benchmark:
        call_params["seed"] = BENCHMARK_SEED
        call_params["num_retries"] = 0
        call_params["extra_body"] = {
            "provider": {
                "order": [BENCHMARK_ANSWER_ENDPOINT_SLUG],
                "allow_fallbacks": False,
            }
        }

    # Add provider-specific configuration
    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    from orchestrator.eval.campaign_guard import campaign_llm_call, current_campaign

    # Default-inert measurement hook. Never include credentials/headers in the
    # request identity or retained receipt. All decoding happens inside the call
    # scope so malformed paid responses also latch the campaign stop.
    guarded_request = {
        key: value
        for key, value in call_params.items()
        if key not in {"api_key", "extra_headers", "timeout"}
    }
    async with campaign_llm_call(bm_call_key or "unqualified_llm", guarded_request) as receipt:
        try:
            response = await litellm.acompletion(**call_params)
        except Exception as e:
            if is_benchmark:
                raise BenchmarkProviderError(
                    f"Benchmark-mode {bm_call_key} provider failure: {e}"
                ) from e
            logger.error(f"LLM call failed: {e}")
            return None

        if current_campaign() is not None:
            model_dump = getattr(response, "model_dump", None)
            response_data = model_dump() if callable(model_dump) else response
            if not isinstance(response_data, dict):
                raise BenchmarkProviderError("Guarded LLM receipt is not an object")
            receipt.complete(response_data)

    if is_benchmark:
        response_data: Any = response
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            response_data = model_dump()
        else:
            dict_method = getattr(response, "dict", None)
            if callable(dict_method):
                response_data = dict_method()
        _capture_benchmark_metadata(response_data, key=bm_call_key)

    return response


def _extract_content(response: Any) -> str:
    """Extract content from litellm response."""
    response_data: Any = response
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        response_data = model_dump()
    else:
        dict_method = getattr(response, "dict", None)
        if callable(dict_method):
            response_data = dict_method()

    choices = response_data.get("choices", [])
    if not choices:
        return ""

    message = choices[0].get("message", {})
    return message.get("content", "")


def build_answer_prompt(question: str, memories: list[dict[str, Any]]) -> str:
    memories_text = "\n\n".join(f"- {memory.get('content', '')}" for memory in memories)

    return f"""You are a helpful assistant. Use the provided memories to answer the question concisely.

Memories:
{memories_text}

Question: {question}

Answer:"""


def parse_answer(text: str) -> str:
    text = text.strip()
    if text.lower().startswith("answer:"):
        text = text[7:].strip()
    return text


async def judge_answer(
    question_text: str,
    hypothesis: str,
    reference: str,
    benchmark_mode: bool = False,
) -> str:
    judgment, _usage = await _judge_with_usage(
        question_text, hypothesis, reference, benchmark_mode=benchmark_mode
    )
    return judgment


def build_generic_judge_prompt(question_text: str, hypothesis: str, reference: str) -> str:
    return f"""You are judging whether an AI assistant's answer is factually correct.

Question: {question_text}
Ground truth answer: {reference}
Assistant's answer: {hypothesis}

Scoring rules:
- CORRECT: The assistant's answer contains the same core factual information as the ground truth. Paraphrasing, additional context, more verbose phrasing, or minor wording differences are all CORRECT. Example: ground truth "The Glass Menagerie", answer "a production of The Glass Menagerie at the local community theater" → CORRECT.
- PARTIAL: The answer contains SOME but not ALL required facts from a multi-part ground truth. Only use PARTIAL when the ground truth requires multiple distinct pieces of information and the answer is missing one or more of them.
- INCORRECT: The core fact is wrong, contradicts the ground truth, or the assistant says it cannot answer when the information was available.

Be generous with CORRECT. The question is whether the assistant knew the right answer, not whether it phrased it identically to the reference.

Reply with exactly one word on the first line: CORRECT, PARTIAL, or INCORRECT
Then a one-sentence explanation on the second line."""


async def _judge_with_usage(
    question_text: str,
    hypothesis: str,
    reference: str,
    *,
    benchmark_mode: bool,
) -> tuple[str, dict[str, int]]:
    prompt = build_generic_judge_prompt(question_text, hypothesis, reference)
    response = await _call_llm_with_provider_config(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=JUDGE_TEMPERATURE,
        max_tokens=JUDGE_MAX_TOKENS,
        bm_call_key="judge" if benchmark_mode else None,
    )

    if response is None:
        return "incorrect", {}
    usage = _usage_from_response(response)

    content = _extract_content(response).strip()
    first_line = content.split("\n")[0].strip().upper()

    if first_line == "CORRECT":
        return "correct", usage
    elif first_line == "PARTIAL":
        return "partially_correct", usage
    elif first_line == "INCORRECT":
        return "incorrect", usage

    content_lower = content.lower()
    if "incorrect" in content_lower:
        return "incorrect", usage
    elif "partial" in content_lower:
        return "partially_correct", usage
    elif "correct" in content_lower:
        return "correct", usage
    else:
        return "incorrect", usage


async def judge_answer_official(
    question_text: str,
    hypothesis: str,
    reference: str,
    *,
    question_id: str,
    question_type: str,
    benchmark_mode: bool = True,
) -> dict[str, Any]:
    """Second metric only: official category prompt, cap and label predicate.

    The pinned Azure route and seed remain the harness's, not upstream OpenAI's.
    Return raw output and finish reason alongside the upstream substring verdict.
    """
    prompt = build_official_prompt(
        question_type, question_text, reference, hypothesis, question_id=question_id
    )
    response = await _call_llm_with_provider_config(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=JUDGE_TEMPERATURE,
        max_tokens=OFFICIAL_MAX_TOKENS,
        bm_call_key="official_judge" if benchmark_mode else None,
    )
    if response is None:
        raise BenchmarkProviderError("Official judge returned no response")
    raw_output = _extract_content(response)
    metadata = get_benchmark_tracking().get("official_judge", {}) if benchmark_mode else {}
    return {
        "official_judgment": "correct" if official_label(raw_output) else "incorrect",
        "official_judge_raw_output": raw_output,
        "official_judge_finish_reason": metadata.get("finish_reason"),
        "official_judge_model": metadata.get("model"),
        "official_judge_fingerprint": metadata.get("fingerprint"),
        "official_judge_usage": _usage_from_response(response),
    }


async def answer_with_llm(
    question: str,
    memories: list[dict[str, Any]],
    benchmark_mode: bool = False,
) -> str:
    """Call GPT-4o via LiteLLM to generate an answer."""
    prompt = build_answer_prompt(question, memories)

    response = await _call_llm_with_provider_config(
        model=ANSWER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=ANSWER_TEMPERATURE,
        max_tokens=ANSWER_MAX_TOKENS,
        bm_call_key="answer" if benchmark_mode else None,
    )

    if response is None:
        return ""

    content = _extract_content(response)
    return parse_answer(content)


async def retrieve_user_memories(
    store: "MemoryStore",
    user_id: uuid.UUID,
    query_embedding: list[float],
    query_text: str,
    limit: int = TOP_K_MEMORIES,
    log_retrieval: bool = False,
    allowed_source_conversation_ids: list[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    return await retrieve_memories_for_text(
        store=store,
        query_text=query_text,
        user_id=user_id,
        query_embedding=query_embedding,
        limit=limit,
        include_l0=True,
        log_retrieval=log_retrieval,
        allowed_source_conversation_ids=allowed_source_conversation_ids,
        retrieval_triggered_by="longmemeval",
        include_dream_observations=True,
    )


async def evaluate_single(
    store: MemoryStore,
    question_id: str,
    question_text: str,
    reference: str,
    category: str,
    log_retrieval: bool = False,
    allowed_source_conversation_ids: list[uuid.UUID] | None = None,
    user_id: uuid.UUID = TEST_USER_ID,
    benchmark_mode: bool = False,
) -> dict[str, Any]:
    """Evaluate a single question."""
    # Get query embedding
    query_embedding = await embed_query(question_text)

    # Retrieve memories
    memories = await retrieve_user_memories(
        store=store,
        user_id=user_id,
        query_embedding=query_embedding,
        query_text=question_text,
        limit=TOP_K_MEMORIES,
        log_retrieval=log_retrieval,
        allowed_source_conversation_ids=allowed_source_conversation_ids,
    )

    # Generate answer
    hypothesis = await answer_with_llm(question_text, memories, benchmark_mode=benchmark_mode)

    # Judge answer
    judgment = await judge_answer(
        question_text,
        hypothesis,
        reference,
        benchmark_mode=benchmark_mode,
    )

    return {
        "question_id": question_id,
        "question": question_text,
        "reference": reference,
        "hypothesis": hypothesis,
        "category": category,
        "judgment": judgment,
        "memories_used": len(memories),
    }


# --- Production-parity answer path (fact substrate) ---------------------------
#
# The fact harness measures the memory prompt surface production builds: the
# same gather/render pair as ``build_memory_context()`` and the same
# ``compose_system_prompt()`` as ``assemble_system_prompt()``. Remaining
# differences from a production chat turn are listed in
# ``orchestrator.eval.fact_harness.PARITY_EXCEPTIONS`` and copied into every
# run manifest. The legacy single-message path above stays for the chunk lane.

PARITY_PIPELINE = "cloud"  # ingest creates cloud conversations
PARITY_RETRIEVAL_TRIGGER = "longmemeval"  # retrieval_log label only; not a ranking input

ScopeFactory = Callable[[], AbstractAsyncContextManager[Any]]


async def build_parity_memory_context(
    store: MemoryStore,
    *,
    user_id: uuid.UUID,
    question_text: str,
    allowed_source_conversation_ids: list[uuid.UUID] | None,
    log_retrieval: bool = False,
) -> tuple[str, MemoryContextInputs]:
    """Gather and render the memory block exactly as production does for a turn.

    The question stands in for the latest user message. Evaluation is read-only:
    trust recording and access touches are disabled.
    """
    inputs = await gather_memory_context_inputs(
        store,
        user_id=user_id,
        pipeline=PARITY_PIPELINE,
        query_text=question_text,
        allowed_source_conversation_ids=allowed_source_conversation_ids,
        record_side_effects=False,
        log_retrieval=log_retrieval,
        retrieval_triggered_by=PARITY_RETRIEVAL_TRIGGER,
    )
    return render_memory_context(inputs), inputs


def build_parity_answer_messages(question_text: str, memory_context: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": compose_system_prompt(memory_context)},
        {"role": "user", "content": question_text},
    ]


def _usage_from_response(response: Any) -> dict[str, int]:
    usage: Any = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}
    if not isinstance(usage, dict):
        model_dump = getattr(usage, "model_dump", None)
        usage = model_dump() if callable(model_dump) else dict(getattr(usage, "__dict__", {}))
    return {
        key: int(usage[key])
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if isinstance(usage.get(key), int)
    }


async def _answer_with_messages(
    messages: list[dict[str, str]],
    *,
    benchmark_mode: bool,
) -> tuple[str, dict[str, int]]:
    response = await _call_llm_with_provider_config(
        model=ANSWER_MODEL,
        messages=messages,
        temperature=ANSWER_TEMPERATURE,
        max_tokens=ANSWER_MAX_TOKENS,
        bm_call_key="answer" if benchmark_mode else None,
    )
    if response is None:
        return "", {}
    return parse_answer(_extract_content(response)), _usage_from_response(response)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def evaluate_single_parity(
    store: MemoryStore,
    question_id: str,
    question_text: str,
    reference: str,
    category: str,
    *,
    allowed_source_conversation_ids: list[uuid.UUID] | None,
    log_retrieval: bool = False,
    user_id: uuid.UUID = TEST_USER_ID,
    benchmark_mode: bool = True,
    scope_factory: ScopeFactory | None = None,
) -> dict[str, Any]:
    """Evaluate one question through the production memory prompt surface.

    ``scope_factory`` opens the account compute scope a production chat turn
    runs in; only the qualified embedding route needs it.
    """
    scope = scope_factory() if scope_factory is not None else nullcontext()
    async with scope:
        memory_context, inputs = await build_parity_memory_context(
            store,
            user_id=user_id,
            question_text=question_text,
            allowed_source_conversation_ids=allowed_source_conversation_ids,
            log_retrieval=log_retrieval,
        )
    messages = build_parity_answer_messages(question_text, memory_context)
    hypothesis, answer_usage = await _answer_with_messages(messages, benchmark_mode=benchmark_mode)
    answer_fingerprint = last_benchmark_fingerprint("answer") if benchmark_mode else None
    answer_metadata = get_benchmark_tracking().get("answer", {}) if benchmark_mode else {}
    judgment, judge_usage = await _judge_with_usage(
        question_text,
        hypothesis,
        reference,
        benchmark_mode=benchmark_mode,
    )
    judge_fingerprint = last_benchmark_fingerprint("judge") if benchmark_mode else None
    judge_metadata = get_benchmark_tracking().get("judge", {}) if benchmark_mode else {}
    question_type = {value: key for key, value in CATEGORY_MAP.items()}.get(category, category)
    result = {
        "question_id": question_id,
        "question": question_text,
        "reference": reference,
        "hypothesis": hypothesis,
        "category": category,
        "judgment": judgment,
        "memories_used": len(inputs.retrieved),
        "l0_used": len(inputs.l0_memories),
        "summaries_used": len(inputs.summaries),
        "retrieval_mode": inputs.retrieval_mode,
        "memory_context": memory_context,
        "memory_context_sha256": _sha256_text(memory_context),
        "system_prompt_sha256": _sha256_text(messages[0]["content"]),
        "answer_usage": answer_usage,
        "judge_usage": judge_usage,
        "answer_fingerprint": answer_fingerprint,
        "judge_fingerprint": judge_fingerprint,
        "answer_finish_reason": answer_metadata.get("finish_reason"),
        "answer_raw_output": answer_metadata.get("raw_output"),
        "answer_max_tokens": ANSWER_MAX_TOKENS,
        "judge_raw_output": judge_metadata.get("raw_output"),
        "judge_finish_reason": judge_metadata.get("finish_reason"),
        "question_type": question_type,
    }
    try:
        official = await judge_answer_official(
            question_text,
            hypothesis,
            reference,
            question_id=question_id,
            question_type=question_type,
            benchmark_mode=benchmark_mode,
        )
    except Exception as exc:
        # The additive metric must not erase or invalidate paid base evidence.
        # No top-level error: canonical re-entry must retain this generic result,
        # not repeat retrieval/reader/generic calls to obtain a missing label.
        result.update(
            official_judge_status="failed",
            official_judge_failure_diagnostics=benchmark_failure_diagnostics(exc),
            official_judge_usage=None,  # unknown, not a zero-cost observation
        )
    else:
        result.update(official)
        result["official_judge_status"] = "completed"
    return result


def score_accuracy(results: list[dict[str, Any]]) -> dict[str, float]:
    category_scores: dict[str, dict[str, int]] = {
        cat: {"correct": 0, "total": 0} for cat in ACCURACY_CATEGORIES
    }

    for result in results:
        category = result.get("category", "IE-user")
        if category not in category_scores:
            continue

        judgment = result.get("judgment", "incorrect")
        category_scores[category]["total"] += 1
        if judgment == "correct":
            category_scores[category]["correct"] += 1

    accuracy: dict[str, float] = {}
    for cat in ACCURACY_CATEGORIES:
        scores = category_scores[cat]
        if scores["total"] > 0:
            accuracy[cat] = scores["correct"] / scores["total"]
        else:
            accuracy[cat] = 0.0

    return accuracy


def print_results(results: list[dict[str, Any]], accuracy: dict[str, float]) -> None:
    print("\n" + "=" * 80)
    print("EVALUATION RESULTS")
    print("=" * 80)

    print("\nCategory Accuracy:")
    print("-" * 40)
    for cat in ACCURACY_CATEGORIES:
        name = CATEGORY_NAMES.get(cat, cat)
        acc = accuracy.get(cat, 0.0)
        print(f"  {name}: {acc:.1%}")

    print("\nDetailed Results:")
    print("-" * 40)
    for result in results:
        qid = result["question_id"]
        category = result["category"]
        judgment = result["judgment"]
        hypothesis = result["hypothesis"][:60]

        status = "✓" if judgment == "correct" else "✗" if judgment == "incorrect" else "~"
        print(f"{status} [{category}] {qid}: {judgment}")
        print(f"  Hypothesis: {hypothesis}...")


async def run_evaluation(
    dataset_path: Path,
    output_path: Path,
    checkpoint_path: Path,
    limit: int | None = None,
    force_retrieval_logging: bool = False,
) -> list[dict[str, Any]]:
    """Run the evaluation."""
    from orchestrator.memory.encryption import ContentEncryption

    settings = get_settings()
    dataset = load_dataset(dataset_path)
    checkpoint_results = load_checkpoint(
        checkpoint_path,
        dataset_path=dataset_path,
    )

    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not set")

    from orchestrator.eval.bench_db import assert_benchmark_database, check_benchmark_dsn

    check_benchmark_dsn(settings.database_url)
    # Create pool directly
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
    )

    try:
        await assert_benchmark_database(pool, settings.database_url)
        encryption = ContentEncryption(settings.daemon_encryption_key)
        store = MemoryStore(pool, encryption)

        # Process questions
        questions = dataset if limit is None else dataset[:limit]
        question_order = [
            str(entry.get("question_id", f"q{idx}")) for idx, entry in enumerate(questions)
        ]

        print(f"Evaluating {len(questions)} questions...")
        if checkpoint_results:
            print(f"Resuming from checkpoint: {len(checkpoint_results)} completed questions")
        if force_retrieval_logging:
            logger.info("LongMemEval benchmark forcing retrieval logging ON")

        for idx, entry in enumerate(questions):
            question_id = str(entry.get("question_id", f"q{idx}"))
            if question_id in checkpoint_results:
                print(f"[{idx + 1}/{len(questions)}] {question_id}... SKIP (checkpoint)")
                continue

            question_text = entry.get("question", "")
            reference = entry.get("answer", "")
            category_raw = entry.get("question_type", "single-session-user")
            category = CATEGORY_MAP.get(category_raw, "IE-user")

            print(f"[{idx + 1}/{len(questions)}] {question_id}...", end=" ", flush=True)

            try:
                result = await evaluate_single(
                    store=store,
                    question_id=question_id,
                    question_text=question_text,
                    reference=reference,
                    category=category,
                    log_retrieval=force_retrieval_logging,
                )
                checkpoint_results[question_id] = result
                print(f"{result['judgment']}")
            except Exception as e:
                print(f"ERROR: {e}")
                checkpoint_results[question_id] = {
                    "question_id": question_id,
                    "question": question_text,
                    "reference": reference,
                    "hypothesis": "",
                    "category": category,
                    "judgment": "incorrect",
                    "error": str(e),
                }

            ordered_checkpoint_results = [
                checkpoint_results[qid] for qid in question_order if qid in checkpoint_results
            ]
            save_checkpoint(
                checkpoint_path,
                dataset_path=dataset_path,
                results=ordered_checkpoint_results,
            )

        results = [checkpoint_results[qid] for qid in question_order if qid in checkpoint_results]
    finally:
        await pool.close()

    # Calculate accuracy
    accuracy = score_accuracy(results)

    # Print summary
    print_results(results, accuracy)

    # Save results
    write_results_jsonl(output_path, results)

    print(f"\nResults saved to: {output_path}")
    print(f"Checkpoint saved to: {checkpoint_path}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Run LongMemEval evaluation")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"Dataset file path (default: {DEFAULT_DATASET_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            f"Output directory for {RESULTS_FILENAME} and {CHECKPOINT_FILENAME} "
            f"(default: {DEFAULT_OUTPUT_DIR})"
        ),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help=(f"Optional checkpoint file path (default: <output-dir>/{CHECKPOINT_FILENAME})"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of questions to evaluate",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    output_path, checkpoint_path = resolve_output_paths(
        output_dir=args.output_dir,
        checkpoint_path=args.checkpoint,
    )
    results = asyncio.run(
        run_evaluation(
            dataset_path=args.dataset,
            output_path=output_path,
            checkpoint_path=checkpoint_path,
            limit=args.limit,
            force_retrieval_logging=True,
        )
    )

    # Final summary
    accuracy = score_accuracy(results)
    print("\n" + "=" * 80)
    print("FINAL ACCURACY")
    print("=" * 80)
    for cat in ACCURACY_CATEGORIES:
        name = CATEGORY_NAMES.get(cat, cat)
        acc = accuracy.get(cat, 0.0)
        print(f"  {name}: {acc:.1%}")


if __name__ == "__main__":
    main()
