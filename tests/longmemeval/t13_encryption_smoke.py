#!/usr/bin/env python3
"""
T13 — Encryption Smoke Test

Verifies that encrypted columns in the Daemon database can be decrypted
and yield valid UTF-8 content.

Targets:
  1. messages.content
  2. memories.content
  3. memory_extraction_log.input_snippet

This script is READ ONLY — no schema or data modifications.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from typing import Any
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orchestrator.memory.encryption import ContentEncryption

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

MIN_GENUINE_DECRYPT = 20
FETCH_LIMIT = 100


@dataclass
class TableResult:
    table: str
    column: str
    total_rows: int
    fernet_rows: int
    genuine_decrypts: int
    plaintext_rows: int
    cipher_unavailable: int
    empty_rows: int
    empty_decrypted: int
    invalid_token: int
    utf8_error: int
    other_error: int
    failures: list[dict[str, str]]
    halt: bool
    halt_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "column": self.column,
            "total_rows": self.total_rows,
            "fernet_rows": self.fernet_rows,
            "genuine_decrypts": self.genuine_decrypts,
            "plaintext_rows": self.plaintext_rows,
            "cipher_unavailable": self.cipher_unavailable,
            "empty_rows": self.empty_rows,
            "empty_decrypted": self.empty_decrypted,
            "invalid_token": self.invalid_token,
            "utf8_error": self.utf8_error,
            "other_error": self.other_error,
            "failures": self.failures,
            "halt": self.halt,
            "halt_reason": self.halt_reason,
        }


def _looks_like_fernet(ciphertext: str) -> bool:
    if not isinstance(ciphertext, str) or len(ciphertext) < 20:
        return False
    return ciphertext[0] == "g"


def _safe_db_url_shape(url: str) -> str:
    if not url:
        return "<not set>"
    redacted = re.sub(r"://([^@]+)@", r"://<user:pass>@", url)
    return redacted


def _try_decrypt(enc: ContentEncryption, ciphertext: str) -> tuple[str | None, str]:
    if not ciphertext:
        return None, "empty"

    if not _looks_like_fernet(ciphertext):
        return ciphertext, "plaintext"

    if enc._cipher is None:
        return None, "cipher_unavailable"

    try:
        decrypted = enc.decrypt(ciphertext)
        try:
            decrypted.encode("utf-8").decode("utf-8")
        except UnicodeError:
            return None, "utf8_error"
        if not decrypted or not decrypted.strip():
            return None, "empty_decrypted"
        return decrypted, "decrypted"
    except ValueError as e:
        if "Invalid ciphertext" in str(e) or "wrong key" in str(e).lower():
            return None, "invalid_token"
        return None, f"other_error:{e}"
    except Exception as e:
        return None, f"other_error:{e}"


async def _sample_fernet_rows(
    pool: asyncpg.Pool,
    table: str,
    content_column: str,
    limit: int,
) -> list[asyncpg.Record]:
    """
    Sample rows that look like Fernet ciphertext (start with 'g', len >= 20).
    This ensures we only test rows that are genuinely encrypted.
    """
    return await pool.fetch(
        f"""
        SELECT id, {content_column} as content, created_at
        FROM {table}
        WHERE {content_column} IS NOT NULL
          AND {content_column} != ''
          AND SUBSTRING({content_column}, 1, 1) = 'g'
          AND LENGTH({content_column}) >= 20
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )


async def _assess_table(
    pool: asyncpg.Pool,
    enc: ContentEncryption,
    table: str,
    content_column: str,
    required_decrypts: int,
) -> TableResult:
    rows = await _sample_fernet_rows(pool, table, content_column, FETCH_LIMIT)

    fernet_rows = len(rows)
    genuine_decrypts = 0
    plaintext_rows = 0
    cipher_unavailable = 0
    empty_rows = 0
    empty_decrypted = 0
    invalid_token = 0
    utf8_error = 0
    other_error = 0
    failures: list[dict[str, str]] = []

    for row in rows:
        ct = row["content"]
        _, status = _try_decrypt(enc, ct)
        row_id = str(row["id"])

        if status == "decrypted":
            genuine_decrypts += 1
        elif status == "plaintext":
            plaintext_rows += 1
        elif status == "cipher_unavailable":
            cipher_unavailable += 1
            failures.append({"id": row_id, "status": status, "col": f"{table}.{content_column}"})
        elif status == "empty":
            empty_rows += 1
            failures.append({"id": row_id, "status": status, "col": f"{table}.{content_column}"})
        elif status == "empty_decrypted":
            empty_decrypted += 1
            failures.append({"id": row_id, "status": status, "col": f"{table}.{content_column}"})
        elif status == "invalid_token":
            invalid_token += 1
            failures.append({"id": row_id, "status": status, "col": f"{table}.{content_column}"})
        elif status == "utf8_error":
            utf8_error += 1
            failures.append({"id": row_id, "status": status, "col": f"{table}.{content_column}"})
        else:
            other_error += 1
            failures.append({"id": row_id, "status": status, "col": f"{table}.{content_column}"})

    halt = False
    halt_reason = None

    critical_failures = cipher_unavailable + invalid_token + utf8_error + other_error + empty_decrypted
    if critical_failures > 0:
        halt = True
        halt_reason = (
            f"HALT: {table}.{content_column} has {critical_failures} critical decrypt failures "
            f"(cipher_unavailable={cipher_unavailable}, invalid_token={invalid_token}, "
            f"utf8_error={utf8_error}, empty_decrypted={empty_decrypted}, other_error={other_error}); "
            f"encryption smoke FAILED"
        )
    elif fernet_rows < required_decrypts:
        halt = True
        halt_reason = (
            f"HALT: {table}.{content_column} has only {fernet_rows} Fernet-looking rows "
            f"(sampled up to {FETCH_LIMIT} rows); need >= {required_decrypts} "
            f"to satisfy 'Decrypt at least 20 sampled rows' acceptance"
        )

    return TableResult(
        table=table,
        column=content_column,
        total_rows=fernet_rows,
        fernet_rows=fernet_rows,
        genuine_decrypts=genuine_decrypts,
        plaintext_rows=plaintext_rows,
        cipher_unavailable=cipher_unavailable,
        empty_rows=empty_rows,
        empty_decrypted=empty_decrypted,
        invalid_token=invalid_token,
        utf8_error=utf8_error,
        other_error=other_error,
        failures=failures,
        halt=halt,
        halt_reason=halt_reason,
    )


