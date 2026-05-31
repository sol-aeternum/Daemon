#!/usr/bin/env python3
"""
Daemon Memory Extraction Benchmark v2.4

Tests the extraction pipeline across 8 scenarios covering dense facts,
ephemeral filtering, corrections/supersession, projects, hedged statements,
realistic multi-turn, explicit instructions, and adversarial noise.

Changes from v2.1:
- Fixed match_fact() to use word-boundary regex instead of substring matching.
  This eliminates the arch/march false positive in S6 where ["arch"] matched
  "User's birthday is March 15th" because "arch" is a substring of "march".
- Fixed S4 expected fact keywords: changed ["memori","promot"] (stems) to
  ["memory","active"] (whole words). Stems are incompatible with word-boundary
  matching. The extraction input says "promoted to active status" so the
  extracted fact will contain both "memory" and "active" as whole words.
- Applied word-boundary matching to noise detection for consistency.
#KS|
#PX|Changes from v2.2:
#JH|- Fixed S6 keyword: "year" → "years" (plural).
#ZV|- Fixed S4 keyword: "memory" → "memories" (plural).
#BB|- Fixed dedup slot fallback: S4 now uses slot 0 instead of slot 1.
#QZ|- Improved extraction prompt: now uses atomic fact extraction.

Changes from v2.3:
- Benchmark transcript replay is now deterministic and benchmark-only.
- Scenario messages are persisted as user turns and replayed through the real
  worker extraction path via `extract_memories(..., messages_json=...)`.
- This removes `/chat` assistant-response contamination while preserving the
  production extraction + storage + dedup pipeline under local/dev DB safety.

Key features:
- Wipes memories + extraction_log between scenarios (prevents accumulation)
- --json flag for structured output to stdout
- --no-wipe flag for debugging accumulated state
- --scenarios flag to run specific scenarios (e.g. --scenarios 3,5)
- Direct PostgreSQL connection for reliable cleanup
- Per-scenario slot inspection and timing
- Results auto-saved to tests/results/bench_{timestamp}.json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

try:
    psycopg2 = importlib.import_module("psycopg2")
except Exception:
    psycopg2 = None

try:
    asyncpg = importlib.import_module("asyncpg")
except Exception:
    asyncpg = None

try:
    redis_mod = importlib.import_module("redis")
except Exception:
    redis_mod = None

# ---------------------------------------------------------------------------
# Standalone Fernet decryption (no orchestrator dependency)
# ---------------------------------------------------------------------------

_fernet_instance: Any = None


def _load_dotenv() -> None:
    """Load key=value pairs from nearest .env file into os.environ.

    Walks up from cwd and script directory looking for .env.
    Only sets vars that aren't already in the environment.
    """
    search_dirs = [
        os.getcwd(),
        os.path.dirname(os.path.abspath(__file__)),
    ]
    # Also check parent dirs (tests/ → project root)
    for d in list(search_dirs):
        parent = os.path.dirname(d)
        if parent not in search_dirs:
            search_dirs.append(parent)

    for d in search_dirs:
        env_path = os.path.join(d, ".env")
        if os.path.isfile(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("\"'")
                    if key and key not in os.environ:
                        os.environ[key] = value
            return  # stop after first .env found


# Auto-load .env on import so DAEMON_ENCRYPTION_KEY + DATABASE_URL are available
_load_dotenv()

# ---------------------------------------------------------------------------
# Configuration (read AFTER dotenv load)
# ---------------------------------------------------------------------------

DEFAULT_WAIT = 50  # seconds to wait for extraction pipeline
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"
# Fresh benchmark user per run (isolated from stale test data)
import uuid as _uuid  # noqa: E402


def _fresh_benchmark_user() -> str:
    """Generate a fresh isolated benchmark user ID."""
    return str(_uuid.uuid4())


# Safety: hosts we'll allow DB wipe on
SAFE_HOSTS = ("localhost", "127.0.0.1", "postgres", "db", "0.0.0.0")

# Docker service names that should be rewritten to localhost when running
# the benchmark from the host (outside Docker network)
_DOCKER_HOST_REWRITES = {"postgres", "db", "database", "pgvector"}
_REDIS_HOST_REWRITES = {"redis", "cache"}


def _resolve_db_url(url: str) -> str:
    """Rewrite Docker-internal hostnames to localhost for host-side access."""
    for docker_host in _DOCKER_HOST_REWRITES:
        # Match @hostname: or @hostname/ patterns
        if f"@{docker_host}:" in url or f"@{docker_host}/" in url:
            url = url.replace(f"@{docker_host}:", "@localhost:").replace(
                f"@{docker_host}/", "@localhost/"
            )
            break
    return url


DATABASE_URL = _resolve_db_url(
    os.environ.get("DATABASE_URL", "postgresql://daemon:daemon@localhost:5432/daemon")
)
REDIS_URL_RAW = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _resolve_redis_url(url: str) -> str:
    for docker_host in _REDIS_HOST_REWRITES:
        if f"://{docker_host}:" in url or url.endswith(f"://{docker_host}"):
            url = url.replace(f"://{docker_host}:", "://localhost:")
            if url.endswith(f"://{docker_host}"):
                url = url.replace(f"://{docker_host}", "://localhost")
            break
    return url


REDIS_URL = _resolve_redis_url(REDIS_URL_RAW)


def _get_fernet() -> Any:
    """Lazy-init Fernet from DAEMON_ENCRYPTION_KEY env var."""
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance
    key = os.environ.get("DAEMON_ENCRYPTION_KEY")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
        return _fernet_instance
    except Exception:
        return None


def _decrypt_content(value: Any) -> str:
    """Attempt Fernet decryption; return raw value on failure."""
    if not isinstance(value, str):
        return str(value)
    fernet = _get_fernet()
    if fernet is None:
        return value
    try:
        return fernet.decrypt(value.encode()).decode()
    except Exception:
        return value


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _is_safe_db(db_url: str) -> bool:
    """Check that DATABASE_URL points to a local/dev database."""
    return any(h in db_url for h in SAFE_HOSTS)


def _is_safe_redis(redis_url: str) -> bool:
    return any(h in redis_url for h in SAFE_HOSTS + ("redis", "cache"))


def wipe_redis_extract_keys(redis_url: str = REDIS_URL) -> int:
    if not _is_safe_redis(redis_url):
        print(f"  ⚠ Refusing to wipe non-local redis: {redis_url}", file=sys.stderr)
        return 0

    if redis_mod is None:
        print("  ⚠ redis module unavailable; skipping redis wipe")
        return 0

    client_cls = cast(Any, redis_mod).Redis
    client = client_cls.from_url(redis_url, decode_responses=True)
    deleted = 0
    patterns = [
        "extract:*",
        "arq:job:extract:*",
        "arq:result:extract:*",
        "arq:retry:extract:*",
    ]
    for pattern in patterns:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=500)
            if keys:
                deleted += int(client.delete(*keys))
            if cursor == 0:
                break
    return deleted


def flush_redis_db(redis_url: str = REDIS_URL) -> bool:
    """Flush the entire Redis database to clear all ARQ jobs and state.

    This is more aggressive than wipe_redis_extract_keys() - it clears the entire
    Redis DB to ensure no queued extraction jobs from previous runs can complete
    during the benchmark.

    Refuses to flush non-local Redis as a safety measure.
    """
    if not _is_safe_redis(redis_url):
        print(f"  ⚠ Refusing to flush non-local redis: {redis_url}", file=sys.stderr)
        return False

    if redis_mod is None:
        print("  ⚠ redis module unavailable; skipping redis flush")
        return False

    try:
        client_cls = cast(Any, redis_mod).Redis
        client = client_cls.from_url(redis_url, decode_responses=True)
        client.flushdb()
        return True
    except Exception as e:
        print(f"  ⚠ Redis flush failed: {e}", file=sys.stderr)
        return False


def wipe_memories(db_url: str = DATABASE_URL) -> int:
    """Delete all memories and extraction_log entries. Returns count deleted.

    Refuses to wipe non-local databases as a safety measure.
    """
    if not _is_safe_db(db_url):
        print(
            f"  ⚠ Refusing to wipe non-local database: {db_url}",
            file=sys.stderr,
        )
        return 0

    # Try the actual table name — could be extraction_log or memory_extraction_log
    log_tables = ["memory_extraction_log", "extraction_log"]

    if psycopg2 is not None:
        psycopg2_mod = cast(Any, psycopg2)
        conn = psycopg2_mod.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM memories")
        (count,) = cur.fetchone()
        # Clean all messages and conversations for full isolation
        cur.execute("DELETE FROM messages")
        cur.execute("DELETE FROM conversations")
        for tbl in log_tables:
            try:
                cur.execute(f"DELETE FROM {tbl}")
                break
            except Exception:
                conn.rollback()
        cur.execute("DELETE FROM memories")
        cur.close()
        conn.close()
        return int(count)

    if asyncpg is None:
        raise RuntimeError("Neither psycopg2 nor asyncpg is available for DB wipe")

    async def _wipe_with_asyncpg() -> int:
        asyncpg_mod = cast(Any, asyncpg)
        conn = await asyncpg_mod.connect(db_url)
        try:
            count = await conn.fetchval("SELECT count(*) FROM memories")
            # Clean all messages and conversations for full isolation
            await conn.execute("DELETE FROM messages")
            await conn.execute("DELETE FROM conversations")
            for tbl in log_tables:
                try:
                    await conn.execute(f"DELETE FROM {tbl}")
                    break
                except Exception:
                    pass
            await conn.execute("DELETE FROM memories")
            return int(count or 0)
        finally:
            await conn.close()

    return asyncio.run(_wipe_with_asyncpg())


def query_memories(
    db_url: str = DATABASE_URL, conversation_id: str | None = None
) -> list[dict[str, Any]]:
    """Fetch memories ordered by creation time.

    Returns both active and closed memories for supersession verification.
    If conversation_id is provided, only returns memories from that conversation.
    """
    if conversation_id:
        query = """
            SELECT id, user_id, content, category, confidence, memory_slot,
                   source_type, valid_from, valid_to, access_count, created_at, source_conversation_id
            FROM memories
            WHERE source_conversation_id = %s
            ORDER BY created_at
        """
        query_async = """
            SELECT id, user_id, content, category, confidence, memory_slot,
                   source_type, valid_from, valid_to, access_count, created_at, source_conversation_id
            FROM memories
            WHERE source_conversation_id = $1
            ORDER BY created_at
        """
    else:
        query = """
            SELECT id, user_id, content, category, confidence, memory_slot,
                   source_type, valid_from, valid_to, access_count, created_at, source_conversation_id
            FROM memories
            ORDER BY created_at
        """
        query_async = query

    def _process_row(d: dict[str, Any]) -> dict[str, Any]:
        for key in ("valid_from", "valid_to", "created_at"):
            if d.get(key) and hasattr(d[key], "isoformat"):
                d[key] = d[key].isoformat()
        if d.get("id"):
            d["id"] = str(d["id"])
        d["content"] = _decrypt_content(d.get("content"))
        return d

    if psycopg2 is not None:
        psycopg2_mod = cast(Any, psycopg2)
        conn = psycopg2_mod.connect(db_url)
        cur = conn.cursor()
        if conversation_id:
            cur.execute(query, (conversation_id,))
        else:
            cur.execute(query)
        cols = [desc[0] for desc in cur.description]
        rows = [_process_row(dict(zip(cols, row))) for row in cur.fetchall()]
        cur.close()
        conn.close()
        return rows

    if asyncpg is None:
        raise RuntimeError("Neither psycopg2 nor asyncpg is available for DB query")

    async def _query_with_asyncpg() -> list[dict[str, Any]]:
        asyncpg_mod = cast(Any, asyncpg)
        conn = await asyncpg_mod.connect(db_url)
        try:
            if conversation_id:
                records = await conn.fetch(query_async, conversation_id)
            else:
                records = await conn.fetch(query_async)
            return [_process_row(dict(rec)) for rec in records]
        finally:
            await conn.close()

    return asyncio.run(_query_with_asyncpg())


async def _ensure_benchmark_user_async(user_id: str) -> None:
    """Ensure benchmark user exists in DB, creating if necessary."""
    import asyncpg

    db_url = _resolve_db_url(os.environ.get("DATABASE_URL", DATABASE_URL))
    conn = await asyncpg.connect(dsn=db_url)
    try:
        await conn.execute(
            """
            INSERT INTO users (id, email, name, username, preferences, created_at, updated_at)
            VALUES ($1::uuid, $2, $3, $4, $5, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
            """,
            user_id,
            f"benchmark-{user_id[:8]}@daemon.test",
            "Benchmark User",
            f"bench_{user_id[:8]}",
            "{}",
        )
    finally:
        await conn.close()


def _ensure_benchmark_user(user_id: str) -> None:
    """Synchronous wrapper for benchmark user setup."""
    asyncio.run(_ensure_benchmark_user_async(user_id))


def build_benchmark_transcript(messages: list[str]) -> list[dict[str, str]]:
    """Convert benchmark scenario messages into deterministic user-only turns."""
    return [{"role": "user", "content": content} for content in messages]


async def invoke_benchmark_extraction(
    store: Any,
    user_id: str,
    conversation_id: str,
    messages_json: list[dict[str, str]],
) -> dict[str, object]:
    """Run the production worker extraction path for benchmark replay only."""
    from orchestrator.worker.jobs import extract_memories

    return await extract_memories(
        {"store": store},
        user_id,
        conversation_id,
        messages_json=messages_json,
    )


async def replay_benchmark_conversation(
    messages: list[str],
    user_id: str,
    db_url: str,
) -> str:
    """Persist deterministic transcript turns and replay extraction synchronously.

    This is benchmark-harness-only: it avoids `/chat` so extraction measures the
    scenario transcript itself rather than stochastic assistant replies.
    """
    if asyncpg is None:
        raise RuntimeError("asyncpg is required for benchmark transcript replay")

    from orchestrator.memory.encryption import ContentEncryption
    from orchestrator.memory.store import MemoryStore

    asyncpg_mod = cast(Any, asyncpg)
    pool = await asyncpg_mod.create_pool(dsn=db_url, min_size=1, max_size=4)
    try:
        await _ensure_benchmark_user_async(user_id)
        store = MemoryStore(
            db_pool=pool,
            encryption=ContentEncryption(os.environ.get("DAEMON_ENCRYPTION_KEY")),
        )
        conversation = await store.create_conversation(_uuid.UUID(user_id), title="Benchmark")
        conversation_id = str(conversation["id"])
        conversation_uuid = _uuid.UUID(conversation_id)
        transcript_so_far: list[str] = []

        for turn_index, content in enumerate(messages, start=1):
            transcript_so_far.append(content)
            await store.insert_message(
                conversation_id=conversation_uuid,
                user_id=_uuid.UUID(user_id),
                role="user",
                content=content,
                status="completed",
                metadata={
                    "benchmark_harness": "extraction",
                    "benchmark_source": "scenario_transcript",
                    "benchmark_turn": turn_index,
                },
            )
            await store.update_conversation(
                conversation_uuid,
                message_count_delta=1,
                metadata_patch={
                    "benchmark_harness": "extraction",
                    "benchmark_transcript_mode": "user_only_replay",
                },
            )

            extraction_result = await invoke_benchmark_extraction(
                store=store,
                user_id=user_id,
                conversation_id=conversation_id,
                messages_json=build_benchmark_transcript(transcript_so_far),
            )
            if extraction_result.get("status") != "ok":
                raise RuntimeError(
                    "Benchmark extraction replay failed: "
                    f"{json.dumps(extraction_result, default=str)}"
                )

        return conversation_id
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Keyword matching helper
# ---------------------------------------------------------------------------


def _keyword_matches(keyword: str, content: str) -> bool:
    """Check if keyword appears in content as a whole word (word-boundary match).

    Uses \\b word boundaries to prevent substring false positives like
    "arch" matching inside "march".
    """
    return bool(re.search(r"\b" + re.escape(keyword) + r"\b", content, re.IGNORECASE))


SLOT_MATCH_HINTS: dict[str, tuple[str, ...]] = {
    "age": ("years", "year", "old"),
    "vehicle": ("car", "drive", "drives"),
    "job": ("work", "works", "occupation"),
    "location": ("live", "lives", "city"),
    "language": ("code", "coding"),
}


def _slot_search_tokens(slot: str) -> str:
    parts = [p for p in re.split(r"[._]", slot.lower()) if p]
    hints: list[str] = []
    for part in parts:
        hints.extend(SLOT_MATCH_HINTS.get(part, ()))
    return " ".join(parts + hints)


def _memory_match_text(mem: dict[str, Any]) -> str:
    content = str(mem.get("content") or "")
    slot = str(mem.get("memory_slot") or "").strip()
    if not slot:
        return content
    return f"{content} {slot} {_slot_search_tokens(slot)}"


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------


@dataclass
class ExpectedFact:
    """A fact we expect the pipeline to extract."""

    keywords: list[str]  # ALL must match (case-insensitive, word-boundary)
    description: str = ""
    min_confidence: float = 0.0
    max_confidence: float = 1.0
    expected_category: str | None = None


@dataclass
class DedupCheck:
    """Assert a memory's active/closed state after supersession."""

    content_pattern: str  # substring to match (case-insensitive)
    should_be_active: bool
    description: str = ""


@dataclass
class Scenario:
    name: str
    description: str
    messages: list[str]
    expected: list[ExpectedFact]
    dedup_checks: list[DedupCheck] = field(default_factory=list)
    noise_keywords: list[list[str]] = field(default_factory=list)
    is_adversarial: bool = False
    wait_seconds: int = DEFAULT_WAIT
    inter_message_wait: int = 0  # extra wait between messages (for supersession)


SCENARIOS: list[Scenario] = [
    # ── S1: Dense Personal Facts ──────────────────────────────────────────
    Scenario(
        name="1: Dense Personal Facts",
        description="Straightforward biographical and preference statements",
        messages=[
            "My name is Julian, I'm 28 and I live in Adelaide. I work as a software engineer.",
            "I have a brother named Callan and a dog named Koda — she's a German Shepherd.",
            "I mainly code in Python and TypeScript. I prefer Neovim over VS Code but I use both.",
        ],
        expected=[
            ExpectedFact(["julian"], "name", min_confidence=0.85),
            ExpectedFact(["28", "years"], "age", min_confidence=0.85),
            ExpectedFact(["adelaide"], "location", min_confidence=0.85),
            ExpectedFact(["software engineer"], "job", min_confidence=0.85),
            ExpectedFact(["brother", "callan"], "sibling", min_confidence=0.85),
            ExpectedFact(["dog", "koda"], "pet", min_confidence=0.80),
            # v2.1: Split — extractor decomposes into separate per-language facts.
            # "User mainly codes in TypeScript" won't contain "python".
            ExpectedFact(["python"], "primary language", min_confidence=0.75),
            ExpectedFact(["typescript"], "secondary language", min_confidence=0.75),
            ExpectedFact(["neovim"], "editor preference", expected_category="preference"),
        ],
    ),
    # ── S2: Ephemeral vs Durable ──────────────────────────────────────────
    Scenario(
        name="2: Ephemeral vs Durable",
        description="Should skip transient questions, extract only durable intent",
        messages=[
            "What's the weather like in Melbourne today?",
            "Can you convert 150 USD to AUD for me?",
            "Actually I'm planning to move to Melbourne next year, that's why I'm curious about the weather.",
            "What time is it in Tokyo right now?",
        ],
        expected=[
            ExpectedFact(
                ["move", "melbourne"],
                "relocation plan",
                min_confidence=0.55,
                max_confidence=0.80,
            ),
        ],
    ),
    # ── S3: Corrections and Supersession ──────────────────────────────────
    Scenario(
        name="3: Corrections and Supersession",
        description="Tests correction category and dedup supersession",
        messages=[
            "I drive a 2019 Toyota Corolla.",
            "Actually I sold the Corolla last month. I drive a 2023 Tesla Model 3 now.",
        ],
        inter_message_wait=60,
        expected=[
            ExpectedFact(["tesla", "model 3"], "current vehicle", min_confidence=0.85),
        ],
        dedup_checks=[
            DedupCheck("Corolla", should_be_active=False, description="Corolla superseded"),
            DedupCheck("Tesla", should_be_active=True, description="Tesla active"),
        ],
    ),
    # ── S4: Projects and Goals ────────────────────────────────────────────
    Scenario(
        name="4: Projects and Goals",
        description="Tests project/goal extraction and category accuracy",
        messages=[
            "I'm building an AI assistant called Daemon. It uses FastAPI on the backend and Next.js on the frontend.",
            "I also want to start learning Rust this year. Not for Daemon, just as a personal goal.",
            "For Daemon I need to fix the memory system — extracted memories aren't being promoted to active status properly.",
        ],
        expected=[
            ExpectedFact(["daemon", "ai assistant"], "project", expected_category="project"),
            ExpectedFact(
                ["rust"],
                "learning goal",
                expected_category="project",
                min_confidence=0.65,
            ),
            # v2.2: Changed from ["memori","promot"] (stems) to ["memory","active"]
            # (whole words). Stems are incompatible with \b word-boundary matching.
            # Input says "promoted to active status" — extracted fact will contain
            # both "memory" and "active" as whole words.
            ExpectedFact(
                ["memories", "active"],
                "memory system issue",
                min_confidence=0.65,
            ),
        ],
    ),
    # ── S5: Hedged Statements ─────────────────────────────────────────────
    Scenario(
        name="5: Hedged Statements",
        description="Tests confidence calibration between definite and speculative",
        messages=[
            "I'm definitely allergic to shellfish. I might also be lactose intolerant but I haven't been tested.",
            "I'm thinking about maybe getting a cat but I'm not sure yet. My girlfriend wants one though.",
            "We'll probably go to Japan in October. Nothing booked yet.",
        ],
        expected=[
            ExpectedFact(["shellfish"], "allergy (definite)", min_confidence=0.88),
            ExpectedFact(["lactose"], "intolerance (hedged)", max_confidence=0.75),
            ExpectedFact(["girlfriend"], "has girlfriend", min_confidence=0.80),
            ExpectedFact(["girlfriend", "cat"], "girlfriend wants cat", min_confidence=0.75),
            ExpectedFact(["cat"], "considering cat (hedged)", max_confidence=0.80),
            ExpectedFact(["japan", "october"], "Japan trip (hedged)", max_confidence=0.75),
        ],
    ),
    # ── S6: Realistic Multi-Turn Session ──────────────────────────────────
    Scenario(
        name="6: Realistic Multi-Turn Session",
        description="Interleaved signal and noise in natural conversation",
        messages=[
            "Hey, can you help me think through my home server setup?",
            "So I've got the 9950X3D picked out, 64 gigs of DDR5, and I'm waiting on the 5090 TUF. Case is a Be Quiet Light Base 500.",
            "What's a good Linux distro for this kind of thing? I was thinking NixOS or CachyOS.",
            "Yeah let's go with CachyOS. I like that it's Arch-based but with better defaults. I've used Arch before so I'm comfortable with pacman.",
            "Oh by the way, my birthday is March 15th. My girlfriend always asks me what I want and I never know what to tell her.",
            "Anyway back to the server — I want Tailscale for remote access, and I'll need a static IP on the local network.",
            "How much power does a 5090 draw under load? I want to make sure my UPS can handle it. I mainly want this for LLM inference, maybe some Plex too.",
        ],
        expected=[
            ExpectedFact(["9950x3d"], "CPU choice", min_confidence=0.80),
            ExpectedFact(["be quiet", "light base"], "case"),
            ExpectedFact(["cachyos"], "distro preference", expected_category="preference"),
            # v2.2: \b word-boundary matching now correctly matches "Arch" as a
            # standalone word without false-matching "March". This was the primary
            # motivator for the v2.2 matching overhaul.
            ExpectedFact(["arch"], "Arch experience"),
            ExpectedFact(["birthday", "march"], "birthday", min_confidence=0.88),
            ExpectedFact(["tailscale"], "remote access"),
            ExpectedFact(["llm"], "server purpose"),
        ],
        noise_keywords=[
            ["ups"],  # assistant knowledge about power draw
        ],
    ),
    # ── S7: Explicit Memory Instructions ──────────────────────────────────
    Scenario(
        name="7: Explicit Memory Instructions",
        description="Tests 'remember this' style explicit storage requests",
        messages=[
            "For future reference: my AWS account ID is 123456789012 and I always deploy to ap-southeast-2.",
            "Remember that I hate YAML. Always suggest TOML or JSON alternatives.",
            "Don't store anything about my medical stuff — that's private.",
        ],
        expected=[
            ExpectedFact(["aws", "123456789012"], "AWS account", min_confidence=0.88),
            ExpectedFact(["ap-southeast-2"], "deploy region", min_confidence=0.85),
            ExpectedFact(
                ["yaml"],
                "YAML hatred",
                expected_category="preference",
                min_confidence=0.88,
            ),
        ],
    ),
    # ── S8: Adversarial Empty ─────────────────────────────────────────────
    Scenario(
        name="8: Adversarial Empty",
        description="Zero memories should be extracted",
        messages=[
            "lol",
            "ok",
            ".",
            "Can you just tell me a joke?",
            "hahaha that's good. anyway, I'm heading to bed. Talk tomorrow!",
        ],
        expected=[],
        is_adversarial=True,
    ),
]


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@dataclass
class MatchResult:
    expected: ExpectedFact
    matched_memory: dict[str, Any] | None = None
    confidence_warning: str | None = None
    category_warning: str | None = None


def match_fact(expected: ExpectedFact, memories: list[dict[str, Any]]) -> MatchResult:
    for mem in memories:
        match_text = _memory_match_text(mem)
        if all(_keyword_matches(kw, match_text) for kw in expected.keywords):
            result = MatchResult(expected=expected, matched_memory=mem)
            conf = mem.get("confidence", 0)
            if expected.min_confidence and conf < expected.min_confidence:
                result.confidence_warning = f"confidence {conf:.2f} < min {expected.min_confidence}"
            if expected.max_confidence < 1.0 and conf > expected.max_confidence:
                result.confidence_warning = (
                    f"confidence {conf:.2f} > max {expected.max_confidence} (hedging failed)"
                )
            cat = mem.get("category", "")
            if expected.expected_category and cat != expected.expected_category:
                result.category_warning = f"expected category={expected.expected_category}"
            return result
    return MatchResult(expected=expected)


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    name: str
    expected_count: int
    extracted_count: int
    tp: int = 0
    fp: int = 0
    fn: int = 0
    precision: float = 0.0
    recall: float = 0.0
    matches: list[dict[str, Any]] = field(default_factory=list)
    unaccounted: list[dict[str, Any]] = field(default_factory=list)
    dedup_results: list[dict[str, Any]] = field(default_factory=list)
    noise_matches: list[dict[str, Any]] = field(default_factory=list)
    adversarial_fail: bool = False
    memories_raw: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0


def run_scenario(
    scenario: Scenario, do_wipe: bool = True, db_url: str = DATABASE_URL
) -> ScenarioResult:
    """Execute a single benchmark scenario and evaluate results."""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  Scenario: {scenario.name}")
    print(f"  {scenario.description}")
    print(sep)

    if do_wipe:
        wiped = wipe_memories(db_url)
        if wiped > 0:
            print(f"  🗑 Wiped {wiped} memories from previous scenario")
        flushed = flush_redis_db()
        if flushed:
            print("  🧹 Redis DB flushed")
        redis_wiped = wipe_redis_extract_keys()
        if redis_wiped > 0:
            print(f"  🧹 Cleared {redis_wiped} Redis extraction key(s)")

    # Generate fresh isolated benchmark user for this scenario
    benchmark_user = _fresh_benchmark_user()
    print(f"  👤 Benchmark user: {benchmark_user}")

    t0 = time.time()

    for i, msg in enumerate(scenario.messages):
        preview = msg[:70] + ("..." if len(msg) > 70 else "")
        print(f"  → [{i + 1}/{len(scenario.messages)}] {preview}")
    conv_id = asyncio.run(
        replay_benchmark_conversation(
            messages=scenario.messages,
            user_id=benchmark_user,
            db_url=db_url,
        )
    )
    print("  ↻ Replayed transcript through deterministic benchmark extraction path")

    all_memories = query_memories(db_url, conversation_id=conv_id)
    active_memories = [m for m in all_memories if m.get("valid_to") is None]

    # Separate extracted memories (from extraction pipeline) from user_created (tool side-effects)
    extracted_memories = [m for m in active_memories if m.get("source_type") == "extracted"]
    user_created_memories = [m for m in active_memories if m.get("source_type") == "user_created"]

    # Debug: show user_created memories if any
    if user_created_memories:
        print(f"  📋 {len(user_created_memories)} user_created memory(ies) - not scored:")
        for mem in user_created_memories:
            print(f"    → '{mem['content'][:60]}...' (source_type={mem.get('source_type')})")

    # Use only extracted memories for scoring
    scoring_memories = extracted_memories

    duration = time.time() - t0

    result = ScenarioResult(
        name=scenario.name,
        expected_count=len(scenario.expected),
        extracted_count=len(scoring_memories),
        memories_raw=all_memories,
        duration_seconds=round(duration, 1),
    )

    # ── Match expected facts ──────────────────────────────────────────────
    matched_ids: set[str] = set()
    for ef in scenario.expected:
        mr = match_fact(ef, scoring_memories)
        if mr.matched_memory:
            result.tp += 1
            matched_ids.add(mr.matched_memory.get("id", ""))
            mem = mr.matched_memory
            warnings = ""
            if mr.confidence_warning:
                warnings += f"  ⚠ {mr.confidence_warning}"
            if mr.category_warning:
                warnings += f"  ⚠ {mr.category_warning}"
            print(
                f"  ✓ {ef.keywords} → '{mem['content']}' "
                f"(cat={mem['category']}, conf={mem['confidence']:.2f}){warnings}"
            )
            result.matches.append(
                {
                    "keywords": ef.keywords,
                    "content": mem["content"],
                    "category": mem["category"],
                    "confidence": mem["confidence"],
                    "slot": mem.get("memory_slot"),
                    "confidence_warning": mr.confidence_warning,
                    "category_warning": mr.category_warning,
                }
            )
        else:
            result.fn += 1
            print(f"  ✗ MISSING: {ef.keywords}")

    # ── Noise detection (word-boundary matching) ──────────────────────────
    for noise_kws in scenario.noise_keywords:
        for mem in scoring_memories:
            if all(_keyword_matches(kw, mem["content"]) for kw in noise_kws):
                result.fp += 1
                print(f"  ✗ NOISE: '{mem['content']}' matched {noise_kws}")
                result.noise_matches.append({"keywords": noise_kws, "content": mem["content"]})

    # ── Adversarial check ─────────────────────────────────────────────────
    if scenario.is_adversarial and scoring_memories:
        result.adversarial_fail = True
        result.fp = len(scoring_memories)
        print(f"  ✗ ADVERSARIAL FAIL: {len(scoring_memories)} memories extracted from noise")
        for mem in scoring_memories:
            print(f"    → '{mem['content']}' (cat={mem['category']}, conf={mem['confidence']:.2f})")

    # ── Unaccounted extractions ───────────────────────────────────────────
    unaccounted = [m for m in scoring_memories if m.get("id") not in matched_ids]
    if unaccounted and not scenario.is_adversarial:
        print(f"  ? {len(unaccounted)} unaccounted extraction(s):")
        for mem in unaccounted:
            print(
                f"    → '{mem['content']}' "
                f"(cat={mem['category']}, conf={mem['confidence']:.2f}, slot={mem.get('memory_slot')})"
            )
        result.unaccounted = [
            {
                "content": m["content"],
                "category": m["category"],
                "confidence": m["confidence"],
                "slot": m.get("memory_slot"),
            }
            for m in unaccounted
        ]

    # ── Dedup / supersession checks ───────────────────────────────────────
    for dc in scenario.dedup_checks:
        pattern = dc.content_pattern.lower()
        matching = [m for m in all_memories if pattern in m["content"].lower()]
        if not matching:
            print(f"  ✗ DEDUP: No memory found matching '{dc.content_pattern}'")
            result.dedup_results.append(
                {
                    "pattern": dc.content_pattern,
                    "expected_active": dc.should_be_active,
                    "found": False,
                    "pass": False,
                }
            )
            continue

        active_count = sum(1 for mem in matching if mem.get("valid_to") is None)
        if dc.should_be_active:
            aggregate_passed = active_count == 1
        else:
            aggregate_passed = active_count == 0

        for mem in matching:
            is_active = mem.get("valid_to") is None
            icon = "✓" if aggregate_passed else "✗"
            print(
                f"  {icon} DEDUP: '{mem['content']}' active={is_active}, "
                f"expected active={dc.should_be_active}"
            )
            result.dedup_results.append(
                {
                    "pattern": dc.content_pattern,
                    "content": mem["content"],
                    "expected_active": dc.should_be_active,
                    "actual_active": is_active,
                    "slot": mem.get("memory_slot"),
                    "valid_from": mem.get("valid_from"),
                    "valid_to": mem.get("valid_to"),
                    "active_count": active_count,
                    "match_count": len(matching),
                    "pass": aggregate_passed,
                }
            )

    # ── Score ─────────────────────────────────────────────────────────────
    total_pos = result.tp + result.fp
    result.precision = result.tp / total_pos if total_pos > 0 else (1.0 if result.fn == 0 else 0.0)
    result.recall = result.tp / (result.tp + result.fn) if (result.tp + result.fn) > 0 else 1.0

    print(
        f"\n  Score: TP={result.tp} FP={result.fp} FN={result.fn} P={result.precision:.2f} R={result.recall:.2f}"
    )

    return result


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def print_summary(results: list[ScenarioResult]) -> tuple[float, float, int]:
    """Print results table and return (precision, recall, adversarial_fp)."""
    total_tp = total_fp = total_fn = total_expected = total_extracted = 0
    for r in results:
        total_tp += r.tp
        total_fp += r.fp
        total_fn += r.fn
        total_expected += r.expected_count
        total_extracted += r.extracted_count

    total_p = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
    total_r = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 1.0
    adversarial_fp = sum(1 for r in results if r.adversarial_fail)

    # Try rich table, fall back to plain
    try:
        console_mod = importlib.import_module("rich.console")
        table_mod = importlib.import_module("rich.table")
        Console = console_mod.Console
        Table = table_mod.Table

        console = Console()
        table = Table(title="Memory Extraction Benchmark Results")
        for col, j in [
            ("Scenario", "left"),
            ("Expected", "right"),
            ("Extracted", "right"),
            ("TP", "right"),
            ("FP", "right"),
            ("FN", "right"),
            ("Precision", "right"),
            ("Recall", "right"),
        ]:
            table.add_column(col, justify=j, style="bold" if col == "Scenario" else None)

        for r in results:
            table.add_row(
                r.name,
                str(r.expected_count),
                str(r.extracted_count),
                str(r.tp),
                str(r.fp),
                str(r.fn),
                f"{r.precision:.2f}",
                f"{r.recall:.2f}",
            )
        table.add_section()
        table.add_row(
            "TOTAL",
            str(total_expected),
            str(total_extracted),
            str(total_tp),
            str(total_fp),
            str(total_fn),
            f"{total_p:.2f}",
            f"{total_r:.2f}",
            style="bold",
        )
        console.print(table)
    except ImportError:
        print("\n--- Results ---")
        for r in results:
            print(f"  {r.name}: TP={r.tp} FP={r.fp} FN={r.fn} P={r.precision:.2f} R={r.recall:.2f}")
        print(f"  TOTAL: TP={total_tp} FP={total_fp} FN={total_fn} P={total_p:.2f} R={total_r:.2f}")

    return total_p, total_r, adversarial_fp


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Daemon Memory Extraction Benchmark v2.4")
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Skip database wipe between scenarios (for debugging)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Output structured JSON results to stdout"
    )
    parser.add_argument(
        "--scenarios",
        type=str,
        default=None,
        help="Comma-separated scenario numbers to run (e.g. '3,5,8')",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=DEFAULT_WAIT,
        help=f"Seconds to wait for extraction (default: {DEFAULT_WAIT})",
    )
    parser.add_argument(
        "--no-save", action="store_true", help="Skip saving results to tests/results/"
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Override DATABASE_URL (bypasses .env and Docker hostname rewriting)",
    )
    args = parser.parse_args()

    effective_db_url = args.db_url if args.db_url else DATABASE_URL

    # Override wait
    if args.wait != DEFAULT_WAIT:
        for s in SCENARIOS:
            s.wait_seconds = args.wait

    # Filter scenarios
    if args.scenarios:
        indices = {int(x.strip()) for x in args.scenarios.split(",")}
        scenarios = [s for s in SCENARIOS if int(s.name.split(":")[0]) in indices]
        if not scenarios:
            print(f"No scenarios matched: {args.scenarios}", file=sys.stderr)
            sys.exit(1)
    else:
        scenarios = SCENARIOS

    db_display = effective_db_url.split("@")[-1] if "@" in effective_db_url else effective_db_url
    print("Daemon Memory Extraction Benchmark v2.4")
    print("Mode: deterministic transcript replay (no /chat assistant generation)")
    print(f"Database: {db_display}")
    print(f"Redis: {REDIS_URL}")
    print(f"Legacy wait setting: {args.wait}s (ignored in replay mode)")
    print(f"Scenarios: {len(scenarios)}")
    print(f"DB wipe: {'disabled' if args.no_wipe else 'enabled'}")
    print(
        f"Decryption: {'available' if _get_fernet() else 'unavailable (set DAEMON_ENCRYPTION_KEY)'}"
    )
    print("Backend health check: skipped (benchmark now uses direct replay path)")

    # Initial wipe
    if not args.no_wipe:
        wiped = wipe_memories(effective_db_url)
        if wiped > 0:
            print(f"🗑 Initial wipe: {wiped} memories cleared")
        flushed = flush_redis_db()
        if flushed:
            print("  🧹 Redis DB flushed")
        redis_wiped = wipe_redis_extract_keys()
        if redis_wiped > 0:
            print(f"🧹 Initial redis wipe: {redis_wiped} extraction key(s) cleared")

    # Run
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        result = run_scenario(
            scenario,
            do_wipe=not args.no_wipe,
            db_url=effective_db_url,
        )
        results.append(result)

    # Summary
    total_p, total_r, adversarial_fp = print_summary(results)

    print()
    print(f"  {'✓' if total_p >= 0.90 else '✗'} Precision ≥ 0.90")
    print(f"  {'✓' if total_r >= 0.90 else '✗'} Recall ≥ 0.90")
    print(f"  {'✓' if adversarial_fp == 0 else '✗'} Adversarial = 0 extractions")

    passed = total_p >= 0.90 and total_r >= 0.90 and adversarial_fp == 0
    print(f"\n  {'✅ BENCHMARK PASSED' if passed else '❌ BENCHMARK FAILED — see details above'}")

    # Build output
    output: dict[str, Any] = {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "2.4",
        "config": {
            "mode": "deterministic_transcript_replay",
            "wait_seconds": args.wait,
            "db_wipe": not args.no_wipe,
            "scenarios_filter": args.scenarios,
            "decryption_available": _get_fernet() is not None,
        },
        "scenarios": [
            {
                "name": r.name,
                "expected": r.expected_count,
                "extracted": r.extracted_count,
                "tp": r.tp,
                "fp": r.fp,
                "fn": r.fn,
                "precision": round(r.precision, 4),
                "recall": round(r.recall, 4),
                "duration_seconds": r.duration_seconds,
                "matches": r.matches,
                "unaccounted": r.unaccounted,
                "dedup_results": r.dedup_results,
                "noise_matches": r.noise_matches,
                "adversarial_fail": r.adversarial_fail,
                "memories_raw": r.memories_raw,
            }
            for r in results
        ],
        "totals": {
            "precision": round(total_p, 4),
            "recall": round(total_r, 4),
            "adversarial_fp": adversarial_fp,
            "passed": passed,
        },
    }

    if args.json:
        print(json.dumps(output, indent=2, default=str))

    if not args.no_save:
        os.makedirs("tests/results", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = f"tests/results/bench_{ts}.json"
        with open(filepath, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"\n  Results saved to {filepath}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
