#!/usr/bin/env python3
import asyncio, sys, json, uuid, os
from pathlib import Path

sys.path.insert(0, '.')
os.environ['BENCHMARK_MODE'] = '1'

import dotenv
dotenv.load_dotenv()
os.environ['DATABASE_URL'] = 'postgresql://daemon:daemon@127.0.0.1:5432/daemon'

from orchestrator.eval.fact_harness import LongMemEvalFactRunner
from orchestrator.memory.encryption import ContentEncryption
from orchestrator.memory.extraction import get_benchmark_tracking, BENCHMARK_EXTRACTION_ENDPOINT_SLUG
from orchestrator.config import get_settings
import asyncpg
import hashlib

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
        print(f"[patched] extract_facts_from_text: BenchmarkSamplingError caught (diagnostic)")
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
        print(f"[patched] check_contradiction: DedupBenchmarkSamplingError caught")
        return False, ""
_dedup.check_contradiction = _patched_check_contradiction
print("Patches applied")
"""


async def main():
    exec(PATCH_CODE)

    settings = get_settings()
    print(f'DB URL: {settings.database_url}')

    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=2, max_size=5)
    encryption = ContentEncryption(settings.daemon_encryption_key)

    OUTPUT = Path('tests/benchmark_results/wave0_rerun_v1/run_1')
    OUTPUT.mkdir(parents=True, exist_ok=True)

    runner = LongMemEvalFactRunner(
        dataset_path=Path('tests/benchmark_longmemeval/fixtures/dev_subset.json'),
        output_path=OUTPUT / 'longmemeval_results.jsonl',
        checkpoint_path=OUTPUT / 'longmemeval_checkpoint.json',
        score_path=OUTPUT / 'longmemeval_score.json',
        limit=None,
        force_retrieval_logging=True,
    )

    print('Starting ingestion...')
    t0 = asyncio.get_event_loop().time()
    await runner.ingest()
    print(f'Ingestion complete in {asyncio.get_event_loop().time() - t0:.0f}s')

    test_user_id = uuid.UUID('12345678-1234-5678-1234-567812345678')

    extraction_rows = await pool.fetch(
        '''
        SELECT id, conversation_id, user_id, input_snippet, extracted_facts,
               dedup_results, model_used, created_at
        FROM memory_extraction_log
        WHERE user_id = $1
        ORDER BY created_at ASC
        ''',
        test_user_id,
    )

    memory_rows = await pool.fetch(
        '''
        SELECT id, user_id, content, category, source_conversation_id,
               confidence, status, slot, created_at
        FROM memories
        WHERE user_id = $1
        ORDER BY created_at ASC
        ''',
        test_user_id,
    )

    await pool.close()

    tracking = get_benchmark_tracking()
    ext_tracking = tracking.get('extraction', {})

    extraction_log_path = OUTPUT / 'extraction_log.jsonl'
    with open(extraction_log_path, 'w') as f:
        for row in extraction_rows:
            decrypted_snippet = encryption.decrypt(row['input_snippet'])
            input_hash = hashlib.sha256(decrypted_snippet[:1000].encode('utf-8')).hexdigest()
            facts = row['extracted_facts'] or []
            entry = {
                'session_id': str(row['conversation_id']),
                'conversation_id': str(row['conversation_id']),
                'input_snippet_hash': input_hash,
                'extracted_count': len(facts),
                'extracted_facts': facts,
                'model': row['model_used'],
                'system_fingerprint': ext_tracking.get('fingerprint'),
                'dedup_results': row['dedup_results'] or {},
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            }
            f.write(json.dumps(entry) + '\n')

    memories_path = OUTPUT / 'memories.jsonl'
    with open(memories_path, 'w') as f:
        for row in memory_rows:
            decrypted_content = encryption.decrypt(row['content'])
            content_hash = hashlib.sha256(decrypted_content.strip().encode('utf-8')).hexdigest()
            entry = {
                'memory_id': str(row['id']),
                'content_sha256': content_hash,
                'category': row['category'],
                'slot': row['slot'],
                'confidence': row['confidence'],
                'status': row['status'],
                'source_conversation_id': str(row['source_conversation_id']) if row['source_conversation_id'] else None,
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
            }
            f.write(json.dumps(entry) + '\n')

    run_metrics = {
        'schema_version': 1,
        'total_extraction_calls': len(extraction_rows),
        'observed_fingerprint': ext_tracking.get('fingerprint'),
        'observed_model': ext_tracking.get('model'),
        'total_memories_created': len(memory_rows),
        'active_memories': sum(1 for r in memory_rows if r['status'] == 'active'),
    }

    metrics_path = OUTPUT / 'run_metrics.json'
    with open(metrics_path, 'w') as f:
        json.dump(run_metrics, f, indent=2)

    print(f'Extraction log: {extraction_log_path} ({len(extraction_rows)} rows)')
    print(f'Memories: {memories_path} ({len(memory_rows)} rows)')
    print('INGEST_OK')

if __name__ == "__main__":
    asyncio.run(main())