async def run_smoke() -> dict[str, Any]:
    load_dotenv()

    raw_db_url = os.environ.get("DATABASE_URL", "")
    enc_key = os.environ.get("DAEMON_ENCRYPTION_KEY", "")

    resolved_db_url = raw_db_url
    if raw_db_url and "postgres" in raw_db_url:
        logger.info("Detected Docker hostname 'postgres' — attempting 127.0.0.1 override")
        resolved_db_url = re.sub(r"@([^:/]+):", r"@127.0.0.1:", resolved_db_url)
        resolved_db_url = re.sub(r"@([^:/]+)/", r"@127.0.0.1/", resolved_db_url)

    enc = ContentEncryption(key=enc_key)
    cipher_ready = enc._cipher is not None

    results: dict[str, Any] = {
        "db_url_shape": _safe_db_url_shape(resolved_db_url),
        "cipher_ready": cipher_ready,
        "encryption_key_loaded": bool(enc_key),
        "targets": [],
        "overall_pass": False,
        "halt": False,
        "halt_reason": None,
    }

    logger.info(f"Connecting to database (shape: {_safe_db_url_shape(resolved_db_url)})...")
    pool = await asyncpg.create_pool(
        resolved_db_url,
        min_size=1,
        max_size=4,
        command_timeout=60,
    )

    try:
        msg_result = await _assess_table(pool, enc, "messages", "content", MIN_GENUINE_DECRYPT)
        results["targets"].append(msg_result.to_dict())

        mem_result = await _assess_table(pool, enc, "memories", "content", MIN_GENUINE_DECRYPT)
        results["targets"].append(mem_result.to_dict())

        log_result = await _assess_table(pool, enc, "memory_extraction_log", "input_snippet", MIN_GENUINE_DECRYPT)
        results["targets"].append(log_result.to_dict())

        any_halt = False
        halt_reason = None
        for tgt in results["targets"]:
            if tgt["halt"]:
                any_halt = True
                halt_reason = tgt["halt_reason"]
                break

        results["halt"] = any_halt
        results["halt_reason"] = halt_reason
        results["overall_pass"] = not any_halt

    finally:
        await pool.close()

    return results


async def main() -> None:
    results = await run_smoke()

    print("\n" + "=" * 60)
    print("T13 — Encryption Smoke Results")
    print("=" * 60)
    print(f"DB URL shape:   {results['db_url_shape']}")
    print(f"Cipher ready:   {results['cipher_ready']}")
    print(f"Key loaded:    {results['encryption_key_loaded']}")
    print()

    for tgt in results["targets"]:
        t_name = f"{tgt['table']}.{tgt['column']}"
        print(f"  [{t_name}]")
        print(f"    total Fernet rows:   {tgt['fernet_rows']}")
        print(f"    genuine_decrypts:   {tgt['genuine_decrypts']}")
        print(f"    plaintext_rows:     {tgt['plaintext_rows']}")
        print(f"    cipher_unavailable: {tgt['cipher_unavailable']}")
        print(f"    empty_rows:         {tgt['empty_rows']}")
        print(f"    empty_decrypted:    {tgt['empty_decrypted']}")
        print(f"    invalid_token:      {tgt['invalid_token']}")
        print(f"    utf8_error:        {tgt['utf8_error']}")
        print(f"    other_error:       {tgt['other_error']}")
        print(f"    halt:              {tgt['halt']}")
        if tgt["halt_reason"]:
            print(f"    halt_reason:        {tgt['halt_reason']}")
        if tgt["failures"]:
            print(f"    FAILURES ({len(tgt['failures'])}):")
            for f_item in tgt["failures"][:5]:
                print(f"      - row={f_item['id']} status={f_item['status']} col={f_item['col']}")
            if len(tgt["failures"]) > 5:
                print(f"      ... and {len(tgt['failures']) - 5} more")
        print()

    print(f"Overall pass: {results['overall_pass']}")
    if results["halt"]:
        print(f"HALT: {results['halt_reason']}")
    print("=" * 60)

    out_path = Path("/home/sol/daemon/.sisyphus/evidence/task-13-encryption-smoke.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info(f"Results written to {out_path}")

    sys.exit(0 if results["overall_pass"] and not results["halt"] else 1)


if __name__ == "__main__":
    asyncio.run(main())