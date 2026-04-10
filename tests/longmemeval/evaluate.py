"""LongMemEval retrieval + answering adapter.

Loads LongMemEval questions, retrieves relevant memories from Daemon's
memory store, and uses GPT-4o to generate answers in LongMemEval's
expected JSONL format.

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
    python tests/longmemeval/evaluate.py --limit 10
    python tests/longmemeval/evaluate.py --output /tmp/results.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import asyncpg
from orchestrator.memory.embedding import embed_query
from orchestrator.memory.retrieval import retrieve_memories
from orchestrator.memory.store import MemoryStore
from orchestrator.config import get_settings

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

DATASET_PATH = Path("/tmp/longmemeval-review/data/longmemeval_s.json")
DEFAULT_OUTPUT_PATH = Path("/tmp/longmemeval_results.jsonl")

TOP_K_MEMORIES = 5
RETRIEVAL_MIN_SIMILARITY = 0.0

ANSWER_MODEL = "openrouter/openai/gpt-4o"
ANSWER_TEMPERATURE = 0.7
ANSWER_MAX_TOKENS = 256

JUDGE_MODEL = "openrouter/openai/gpt-4o"
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 256

logger = logging.getLogger(__name__)


def _normalize_model_for_provider(model: str) -> str:
    """Normalize model ID for OpenRouter compatibility."""
    # Ensure openrouter prefix
    if not model.startswith("openrouter/"):
        model = f"openrouter/{model}"
    return model


async def _call_llm_with_provider_config(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
) -> Any | None:
    """Call LLM with proper OpenRouter provider configuration."""
    import litellm

    # Get settings and provider config
    settings = get_settings()
    provider_config = settings.get_provider_config("openrouter")

    # Normalize model
    model = _normalize_model_for_provider(model)

    # Build call parameters
    call_params: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": provider_config.timeout_s,
    }

    # Add provider-specific configuration
    if provider_config.base_url:
        call_params["api_base"] = provider_config.base_url
    if provider_config.api_key:
        call_params["api_key"] = provider_config.api_key
    if provider_config.extra_headers:
        call_params["extra_headers"] = provider_config.extra_headers

    try:
        response = await litellm.acompletion(**call_params)
        return response
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return None


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
    memories_text = "\n\n".join(
        f"- {memory.get('content', '')}" for memory in memories
    )

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


async def judge_answer(hypothesis: str, reference: str) -> str:
    prompt = f"""Given a reference answer and a hypothesis answer, determine if the hypothesis is correct, incorrect, or partially correct.

Reference Answer: {reference}

Hypothesis Answer: {hypothesis}

