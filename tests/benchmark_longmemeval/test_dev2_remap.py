"""Offline tests for the dev2 historical-clock remap. Fakes, no live database."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from orchestrator.eval.bench_db import BENCHMARK_DB_MARKER, question_user_id
from tests.benchmark_longmemeval import dev2_remap
from tests.benchmark_longmemeval.dev2_remap import (
    COLUMNS,
    COPY_DB_NAME,
    EXCLUDED,
    Mapper,
    RemapError,
    SOURCE_DB_NAME,
    assert_copy_database,
    assert_source_database,
    check_memory_windows,
    classify_columns,
    column_inventory,
    load_dataset,
    memory_session_bounds,
    prepare_copy,
    run_remap,
)

W1 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
D1 = datetime(2023, 5, 20, 2, 21, tzinfo=UTC)
D2 = datetime(2023, 5, 21, 9, 0, tzinfo=UTC)


class _Frozen:
    def __init__(self, ids: Sequence[str]) -> None:
        self.ids = tuple(ids)
        self.sha256 = "f" * 64


def _frozen(count: int = 100) -> _Frozen:
    return _Frozen(tuple(f"q{index:03d}" for index in range(count)))


def _discovery() -> dict[str, list[str]]:
    found = {table: list(columns) for table, columns in COLUMNS.items()}
    for table, columns in EXCLUDED.items():
        found.setdefault(table, []).extend(columns)
    return found


class _Transaction:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def start(self) -> None:
        self.conn.events.append("start")

    async def commit(self) -> None:
        self.conn.events.append("commit")

    async def rollback(self) -> None:
        self.conn.events.append("rollback")


class _Conn:
    """Scripted connection: the refusal paths run before any table is touched."""

    def __init__(
        self,
        *,
        database: str = COPY_DB_NAME,
        discovery: Mapping[str, Sequence[str]] | None = None,
        retrieval_rows: int = 0,
        triggers: int = 0,
    ) -> None:
        self.database = database
        self.discovery = discovery if discovery is not None else _discovery()
        self.retrieval_rows = retrieval_rows
        self.triggers = triggers
        self.events: list[str] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "current_database" in sql:
            return self.database
        if "retrieval_log" in sql:
            return self.retrieval_rows
        if "pg_trigger" in sql:
            return self.triggers
        if "information_schema.columns" in sql:
            return 1
        raise AssertionError(f"unexpected query: {sql}")

    async def fetch(self, sql: str, *args: Any) -> Any:
        if "information_schema.columns" in sql:
            return [
                {"table_name": table, "column_name": column}
                for table, columns in self.discovery.items()
                for column in columns
            ]
        raise AssertionError(f"unexpected query: {sql}")

    def transaction(self, **kwargs: Any) -> _Transaction:
        return _Transaction(self)


class _Control:
    """A control connection that records CREATE DATABASE and COMMENT.

    ``CREATE DATABASE ... TEMPLATE`` does not carry the shared-object comment, so
    the copy starts unmarked and the prepare step has to apply it.
    """

    def __init__(
        self,
        *,
        marker: str | None = BENCHMARK_DB_MARKER,
        copy_marker: str | None = None,
        sessions: int = 0,
        exists: int = 0,
    ) -> None:
        self.marker = marker
        self.copy_marker = copy_marker
        self.sessions = sessions
        self.exists = exists
        self.copy_created = False
        self.statements: list[str] = []

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        if "FROM pg_database" in sql:
            return {"oid": 4242, "datallowconn": True, "marker": self.marker}
        raise AssertionError(sql)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "pg_stat_activity" in sql:
            return self.sessions
        if "SELECT count(*) FROM pg_database" in sql:
            return self.exists
        if "shobj_description" in sql:
            return self.copy_marker if self.copy_created else self.marker
        if "SELECT oid FROM pg_database" in sql:
            return 9999
        raise AssertionError(sql)

    async def execute(self, sql: str, *args: Any) -> None:
        self.statements.append(sql)
        if sql.startswith("CREATE DATABASE"):
            self.copy_created = True
        if sql.startswith("COMMENT ON DATABASE"):
            self.copy_marker = BENCHMARK_DB_MARKER


class _FingerprintConn:
    async def close(self) -> None:
        return None


# --- mapper arithmetic and boundaries ----------------------------------------


def test_mapper_arithmetic_preserves_order_and_gap() -> None:
    mapper = Mapper(
        windows=(W1, W1 + timedelta(hours=1)),
        dates=(D1, D1 + timedelta(hours=1)),
        end=W1 + timedelta(hours=2),
        question_at=D1 + timedelta(days=2),
    )
    # Half of a one-hour raw span fits inside half of a one-hour target gap.
    assert mapper.scales == (0.5, 1.0)

    start = mapper(W1)
    middle = mapper(W1 + timedelta(minutes=30))
    next = mapper(W1 + timedelta(hours=1))
    assert start == D1
    assert middle == D1 + timedelta(minutes=30) * mapper.scales[0]
    assert next == D1 + timedelta(hours=1)


def test_mapper_clamps_before_first_window_and_keeps_null() -> None:
    mapper = Mapper(
        windows=(W1,),
        dates=(D1,),
        end=W1 + timedelta(hours=1),
        question_at=D1 + timedelta(days=2),
    )
    assert mapper(W1 - timedelta(days=30)) == D1
    assert mapper(None) is None


def test_mapper_scales_at_most_one() -> None:
    # A tiny raw span with a wide target gap must not stretch time.
    mapper = Mapper(
        windows=(W1,),
        dates=(D1,),
        end=W1 + timedelta(seconds=1),
        question_at=D1 + timedelta(days=365),
    )
    assert mapper.scales == (1.0,)
    assert mapper(W1 + timedelta(seconds=1)) == D1 + timedelta(seconds=1)


def test_mapper_ties_collapse_equal_session_dates() -> None:
    same = D1
    mapper = Mapper(
        windows=(W1, W1 + timedelta(hours=1)),
        dates=(same, same),
        end=W1 + timedelta(hours=2),
        question_at=D1 + timedelta(days=1),
    )
    assert mapper.scales[0] == 0.0
    assert mapper(W1 + timedelta(minutes=10)) == same
    assert mapper(W1 + timedelta(hours=1)) == same
    later = mapper(W1 + timedelta(hours=1, minutes=10))
    assert later is not None and later > same


def test_mapper_refuses_mismatched_windows_and_dates() -> None:
    with pytest.raises(RemapError):
        Mapper(windows=(W1,), dates=(D1, D2), end=W1, question_at=D2)
    with pytest.raises(RemapError):
        Mapper(windows=(), dates=(), end=W1, question_at=D2)


# --- database-name refusals ---------------------------------------------------


def test_copy_refuses_the_original_explicitly() -> None:
    with pytest.raises(RemapError, match="original stays as evidence"):
        assert_copy_database(SOURCE_DB_NAME)


@pytest.mark.parametrize("name", [None, "", "postgres", "daemon_lme_bench_dev2", "other"])
def test_copy_refuses_any_other_database(name: str | None) -> None:
    with pytest.raises(RemapError, match="only daemon_lme_bench_dev2_final_remap"):
        assert_copy_database(name)


def test_copy_names_are_exact() -> None:
    assert assert_copy_database(COPY_DB_NAME) == COPY_DB_NAME
    assert assert_source_database(SOURCE_DB_NAME) == SOURCE_DB_NAME
    with pytest.raises(RemapError):
        assert_source_database(COPY_DB_NAME)


def test_run_remap_refuses_the_original_before_any_write(tmp_path: Path) -> None:
    conn = _Conn(database=SOURCE_DB_NAME)
    with pytest.raises(RemapError, match="original stays as evidence"):
        asyncio.run(
            run_remap(
                conn=conn,
                dataset=[],
                apply=False,
                output_path=tmp_path / "remap.json",
                repo_root=tmp_path / "repo",
            )
        )
    assert conn.events == []


# --- column classification ----------------------------------------------------


def test_known_schema_classifies() -> None:
    classified = classify_columns(_discovery())
    assert classified == dict(COLUMNS)


def test_unclassified_table_refuses() -> None:
    discovery = _discovery()
    discovery["mystery_table"] = ["created_at"]
    with pytest.raises(RemapError, match="mystery_table"):
        classify_columns(discovery)


def test_unclassified_column_on_a_remapped_table_refuses() -> None:
    discovery = _discovery()
    discovery["memories"].append("archived_at")
    with pytest.raises(RemapError, match="archived_at"):
        classify_columns(discovery)


def test_unclassified_column_on_an_excluded_table_refuses() -> None:
    discovery = _discovery()
    discovery["retrieval_log"].append("settled_at")
    with pytest.raises(RemapError, match="settled_at"):
        classify_columns(discovery)


def test_missing_remapped_table_refuses() -> None:
    discovery = _discovery()
    del discovery["entities"]
    with pytest.raises(RemapError, match="absent from the schema"):
        classify_columns(discovery)


def test_missing_remapped_column_refuses() -> None:
    discovery = _discovery()
    discovery["messages"] = []
    with pytest.raises(RemapError, match="missing"):
        classify_columns(discovery)


def test_report_inventory_names_every_column() -> None:
    discovery = _discovery()
    inventory = column_inventory(discovery)
    for table, columns in discovery.items():
        assert set(inventory[table]) == set(columns)
        for column in columns:
            assert inventory[table][column] != "UNCLASSIFIED"
            assert inventory[table][column]
    assert inventory["memories"]["valid_from"] == "remapped"
    assert inventory["retrieval_log"]["created_at"] == EXCLUDED["retrieval_log"]["created_at"]


# --- early refusals through run_remap ----------------------------------------


def test_retrieval_log_rows_refuse(tmp_path: Path) -> None:
    conn = _Conn(retrieval_rows=3)
    with pytest.raises(RemapError, match="retrieval_log holds 3"):
        asyncio.run(
            run_remap(
                conn=conn,
                dataset=[],
                apply=False,
                output_path=tmp_path / "remap.json",
                repo_root=tmp_path / "repo",
            )
        )
    assert conn.events == []


def test_unclassified_column_refuses_the_run(tmp_path: Path) -> None:
    discovery = _discovery()
    discovery["memories"].append("archived_at")
    with pytest.raises(RemapError, match="archived_at"):
        asyncio.run(
            run_remap(
                conn=_Conn(discovery=discovery),
                dataset=[],
                apply=False,
                output_path=tmp_path / "remap.json",
                repo_root=tmp_path / "repo",
            )
        )


def test_user_trigger_refuses(tmp_path: Path) -> None:
    with pytest.raises(RemapError, match="user triggers"):
        asyncio.run(
            run_remap(
                conn=_Conn(triggers=1),
                dataset=[],
                apply=False,
                output_path=tmp_path / "remap.json",
                repo_root=tmp_path / "repo",
            )
        )


def test_existing_output_refuses_without_touching_the_database(tmp_path: Path) -> None:
    output = tmp_path / "remap.json"
    output.write_text("{}", encoding="utf-8")
    conn = _Conn()
    with pytest.raises(FileExistsError):
        asyncio.run(
            run_remap(
                conn=conn,
                dataset=[],
                apply=False,
                output_path=output,
                repo_root=tmp_path / "repo",
            )
        )
    assert conn.events == []
    assert output.read_text(encoding="utf-8") == "{}"


# --- dataset loading ---------------------------------------------------------


def _entry(question_id: str, *, dates: Sequence[str] = ("2023/05/20 (Sat) 02:21",)) -> dict:
    return {
        "question_id": question_id,
        "question_date": "2023/05/22 (Mon) 10:00",
        "haystack_session_ids": [f"s{index}" for index in range(len(dates))],
        "haystack_dates": list(dates),
    }


def test_dataset_requires_every_frozen_question(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps([_entry("q000")]), encoding="utf-8")
    with pytest.raises(RemapError, match="missing 99 frozen question"):
        load_dataset(path, _frozen())


def test_dataset_refuses_duplicates(tmp_path: Path) -> None:
    entries = [_entry("q000"), _entry("q000"), *[_entry(f"q{i:03d}") for i in range(1, 100)]]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    with pytest.raises(RemapError, match="Duplicate question"):
        load_dataset(path, _frozen())


def test_dataset_refuses_unparsable_dates(tmp_path: Path) -> None:
    entries = [_entry(f"q{i:03d}") for i in range(100)]
    entries[0] = _entry("q000", dates=("not a date",))
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    with pytest.raises(RemapError, match="unparsable haystack date"):
        load_dataset(path, _frozen())


def test_dataset_ignores_questions_outside_the_frozen_set(tmp_path: Path) -> None:
    entries = [_entry(f"q{i:03d}") for i in range(100)]
    entries.append(_entry("outside"))
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    loaded = load_dataset(path, _frozen())
    assert len(loaded) == 100
    assert {entry["question_id"] for entry in loaded} == {f"q{i:03d}" for i in range(100)}


# --- memory window post-check -------------------------------------------------


def test_memory_windows_accept_the_bounds_and_refuse_outside() -> None:
    question_at = datetime(2023, 5, 22, 10, 0, tzinfo=UTC)
    bounds = memory_session_bounds(("s1", "s2"), (D1, D2), question_at)
    assert bounds["s1"] == (D1, D2)
    assert bounds["s2"] == (D2, question_at)
    assert check_memory_windows([{"id": "m", "session_id": "s1", "valid_from": D1}], bounds) == 1
    with pytest.raises(RemapError, match="outside its session's window"):
        check_memory_windows([{"id": "m", "session_id": "s1", "valid_from": D2}], bounds)
    with pytest.raises(RemapError, match="outside its session's window"):
        check_memory_windows([{"id": "m", "session_id": "s2", "valid_from": question_at}], bounds)


def test_memory_windows_refuse_unattributable_memories() -> None:
    bounds = memory_session_bounds(("s1",), (D1,), D2)
    with pytest.raises(RemapError, match="cannot be attributed"):
        check_memory_windows([{"id": "m", "session_id": None, "valid_from": D1}], bounds)
    with pytest.raises(RemapError, match="no valid_from"):
        check_memory_windows([{"id": "m", "session_id": "s1", "valid_from": None}], bounds)


# --- prepare -----------------------------------------------------------------


def _prepare_kwargs(tmp_path: Path, control: _Control) -> dict[str, Any]:
    return {
        "control": control,
        "control_dsn": "postgresql://b:b@h:5432/postgres",
        "source_dsn": f"postgresql://b:b@h:5432/{SOURCE_DB_NAME}",
        "copy_dsn": f"postgresql://b:b@h:5432/{COPY_DB_NAME}",
        "frozen": _frozen(),
        "output_path": tmp_path / "prepared.json",
        "repo_root": tmp_path / "repo",
    }


def test_prepare_refuses_an_existing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "prepared.json"
    output.write_text("{}", encoding="utf-8")

    async def connect(dsn: str) -> Any:
        raise AssertionError("no connection expected")

    kwargs = _prepare_kwargs(tmp_path, _Control(marker=BENCHMARK_DB_MARKER))
    kwargs["output_path"] = output
    kwargs["connect"] = connect
    with pytest.raises(FileExistsError):
        asyncio.run(prepare_copy(**kwargs))


def test_prepare_refuses_the_wrong_source_name(tmp_path: Path) -> None:
    kwargs = _prepare_kwargs(tmp_path, _Control(marker=BENCHMARK_DB_MARKER))
    kwargs["source_dsn"] = f"postgresql://b:b@h:5432/{COPY_DB_NAME}"
    kwargs["connect"] = None
    with pytest.raises(RemapError, match="Refusing source"):
        asyncio.run(prepare_copy(**kwargs))


def test_prepare_refuses_an_existing_copy(tmp_path: Path) -> None:
    kwargs = _prepare_kwargs(tmp_path, _Control(marker=BENCHMARK_DB_MARKER, exists=1))
    kwargs["connect"] = None
    with pytest.raises(RemapError, match="already exists"):
        asyncio.run(prepare_copy(**kwargs))


def test_prepare_refuses_a_source_with_sessions(tmp_path: Path) -> None:
    kwargs = _prepare_kwargs(tmp_path, _Control(marker=BENCHMARK_DB_MARKER, sessions=2))
    kwargs["connect"] = None
    with pytest.raises(RemapError, match="still has 2 session"):
        asyncio.run(prepare_copy(**kwargs))


def test_prepare_refuses_an_unmarked_source(tmp_path: Path) -> None:
    kwargs = _prepare_kwargs(tmp_path, _Control(marker="something else"))
    kwargs["connect"] = None
    with pytest.raises(RemapError, match="no benchmark marker"):
        asyncio.run(prepare_copy(**kwargs))


def test_prepare_applies_the_marker_and_creates_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fingerprint(conn: Any, user_ids: Sequence[uuid.UUID]) -> dict[str, Any]:
        return {"sha256": "a" * 64, "users": len(user_ids), "rows": {}}

    async def connect(dsn: str) -> Any:
        return _FingerprintConn()

    monkeypatch.setattr(dev2_remap, "fingerprint_benchmark_users", fingerprint)
    control = _Control()
    kwargs = _prepare_kwargs(tmp_path, control)
    kwargs["connect"] = connect
    document = asyncio.run(prepare_copy(**kwargs))
    assert document["copy"]["marker_applied"] is True
    assert document["copy"]["marker"] == BENCHMARK_DB_MARKER
    assert document["copy"]["fingerprint_sha256"] == document["source"]["fingerprint_sha256"]
    assert any(statement.startswith("CREATE DATABASE") for statement in control.statements)
    assert any(statement.startswith("COMMENT ON DATABASE") for statement in control.statements)
    saved = json.loads((tmp_path / "prepared.json").read_text(encoding="utf-8"))
    assert saved["schema"] == "dev2-remap-prepare/1"


def test_prepare_refuses_a_copy_fingerprint_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digests = iter(["a" * 64, "b" * 64])

    async def fingerprint(conn: Any, user_ids: Sequence[uuid.UUID]) -> dict[str, Any]:
        return {"sha256": next(digests), "users": len(user_ids), "rows": {}}

    async def connect(dsn: str) -> Any:
        return _FingerprintConn()

    monkeypatch.setattr(dev2_remap, "fingerprint_benchmark_users", fingerprint)
    kwargs = _prepare_kwargs(tmp_path, _Control(marker=BENCHMARK_DB_MARKER))
    kwargs["connect"] = connect
    with pytest.raises(RemapError, match="Copy fingerprint differs"):
        asyncio.run(prepare_copy(**kwargs))
    assert not (tmp_path / "prepared.json").exists()


# --- integration: real Postgres, opt-in ---------------------------------------

DDL = (
    """CREATE TABLE conversations (
        id uuid PRIMARY KEY, user_id uuid NOT NULL, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
        last_activity_at timestamptz NOT NULL, summary_updated_at timestamptz)""",
    """CREATE TABLE messages (
        id uuid PRIMARY KEY, user_id uuid NOT NULL, conversation_id uuid NOT NULL,
        metadata jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL)""",
    """CREATE TABLE memories (
        id uuid PRIMARY KEY, user_id uuid NOT NULL, source_conversation_id uuid,
        source_type text NOT NULL, created_at timestamptz NOT NULL,
        updated_at timestamptz NOT NULL, valid_from timestamptz NOT NULL, valid_to timestamptz,
        last_accessed_at timestamptz, last_retrieved_at timestamptz)""",
    """CREATE TABLE memory_extraction_log (
        id uuid PRIMARY KEY, user_id uuid NOT NULL, conversation_id uuid,
        created_at timestamptz NOT NULL, last_message_observed_at timestamptz)""",
    """CREATE TABLE entities (
        id uuid PRIMARY KEY, user_id uuid NOT NULL, first_mentioned_at timestamptz,
        created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL)""",
    "CREATE TABLE retrieval_log (id uuid PRIMARY KEY, created_at timestamptz NOT NULL)",
)
TABLES = (
    "retrieval_log",
    "entities",
    "memory_extraction_log",
    "memories",
    "messages",
    "conversations",
)


def _integration_dataset(question_id: str) -> list[dict[str, Any]]:
    return [
        {
            "question_id": question_id,
            "question_date": "2023/05/22 (Mon) 10:00",
            "haystack_session_ids": ["bench-session-1"],
            "haystack_dates": ["2023/05/20 (Sat) 02:21"],
        }
    ]


@pytest.mark.skipif(
    not os.environ.get("REMAP_TEST_DATABASE_URL"),
    reason="REMAP_TEST_DATABASE_URL is not set",
)
def test_integration_dry_run_rolls_back_and_apply_commits(tmp_path: Path) -> None:
    asyncio.run(_integration_case(tmp_path))


async def _integration_case(tmp_path: Path) -> None:
    asyncpg = pytest.importorskip("asyncpg")
    dsn = os.environ["REMAP_TEST_DATABASE_URL"]
    question_id = "bench-q1"
    user_id = question_user_id(question_id)
    ingest = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    conn = await asyncpg.connect(dsn)
    try:
        for table in TABLES:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        for statement in DDL:
            await conn.execute(statement)
        conversation = uuid.uuid4()
        await conn.execute(
            "INSERT INTO conversations (id, user_id, metadata, created_at, updated_at, "
            "last_activity_at) VALUES ($1, $2, $3::jsonb, $4, $4, $4)",
            conversation,
            user_id,
            '{"session_id": "bench-session-1", "source": "longmemeval"}',
            ingest,
        )
        for minutes in (0, 5):
            await conn.execute(
                "INSERT INTO messages (id, user_id, conversation_id, metadata, created_at) "
                "VALUES ($1, $2, $3, $4::jsonb, $5)",
                uuid.uuid4(),
                user_id,
                conversation,
                '{"session_id": "bench-session-1", "source": "longmemeval"}',
                ingest + timedelta(minutes=minutes),
            )
        memory = uuid.uuid4()
        await conn.execute(
            "INSERT INTO memories (id, user_id, source_conversation_id, source_type, "
            "created_at, updated_at, valid_from) VALUES ($1, $2, $3, 'extraction', $4, $4, $4)",
            memory,
            user_id,
            conversation,
            ingest + timedelta(minutes=2),
        )
        dataset = _integration_dataset(question_id)
        original = await conn.fetchval("SELECT valid_from FROM memories WHERE id = $1", memory)

        dry = await run_remap(
            conn=conn,
            dataset=dataset,
            apply=False,
            output_path=tmp_path / "dry.json",
            repo_root=tmp_path / "repo",
        )
        assert dry["committed"] is False
        assert dry["tables"]["memories"]["rows"] == 1
        assert dry["columns"]["memories"]["valid_from"] == "remapped"
        assert dry["columns"]["retrieval_log"]["created_at"]
        after_dry = await conn.fetchval("SELECT valid_from FROM memories WHERE id = $1", memory)
        assert after_dry == original

        applied = await run_remap(
            conn=conn,
            dataset=dataset,
            apply=True,
            output_path=tmp_path / "apply.json",
            repo_root=tmp_path / "repo",
        )
        assert applied["committed"] is True
        assert applied["invariants"]["values_stuck"] is True
        assert applied["invariants"]["memory_windows_checked"] == 1
        mapped = await conn.fetchval("SELECT valid_from FROM memories WHERE id = $1", memory)
        lower = datetime(2023, 5, 20, 2, 21, tzinfo=UTC)
        upper = datetime(2023, 5, 22, 10, 0, tzinfo=UTC)
        assert lower <= mapped < upper
        assert mapped != original
        saved = json.loads((tmp_path / "apply.json").read_text(encoding="utf-8"))
        for table, columns in COLUMNS.items():
            for column in columns:
                assert column in saved["columns"][table]
    finally:
        for table in TABLES:
            await conn.execute(f"DROP TABLE IF EXISTS {table}")
        await conn.close()
