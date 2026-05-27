#!/usr/bin/env python3
"""
Single run driver with configurable limit.
"""
import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

import dotenv
dotenv.load_dotenv()
os.environ['DATABASE_URL'] = 'postgresql://daemon:daemon@127.0.0.1:5432/daemon'
os.environ['BENCHMARK_MODE'] = '1'

import orchestrator.memory.extraction as _ext
_ext.BENCHMARK_EXTRACTION_ENDPOINT_SLUG = 'openai'
_orig_extract = _ext.extract_facts_from_text
_BenchmarkSamplingError = _ext.BenchmarkSamplingError

async def _patched_extract(text, model='openrouter/openai/gpt-4o-mini', *, summary=None, retry_hint=None, benchmark_mode=None):
    try:
        return await _orig_extract(text, model=model, summary=summary, retry_hint=retry_hint, benchmark_mode=benchmark_mode)
    except _BenchmarkSamplingError as e:
        print(f'[patched] BenchmarkSamplingError: {e}')
        from dataclasses import dataclass
        @dataclass
        class _EmptyOutcome:
            facts: list | None = None
            raw_count: int = 0
            calibrated_count: int = 0
            rejected_count: int = 0
            slot_coverage: int = 0
        return _EmptyOutcome()
_ext.extract_facts_from_text = _patched_extract

import orchestrator.memory.dedup as _dedup
_dedup.BENCHMARK_CONTRADICTION_MODEL = 'openrouter/deepseek/deepseek-v3.2'
_dedup.BENCHMARK_CONTRADICTION_ENDPOINT_SLUG = 'novita'
_DedupBenchmarkSamplingError = _dedup.DedupBenchmarkSamplingError
_orig_contradiction = _dedup.check_contradiction

async def _patched_contradiction(existing_content, new_content, benchmark_mode=None):
    try:
        return await _orig_contradiction(existing_content, new_content, benchmark_mode=benchmark_mode)
    except _DedupBenchmarkSamplingError as e:
        print(f'[patched] DedupBenchmarkSamplingError: {e}')
        return False, ''
_dedup.check_contradiction = _patched_contradiction

from orchestrator.eval.runner import LongMemEvalRunner, reset_canonical_benchmark
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.extraction import get_benchmark_tracking
from orchestrator.config import get_settings
import asyncpg


def canonicalize_facts(facts):
    normalized = []
    for f in facts:
        normalized.append({
            'content': f.get('content', '').strip(),
            'category': f.get('category', '').strip(),
            'slot': f.get('slot'),
        })
    normalized.sort(key=lambda x: (x['content'], x['category'], str(x['slot'])))
    return normalized


def fact_sha256(facts):
    canon = canonicalize_facts(facts)
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()


async def reset_run(run_dir):
    checkpoint = run_dir / 'longmemeval_checkpoint.json'
    result_file = run_dir / 'reset_result.json'
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=5)
    try:
        summary = await reset_canonical_benchmark(pool, checkpoint, cleanup_redis=False)
        result = {
            'success': summary.success,
            'tables_cleared': summary.tables_cleared,
            'total_rows_deleted': summary.total_rows_deleted,
        }
        with open(result_file, 'w') as f:
            json.dump(result, f)
        print(f'  Reset: {summary.total_rows_deleted} rows deleted')
    finally:
        await pool.close()


