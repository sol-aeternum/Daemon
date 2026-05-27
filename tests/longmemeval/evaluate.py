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
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

import asyncpg
from orchestrator.memory.embedding import embed_query
from orchestrator.config import get_settings
from orchestrator.memory.retrieval import retrieve_memories_for_text
from orchestrator.memory.store import MemoryStore
from tests.longmemeval.ingest import DATASET_PATH as DEFAULT_DATASET_PATH

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

logger = logging.getLogger(__name__)


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


async def judge_answer(question_text: str, hypothesis: str, reference: str) -> str:
    prompt = f"""You are judging whether an AI assistant's answer is factually correct.

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

    response = await _call_llm_with_provider_config(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=JUDGE_TEMPERATURE,
        max_tokens=JUDGE_MAX_TOKENS,
    )

    if response is None:
        return "incorrect"

    content = _extract_content(response).strip()
    first_line = content.split("\n")[0].strip().upper()

    if first_line == "CORRECT":
        return "correct"
    elif first_line == "PARTIAL":
        return "partially_correct"
    elif first_line == "INCORRECT":
        return "incorrect"

    content_lower = content.lower()
    if "incorrect" in content_lower:
        return "incorrect"
    elif "partial" in content_lower:
        return "partially_correct"
    elif "correct" in content_lower:
        return "correct"
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
    hypothesis = await answer_with_llm(question_text, memories)

    # Judge answer
    judgment = await judge_answer(question_text, hypothesis, reference)

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

        status = (
            "✓" if judgment == "correct" else "✗" if judgment == "incorrect" else "~"
        )
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

    # Create pool directly
    pool = await asyncpg.create_pool(
        dsn=settings.database_url,
        min_size=2,
        max_size=10,
    )

    try:
        encryption = ContentEncryption(settings.daemon_encryption_key or "")
        store = MemoryStore(pool, encryption)

        # Process questions
        questions = dataset if limit is None else dataset[:limit]
        question_order = [
            str(entry.get("question_id", f"q{idx}"))
            for idx, entry in enumerate(questions)
        ]

        print(f"Evaluating {len(questions)} questions...")
        if checkpoint_results:
            print(
                f"Resuming from checkpoint: {len(checkpoint_results)} completed questions"
            )
        if force_retrieval_logging:
            logger.info("LongMemEval benchmark forcing retrieval logging ON")

        for idx, entry in enumerate(questions):
            question_id = str(entry.get("question_id", f"q{idx}"))
            if question_id in checkpoint_results:
                print(
                    f"[{idx + 1}/{len(questions)}] {question_id}... SKIP (checkpoint)"
                )
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
                checkpoint_results[qid]
                for qid in question_order
                if qid in checkpoint_results
            ]
            save_checkpoint(
                checkpoint_path,
                dataset_path=dataset_path,
                results=ordered_checkpoint_results,
            )

        results = [
            checkpoint_results[qid]
            for qid in question_order
            if qid in checkpoint_results
        ]
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
        help=(
            "Optional checkpoint file path "
            f"(default: <output-dir>/{CHECKPOINT_FILENAME})"
        ),
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
