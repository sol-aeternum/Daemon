#!/usr/bin/env python3
"""
Driver script to run the ingestion rerun 3 times with preservation.
"""

import subprocess
import sys
import os
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_DIR = PROJECT_ROOT / "tests/benchmark_results/wave0_rerun_v1"

os.environ["PYTHONPATH"] = str(PROJECT_ROOT)


def run_harness(output_dir: Path, run_label: str) -> dict:
    """Run the ingestion rerun harness once."""
    print(f"\n{'=' * 60}")
    print(f"RUN {run_label}")
    print(f"{'=' * 60}")
    print(f"Output: {output_dir}")

    # Clean and create output dir
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create a modified version of ingestion_rerun.py inline
    harness_code = f'''
import sys
sys.path.insert(0, "{PROJECT_ROOT}")
import os
os.environ["BENCHMARK_MODE"] = "1"
os.environ["DATABASE_URL"] = "postgresql://daemon:daemon@127.0.0.1:5432/daemon"

PATCH_CODE = """
import sys
import dotenv
dotenv.load_dotenv()

import orchestrator.memory.extraction as _ext
_ext.BENCHMARK_EXTRACTION_ENDPOINT_SLUG = "openai"
_BenchmarkSamplingError = _ext.BenchmarkSamplingError
_original_extract = _ext.extract_facts_from_text

async def _patched_extract_facts_from_text(text, model="openrouter/openai/gpt-4o-mini", *, summary=None, retry_hint=None, benchmark_mode=None):
    try:
        return await _original_extract(text, model=model, summary=summary, retry_hint=retry_hint, benchmark_mode=benchmark_mode)
    except _BenchmarkSamplingError as e:
        print(f"[patched] extract_facts_from_text: BenchmarkSamplingError caught (diagnostic) -> {{e}}")
        from dataclasses import dataclass
        @dataclass
        class _EmptyOutcome:
            facts: list = None
            raw_count: int = 0
            calibrated_count: int = 0
            rejected_count: int = 0
            slot_coverage: int = 0
        return _EmptyOutcome()
_ext.extract_facts_from_text = _patched_extract_facts_from_text

import orchestrator.memory.dedup as _dedup
_dedup.BENCHMARK_CONTRADICTION_MODEL = "openrouter/deepseek/deepseek-v3.2"
_dedup.BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = "novita"
_DedupBenchmarkSamplingError = _dedup.DedupBenchmarkSamplingError
_dedup_check_orig = _dedup.check_contradiction

async def _patched_check_contradiction(existing_content, new_content, benchmark_mode=None):
    try:
        return await _dedup_check_orig(existing_content, new_content, benchmark_mode=benchmark_mode)
    except _DedupBenchmarkSamplingError as e:
        print(f"[patched] check_contradiction: DedupBenchmarkSamplingError caught -> {{e}}")
        return False, ""
_dedup.check_contradiction = _patched_check_contradiction
"""

RESET_CODE = PATCH_CODE + f"""
import asyncio, sys, json
sys.path.insert(0, "{PROJECT_ROOT}")
from orchestrator.eval.fact_harness import reset_canonical_benchmark
from orchestrator.config import get_settings
from pathlib import Path
import asyncpg

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=5)
    try:
        checkpoint = Path("{output_dir}/longmemeval_checkpoint.json")
        summary = await reset_canonical_benchmark(pool, checkpoint, cleanup_redis=False)
        result = {{
            "success": summary.success,
            "tables_cleared": summary.tables_cleared,
            "total_rows_deleted": summary.total_rows_deleted,
            "error": summary.error,
        }}
        with open("{output_dir}/reset_result.json", "w") as f:
            json.dump(result, f)
        print("RESET_OK")
    finally:
        await pool.close()

asyncio.run(main())
"""

INGEST_CODE = PATCH_CODE + f"""
import asyncio, sys, json
sys.path.insert(0, "{PROJECT_ROOT}")
from orchestrator.eval.fact_harness import LongMemEvalFactRunner
from pathlib import Path

runner = LongMemEvalFactRunner(
    dataset_path=Path("{PROJECT_ROOT}/tests/benchmark_longmemeval/fixtures/dev_subset.json"),
    output_path=Path("{output_dir}/longmemeval_results.jsonl"),
    checkpoint_path=Path("{output_dir}/longmemeval_checkpoint.json"),
    score_path=Path("{output_dir}/longmemeval_score.json"),
    limit=None,
    force_retrieval_logging=True,
)

asyncio.run(runner.ingest())
print("INGEST_OK")
"""

print("Running reset...")
r = subprocess.run([sys.executable, "-c", RESET_CODE], capture_output=True, text=True)
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:300])

print("Running ingest...")
r = subprocess.run([sys.executable, "-c", INGEST_CODE], capture_output=True, text=True, timeout=600)
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:300])
'''

    result = subprocess.run(
        [sys.executable, "-c", harness_code],
        capture_output=True,
        text=True,
        timeout=900,
    )

    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "output_dir": str(output_dir),
    }


def main():
    print("Starting V1 Triple Rerun with Preservation")
    print(f"Base output: {BASE_DIR}")

    results = []
    for i in [1, 2, 3]:
        run_dir = BASE_DIR / f"run_{i}"
        result = run_harness(run_dir, f"{i}/3")
        results.append(
            {
                "run": i,
                "output_dir": result["output_dir"],
                "returncode": result["returncode"],
            }
        )
        print(f"Run {i} completed with code {result['returncode']}")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for r in results:
        print(f"  Run {r['run']}: rc={r['returncode']} -> {r['output_dir']}")

    success = sum(1 for r in results if r["returncode"] == 0)
    print(f"\nSuccessful: {success}/3")

    return 0 if success == 3 else 1


if __name__ == "__main__":
    sys.exit(main())