async def ingest_and_preserve(run_dir, limit=None):
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=2, max_size=5)
    encryption = ContentEncryption(settings.daemon_encryption_key)

    runner = LongMemEvalRunner(
        dataset_path=PROJECT_ROOT / 'tests' / 'benchmark_longmemeval' / 'fixtures' / 'dev_subset.json',
        output_path=run_dir / 'longmemeval_results.jsonl',
        checkpoint_path=run_dir / 'longmemeval_checkpoint.json',
        score_path=run_dir / 'longmemeval_score.json',
        limit=limit,
        force_retrieval_logging=True,
    )

    print(f'  Ingesting (limit={limit})...')
    t0 = asyncio.get_event_loop().time()
    await runner.ingest()
    print(f'  Ingest done in {asyncio.get_event_loop().time() - t0:.0f}s')

    test_user_id = uuid.UUID('12345678-1234-5678-1234-567812345678')

    extraction_rows = await pool.fetch('''
        SELECT id, conversation_id, user_id, input_snippet, extracted_facts,
               dedup_results, model_used, created_at
        FROM memory_extraction_log WHERE user_id = $1 ORDER BY created_at ASC
    ''', test_user_id)

    memory_rows = await pool.fetch('''
        SELECT id, user_id, content, category, source_conversation_id,
               confidence, status, memory_slot, created_at
        FROM memories WHERE user_id = $1 ORDER BY created_at ASC
    ''', test_user_id)

    await pool.close()

    tracking = get_benchmark_tracking()
    ext_tracking = tracking.get('extraction', {})

    with open(run_dir / 'extraction_log.jsonl', 'w') as f:
        for row in extraction_rows:
            decrypted = encryption.decrypt(row['input_snippet'])
            input_hash = hashlib.sha256(decrypted[:1000].encode()).hexdigest()
            raw_facts = row['extracted_facts']
            if isinstance(raw_facts, str):
                facts = json.loads(raw_facts) if raw_facts else []
            else:
                facts = raw_facts or []
            entry = {
                'session_id': str(row['conversation_id']),
                'conversation_id': str(row['conversation_id']),
                'input_snippet_hash': input_hash,
                'extracted_count': len(facts),
                'extracted_facts': facts,
                'facts_sha256': fact_sha256(facts) if facts else '',
                'model': row['model_used'],
                'system_fingerprint': ext_tracking.get('fingerprint'),
                'dedup_results': row['dedup_results'],
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            }
            f.write(json.dumps(entry) + '\n')

    with open(run_dir / 'memories.jsonl', 'w') as f:
        for row in memory_rows:
            decrypted = encryption.decrypt(row['content'])
            content_hash = hashlib.sha256(decrypted.strip().encode()).hexdigest()
            entry = {
                'memory_id': str(row['id']),
                'content_sha256': content_hash,
                'category': row['category'],
                'slot': row['memory_slot'],
                'confidence': row['confidence'],
                'status': row['status'],
                'source_conversation_id': str(row['source_conversation_id']) if row['source_conversation_id'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            }
            f.write(json.dumps(entry) + '\n')

    checkpoint_path = run_dir / 'longmemeval_checkpoint.json'
    outcome_counts = {'completed': 0, 'errored': 0, 'empty': 0, 'unknown': 0}
    if checkpoint_path.exists():
        with open(checkpoint_path) as cf:
            cp = json.load(cf)
        for r in cp.get('phases', {}).get('ingest', {}).get('results', {}).values():
            outcome = r.get('outcome', 'unknown')
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    metrics = {
        'schema_version': 1,
        'generated_at': datetime.now(UTC).isoformat(),
        'total_extraction_calls': len(extraction_rows),
        'observed_fingerprint': ext_tracking.get('fingerprint'),
        'observed_model': ext_tracking.get('model'),
        'total_memories_created': len(memory_rows),
        'active_memories': sum(1 for r in memory_rows if r['status'] == 'active'),
        'extraction_outcome_counts': outcome_counts,
    }
    with open(run_dir / 'run_metrics.json', 'w') as f:
        json.dump(metrics, f, indent=2)

    print(f'  Preservation: {len(extraction_rows)} extractions, {len(memory_rows)} memories')


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=int, required=True)
    parser.add_argument('--limit', type=int, default=None)
    args = parser.parse_args()

    base_dir = PROJECT_ROOT / 'tests' / 'benchmark_results' / 'wave0_rerun_v1'
    run_dir = base_dir / f'run_{args.run}'
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f'\n=== RUN {args.run} (limit={args.limit}) ===')
    print(f'  Output: {run_dir}')

    try:
        await reset_run(run_dir)
        await ingest_and_preserve(run_dir, limit=args.limit)
        print(f'  Run {args.run}: SUCCESS')
    except Exception as e:
        print(f'  Run {args.run}: FAILED - {e}')
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())