Is the hypothesis correct, incorrect, or partially correct given the reference? Respond with exactly one word: correct, incorrect, or partially_correct."""

    response = await _call_llm_with_provider_config(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=JUDGE_TEMPERATURE,
        max_tokens=JUDGE_MAX_TOKENS,
    )

    if response is None:
        return "incorrect"

    content = _extract_content(response).strip().lower()

    if "correct" in content and "partially" not in content:
        return "correct"
    elif "incorrect" in content:
        return "incorrect"
    elif "partially" in content:
        return "partially_correct"
    else:
        return "incorrect"


async def answer_with_llm(
    question: str,
    memories: list[dict[str, Any]],
) -> str:
    """Call GPT-4o via LiteLLM to generate an answer."""
    prompt = build_answer_prompt(question, memories)

    response = await _call_llm_with_provider_config(
        model=ANSWER_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=ANSWER_TEMPERATURE,
        max_tokens=ANSWER_MAX_TOKENS,
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
) -> list[dict[str, Any]]:
    # Get L0 memories first (always injected, no query match needed)
    l0_memories = await store.get_l0_memories(user_id)

    # Use proper retrieval path with hybrid scoring (vector + BM25 + composite)
    memories = await retrieve_memories(
        store=store,
        query_embedding=query_embedding,
        query_text=query_text,
        user_id=user_id,
        limit=limit,
    )

    # L0 memories are always prepended to results (L0 always injected)
    # Format L0 memories to match the memory structure
    formatted_l0 = []
    for memory in l0_memories:
        entry = dict(memory)
        entry["final_score"] = float("inf")  # L0 always ranked at top
        entry["source"] = "l0"
        formatted_l0.append(entry)

    # Combine L0 + retrieved memories, avoiding duplicates
    seen_ids = set()
    combined = []
    for memory in formatted_l0:
        memory_id = memory.get("id")
        if memory_id and memory_id not in seen_ids:
            combined.append(memory)
            seen_ids.add(memory_id)

    for memory in memories:
        memory_id = memory.get("id")
        if memory_id and memory_id not in seen_ids:
            combined.append(memory)
            seen_ids.add(memory_id)

    return combined


def _as_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return float(value)
    return default


def _days_since_accessed(memory: dict[str, object]) -> float:
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    accessed_at = (
        memory.get("last_accessed_at")
        or memory.get("updated_at")
        or memory.get("created_at")
    )
    if not isinstance(accessed_at, dt.datetime):
        return 1.0

    if accessed_at.tzinfo is None:
        accessed_at = accessed_at.replace(tzinfo=dt.timezone.utc)

    delta = now - accessed_at
    return max(delta.total_seconds() / 86400.0, 1.0)


def _recency_score(days: float) -> float:
    if days <= 7:
        return 1.0
    if days <= 30:
        return 0.9
    if days <= 90:
        return 0.7
    return 0.5


def _source_boost(memory: dict[str, object]) -> float:
    source_type = str(memory.get("source_type") or "").lower()
    category = str(memory.get("category") or "").lower()

    if source_type in {"project", "important"}:
        return 1.2
    if category == "project":
        return 1.1
    return 1.0


async def evaluate_single(
    store: MemoryStore,
    question_id: str,
    question_text: str,
    reference: str,
    category: str,
) -> dict[str, Any]:
    """Evaluate a single question."""
    # Get query embedding
    query_embedding = await embed_query(question_text)

    # Retrieve memories
    memories = await retrieve_user_memories(
        store=store,
        user_id=TEST_USER_ID,
        query_embedding=query_embedding,
        query_text=question_text,
        limit=TOP_K_MEMORIES,
    )

    # Generate answer
    hypothesis = await answer_with_llm(question_text, memories)

    # Judge answer
    judgment = await judge_answer(hypothesis, reference)

    return {
        "question_id": question_id,
        "question": question_text,
        "reference": reference,
        "hypothesis": hypothesis,
        "category": category,
        "judgment": judgment,
        "memories_used": len(memories),
    }


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
    output_path: Path,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Run the evaluation."""
    from orchestrator.memory.encryption import ContentEncryption

    settings = get_settings()
    
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL not set")
    
    # Create pool directly
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
    )
    
    encryption = ContentEncryption(settings.daemon_encryption_key or "")
    store = MemoryStore(pool, encryption)

    # Load dataset
    if not DATASET_PATH.exists():
        raise FileNotFoundError(f"Dataset not found: {DATASET_PATH}")

    with open(DATASET_PATH) as f:
        dataset = json.load(f)

    # Process questions
    results: list[dict[str, Any]] = []

    questions = dataset if limit is None else dataset[:limit]

    print(f"Evaluating {len(questions)} questions...")

    for idx, entry in enumerate(questions):
        question_id = entry.get("question_id", f"q{idx}")
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
            )
            results.append(result)
            print(f"{result['judgment']}")
        except Exception as e:
            print(f"ERROR: {e}")
            results.append({
                "question_id": question_id,
                "question": question_text,
                "reference": reference,
                "hypothesis": "",
                "category": category,
                "judgment": "incorrect",
                "error": str(e),
            })

    await pool.close()

    # Calculate accuracy
    accuracy = score_accuracy(results)

    # Print summary
    print_results(results, accuracy)

    # Save results
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for result in results:
            f.write(json.dumps(result) + "\n")

    print(f"\nResults saved to: {output_path}")

    return results

def main():
    parser = argparse.ArgumentParser(description="Run LongMemEval evaluation")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Output file path (default: {DEFAULT_OUTPUT_PATH})",
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

    results = asyncio.run(run_evaluation(args.output, args.limit))

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
