"""Manifest tests for the J1c-1a account-writer classification catalogue.

Positive: the explicit classification manifest (tests/fixtures/
account_writer_catalogue.json) must exactly equal what the bounded stdlib scanner
discovers today, and every entry must carry owner source, rationale and explicit
integration status. Only named title/summary/queued-entity SQL sites may reference
their bounded contracts.

Negative synthetic fixtures prove discriminative failure for: a new mutating
statement inside an already-classified function (per-mutation fingerprint, not
just new module/function), a new writer module, a new direct account table, a new
transitive FK table, malformed/changed categories, unknown classifications and
unclassified stores.

Nothing here proves runtime safety or a completed account fence. Named contracts
are exercised separately; other writers and the request applier remain uncovered.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

import tests.account_writer_catalogue as catalogue

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_MIGRATIONS = """\
CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY,
    email text
);
CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS messages (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations(id)
);
CREATE TABLE IF NOT EXISTS tenants (
    id uuid PRIMARY KEY,
    name text
);
"""

_BASE_APP = {
    "app.py": (
        "async def write_message(db, mid, cid):\n"
        "    sql = 'insert into messages (id, conversation_id) values ($1, $2)'\n"
        "    await db.execute(sql, mid, cid)\n"
    )
}


def _write_tree(
    tmp_path: Path, migrations: str, python_files: dict[str, str]
) -> "tuple[Path, Path]":
    migrations_dir = tmp_path / "migrations"
    app_dir = tmp_path / "app"
    migrations_dir.mkdir(parents=True)
    app_dir.mkdir(parents=True)
    (migrations_dir / "001_base.sql").write_text(migrations, encoding="utf-8")
    for relative, source in python_files.items():
        target = app_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return migrations_dir, app_dir


def _discovered(migrations_dir: Path, app_dir: Path):
    graph = catalogue.discover_tables(migrations_dir)
    paths = catalogue.ownership_paths(graph)
    sites = catalogue.discover_writer_sites(app_dir)
    return graph, paths, sites


def _synthetic_manifest(migrations_dir: Path, app_dir: Path) -> dict[str, object]:
    """A complete baseline manifest derived mechanically from discovered output."""
    graph, paths, sites = _discovered(migrations_dir, app_dir)
    table_categories = {
        name.lower(): ("content" if "users" in paths[name] else "shared_unowned")
        for name in graph.tables
    }
    tables = {
        name: {
            "category": category,
            "owner_source": f"migrations/{graph.tables[name]}",
            "ownership_path": paths[name],
            "rationale": "synthetic owner derivation for test trees",
        }
        for name, category in table_categories.items()
    }
    entries: list[dict[str, object]] = []
    for site in sites:
        target_categories = {table_categories.get(target, "deferred") for target in site.tables}
        if site.unknown:
            category, unknown = "deferred", True
            entry_tables: list[str] = list(site.tables)
        elif not site.tables:
            category, unknown = "deferred", False
            entry_tables = []
        elif len(target_categories) == 1:
            category, unknown = next(iter(target_categories)), False
            entry_tables = list(site.tables)
        else:
            category, unknown = "deferred", False
            entry_tables = list(site.tables)
        entries.append(
            {
                "owner_source": site.module,
                "qualname": site.qualname,
                "kind": site.kind,
                "line_hint": site.line,
                "fingerprint": site.fingerprint,
                "tables": entry_tables,
                "unknown": unknown,
                "uncovered": True,
                "category": category,
                "rationale": "synthetic writer derivation for test trees",
            }
        )
    return {
        "schema": catalogue.MANIFEST_SCHEMA,
        "purpose": "synthetic boundary test manifest",
        "limits": {
            "scanner": "synthetic bounds",
            "uncovered": "synthetic: nothing is fenced",
        },
        "tables": tables,
        "fk_edges": {key: list(value) for key, value in sorted(graph.edges.items())},
        "ownership_paths": dict(sorted(paths.items())),
        "writer_sites": entries,
    }


def test_committed_manifest_matches_current_discovery() -> None:
    manifest = json.loads(catalogue.MANIFEST_PATH.read_text(encoding="utf-8"))
    problems = catalogue.compare_manifest_to_discovery(
        REPO_ROOT / "migrations", [REPO_ROOT / "orchestrator", REPO_ROOT / "providers"], manifest
    )
    assert problems == []


@pytest.mark.parametrize(
    "call",
    [
        "has_table_privilege(u,c.oid,'SELECT,INSERT,REFERENCES')",
        "pg_catalog.has_any_column_privilege(u,'public.messages','SELECT,REFERENCES')",
        "has_column_privilege(u,c.oid,'conversation_id','references')",
    ],
)
def test_closed_builtin_privilege_literals_do_not_mask_actual_fk_discovery(tmp_path, call):
    sql = _BASE_MIGRATIONS + (
        f"SELECT {call};\n"
        "CREATE TABLE notes (id uuid PRIMARY KEY, user_id uuid REFERENCES users(id));\n"
    )
    migrations, app = _write_tree(tmp_path, sql, _BASE_APP)
    graph, paths, _ = _discovered(migrations, app)
    assert graph.edges["notes"] == ("users",)
    assert paths["notes"] == "root:users <- notes"
    assert set(graph.tables) == {"users", "conversations", "messages", "tenants", "notes"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'REFERENCES';",
        "SELECT other_function(u,c.oid,'REFERENCES');",
        "SELECT public.has_table_privilege(u,c.oid,'REFERENCES');",
        "SELECT not_has_table_privilege(u,c.oid,'REFERENCES');",
        "SELECT has_table_privilege(u,c.oid,'REFERENCES'||suffix);",
        "SELECT has_table_privilege(u,c.oid,'REFERENCES,UNKNOWN');",
        'CREATE TABLE notes (user_id uuid REFERENCES "users"(id));',
        "DO $$ BEGIN EXECUTE 'ALTER TABLE notes ADD CONSTRAINT f FOREIGN KEY(user_id) REFERENCES ' || target; END $$;",
    ],
)
def test_privilege_literal_handling_keeps_unsupported_references_fail_closed(tmp_path, sql):
    migrations, _ = _write_tree(tmp_path, _BASE_MIGRATIONS + sql, _BASE_APP)
    with pytest.raises(ValueError, match="unsupported reference"):
        catalogue.discover_tables(migrations)


def test_manifest_makes_explicit_non_wildcard_classification() -> None:
    manifest = json.loads(catalogue.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert catalogue.validate_manifest_shape(manifest) == []
    assert set(manifest["tables"]) == set(
        catalogue.discover_tables(REPO_ROOT / "migrations").tables
    )
    assert all(entry["category"] in catalogue.CATEGORIES for entry in manifest["tables"].values())
    assert all(entry["category"] in catalogue.CATEGORIES for entry in manifest["writer_sites"])
    integrated = [entry for entry in manifest["writer_sites"] if entry["uncovered"] is False]
    assert {(entry["owner_source"], entry["qualname"]) for entry in integrated} == {
        catalogue.TITLE_GUARDED_SITE,
        *catalogue.SUMMARY_GUARDED_SITES,
        catalogue.ENTITY_GUARDED_SITE,
    }
    for entry in integrated:
        assert entry["guard_contract"] == (
            catalogue.TITLE_GUARD_CONTRACT
            if (entry["owner_source"], entry["qualname"]) == catalogue.TITLE_GUARDED_SITE
            else catalogue.ENTITY_GUARD_CONTRACT
            if (entry["owner_source"], entry["qualname"]) == catalogue.ENTITY_GUARDED_SITE
            else catalogue.SUMMARY_GUARD_CONTRACT
        )
    assert all(entry["rationale"] and entry["owner_source"] for entry in manifest["writer_sites"])
    used = {entry["category"] for entry in manifest["tables"].values()}
    assert used <= set(catalogue.CATEGORIES)
    assert {"content", "control", "shared_unowned", "deferred"} <= used
    assert {entry["category"] for entry in manifest["writer_sites"]} <= set(catalogue.CATEGORIES)


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_source", "orchestrator.worker.jobs"),
        ("qualname", "MemoryStore.update_conversation"),
        ("tables", ["memories"]),
        ("unknown", True),
        ("category", "control"),
        ("guard_contract", "unverified"),
        ("uncovered", True),
    ],
)
def test_title_contract_cannot_be_retagged_as_other_writer_coverage(field, value):
    manifest = json.loads(catalogue.MANIFEST_PATH.read_text())
    entry = next(
        item
        for item in manifest["writer_sites"]
        if (item["owner_source"], item["qualname"]) == catalogue.TITLE_GUARDED_SITE
    )
    entry[field] = value
    assert catalogue.validate_manifest_shape(manifest)


def test_manifest_does_not_claim_runtime_fencing() -> None:
    text = catalogue.MANIFEST_PATH.read_text(encoding="utf-8")
    assert "all_runtime_fenced" not in text
    manifest: dict[str, object] = json.loads(text)
    mutated = copy.deepcopy(manifest)
    mutated["all_runtime_fenced"] = True  # type: ignore[assignment]
    assert any(
        code.startswith("claim/forbidden-key")
        for code in catalogue.validate_manifest_shape(mutated)
    )


# ---------------------------------------------------------------------------
# negative synthetic fixtures
# ---------------------------------------------------------------------------


def test_negative_new_write_inside_existing_function(tmp_path: Path) -> None:
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, _BASE_APP)
    manifest = _synthetic_manifest(migrations_dir, app_dir)
    assert catalogue.compare_manifest_to_discovery(migrations_dir, [app_dir], manifest) == []

    mutated_app = dict(_BASE_APP)
    mutated_app["app.py"] = (
        "async def write_message(db, mid, cid):\n"
        "    sql = 'insert into messages (id, conversation_id) values ($1, $2)'\n"
        "    await db.execute(sql, mid, cid)\n"
        "    await db.execute('update users set email = $1 where id = $2', 'x', mid)\n"
    )
    mutated_migrations_dir, mutated_app_dir = _write_tree(
        tmp_path / "mutated", _BASE_MIGRATIONS, mutated_app
    )
    problems = catalogue.compare_manifest_to_discovery(
        mutated_migrations_dir, [mutated_app_dir], manifest
    )
    same_function_fails = [
        code
        for code in problems
        if code.startswith("writers/unclassified-site:") and "write_message" in code
    ]
    assert same_function_fails, problems


def test_negative_new_writer_module(tmp_path: Path) -> None:
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, _BASE_APP)
    manifest = _synthetic_manifest(migrations_dir, app_dir)

    extra = copy.deepcopy(_BASE_APP)
    extra["new_writer.py"] = "def wipe(db):\n    db.execute('delete from users')\n"
    mutated_migrations_dir, mutated_app_dir = _write_tree(
        tmp_path / "mutated", _BASE_MIGRATIONS, extra
    )
    problems = catalogue.compare_manifest_to_discovery(
        mutated_migrations_dir, [mutated_app_dir], manifest
    )
    assert any(
        code.startswith("writers/unclassified-site:") and "new_writer" in code for code in problems
    ), problems


def test_negative_new_direct_account_table(tmp_path: Path) -> None:
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, _BASE_APP)
    manifest = _synthetic_manifest(migrations_dir, app_dir)

    mutated_sql = _BASE_MIGRATIONS + (
        "CREATE TABLE IF NOT EXISTS account_notes (\n"
        "    id uuid PRIMARY KEY,\n"
        "    user_id uuid NOT NULL REFERENCES users(id)\n"
        ");\n"
    )
    mutated_migrations_dir, mutated_app_dir = _write_tree(
        tmp_path / "mutated", mutated_sql, _BASE_APP
    )
    problems = catalogue.compare_manifest_to_discovery(
        mutated_migrations_dir, [mutated_app_dir], manifest
    )
    assert any("tables/undeclared-discovered: account_notes" in code for code in problems)
    assert any("edges/undeclared-discovered: account_notes->users" in code for code in problems)
    assert any("paths/mismatch: account_notes" in code for code in problems)


def test_negative_new_transitive_fk_table(tmp_path: Path) -> None:
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, _BASE_APP)
    manifest = _synthetic_manifest(migrations_dir, app_dir)

    mutated_sql = _BASE_MIGRATIONS + (
        "CREATE TABLE IF NOT EXISTS message_receipts (\n"
        "    id uuid PRIMARY KEY,\n"
        "    message_id uuid NOT NULL REFERENCES messages(id)\n"
        ");\n"
    )
    mutated_migrations_dir, mutated_app_dir = _write_tree(
        tmp_path / "mutated", mutated_sql, _BASE_APP
    )
    problems = catalogue.compare_manifest_to_discovery(
        mutated_migrations_dir, [mutated_app_dir], manifest
    )
    assert any("tables/undeclared-discovered: message_receipts" in code for code in problems)
    assert any(
        "edges/undeclared-discovered: message_receipts->messages" in code for code in problems
    )
    assert any(
        "paths/mismatch: message_receipts" in code and "conversations" in code for code in problems
    ), problems


def test_negative_invalid_and_changed_categories(tmp_path: Path) -> None:
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, _BASE_APP)
    manifest = _synthetic_manifest(migrations_dir, app_dir)

    malformed = copy.deepcopy(manifest)
    tables_obj = malformed["tables"]
    assert isinstance(tables_obj, dict)
    tables_obj["messages"]["category"] = "account_content"  # type: ignore[index]
    problems = catalogue.compare_manifest_to_discovery(migrations_dir, [app_dir], malformed)
    assert any("messages/invalid-category" in code for code in problems)

    changed = copy.deepcopy(manifest)
    tables_obj = changed["tables"]
    assert isinstance(tables_obj, dict)
    tables_obj["messages"]["category"] = "control"  # type: ignore[index]
    problems = catalogue.compare_manifest_to_discovery(migrations_dir, [app_dir], changed)
    assert any(
        code.startswith("writers/category-mismatch") or "table" in code for code in problems
    ), problems


def test_negative_malformed_writer_entry(tmp_path: Path) -> None:
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, _BASE_APP)
    manifest = _synthetic_manifest(migrations_dir, app_dir)

    stripped = copy.deepcopy(manifest)
    sites_obj = stripped["writer_sites"]
    assert isinstance(sites_obj, list)
    sites_obj[0]["rationale"] = ""
    problems = catalogue.compare_manifest_to_discovery(migrations_dir, [app_dir], stripped)
    assert any("missing-rationale" in code for code in problems)

    covered = copy.deepcopy(manifest)
    sites_obj = covered["writer_sites"]
    assert isinstance(sites_obj, list)
    for entry in sites_obj:
        entry["uncovered"] = False
    problems = catalogue.compare_manifest_to_discovery(migrations_dir, [app_dir], covered)
    assert any("coverage-claim" in code for code in problems)

    unknown_only = copy.deepcopy(manifest)
    sites_obj = unknown_only["writer_sites"]
    assert isinstance(sites_obj, list)
    sites_obj[0]["unknown"] = True
    sites_obj[0]["category"] = "content"
    problems = catalogue.compare_manifest_to_discovery(migrations_dir, [app_dir], unknown_only)
    assert any("unknown-flag-not-allowed-for-category" in code for code in problems)


def test_unresolvable_dynamic_call_recorded_not_skipped(tmp_path: Path) -> None:
    dynamic_app = {
        "dynamic.py": (
            "def build_sql(kind):\n"
            "    return kind + ' whatever'\n"
            "\n"
            "async def run(db):\n"
            "    await db.execute(build_sql('w'))\n"
        )
    }
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, dynamic_app)
    manifest = _synthetic_manifest(migrations_dir, app_dir)
    # Baseline itself is complete: the unresolvable call is explicitly catalogued.
    assert catalogue.compare_manifest_to_discovery(migrations_dir, [app_dir], manifest) == []
    sighted = manifest["writer_sites"]
    assert isinstance(sighted, list)
    entry = next(entry_ for entry_ in sighted if isinstance(entry_, dict))
    assert entry["kind"] == "sql_unresolvable"
    assert entry["unknown"] is True
    assert entry["category"] == "deferred"

    removed = copy.deepcopy(manifest)
    sites_obj = removed["writer_sites"]
    assert isinstance(sites_obj, list)
    sites_obj.remove(entry)
    problems = catalogue.compare_manifest_to_discovery(migrations_dir, [app_dir], removed)
    assert any(
        code.startswith("writers/unclassified-site:")
        and "dynamic" in code
        and "sql_unresolvable" in code
        for code in problems
    )


def test_scanner_never_imports_runtime_sources(tmp_path: Path) -> None:
    trap = {
        "trap.py": (
            "raise RuntimeError('scanner must not execute scanned sources')\n"
            "def unreachable(db):\n"
            "    db.execute('insert into messages (id) values ($1)')\n"
        )
    }
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, trap)
    sites = catalogue.discover_writer_sites(app_dir)
    # Discovery succeeded without executing the trap module; AST-only parsing.
    assert [site.qualname for site in sites] == ["unreachable"]
    assert sites[0].kind == "sql_inline"

    scanner_source = catalogue.__file__
    assert scanner_source is not None
    parsed = ast.parse(Path(scanner_source).read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(parsed)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in getattr(node, "names", [])
        if not isinstance(node, ast.ImportFrom) or node.module is not None
    }
    imported_roots.update(
        node.module.split(".")[0]
        for node in ast.walk(parsed)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    assert "orchestrator" not in imported_roots
    assert "providers" not in imported_roots


def test_static_reads_are_not_catalogued_but_insert_returning_is(tmp_path: Path) -> None:
    app = {
        "mixed.py": (
            "async def read(db):\n"
            "    return await db.execute('select id from users')\n"
            "\n"
            "async def upsert(db, uid):\n"
            "    await db.execute(\n"
            "        'insert into users (id, email) values ($1, $2) returning id', uid, 'a'\n"
            "    )\n"
        )
    }
    migrations_dir, app_dir = _write_tree(tmp_path, _BASE_MIGRATIONS, app)
    sites = catalogue.discover_writer_sites(app_dir)
    kinds = [(site.qualname, site.kind) for site in sites]
    assert ("read", "sql_inline") not in kinds
    assert ("upsert", "sql_inline") in kinds
    upsert = next(site for site in sites if site.qualname == "upsert")
    assert upsert.tables == ("users",)


def test_all_multi_parent_paths_and_alter_references_are_retained(tmp_path):
    sql = _BASE_MIGRATIONS + (
        "ALTER TABLE messages ADD COLUMN user_id uuid REFERENCES users(id);\n"
        "CREATE TABLE receipts(id uuid, message_id uuid REFERENCES messages(id), "
        "user_id uuid REFERENCES users(id));\n"
    )
    migrations, _ = _write_tree(tmp_path, sql, {})
    graph = catalogue.discover_tables(migrations)
    assert graph.edges["messages"] == ("conversations", "users")
    assert graph.edges["receipts"] == ("messages", "users")
    path = catalogue.ownership_paths(graph)["receipts"]
    assert "root:users <- conversations <- messages <- receipts" in path
    assert "root:users <- messages <- receipts" in path
    assert "root:users <- receipts" in path
    assert "root:messages" not in path


def test_public_qualified_create_fk_alter_normalize_and_keep_all_paths(tmp_path):
    sql = (
        "CREATE TABLE PUBLIC.users(id uuid PRIMARY KEY);"
        "CREATE TABLE public.tenants(id uuid, user_id uuid REFERENCES public.users(id));"
        "CREATE TABLE IF NOT EXISTS public . messages(id uuid, tenant_id uuid REFERENCES PUBLIC.tenants(id));"
        "ALTER TABLE public.messages ADD COLUMN user_id uuid REFERENCES public . users(id);"
        "CREATE TABLE public.receipts(id uuid, message_id uuid REFERENCES public.messages(id), "
        "user_id uuid REFERENCES users(id));"
        "ALTER TABLE public.messages ALTER COLUMN id SET NOT NULL;"
        "ALTER TABLE public.messages DROP COLUMN obsolete;"
    )
    migrations, _ = _write_tree(tmp_path, sql, {})
    graph = catalogue.discover_tables(migrations)
    assert set(graph.tables) == {"users", "tenants", "messages", "receipts"}
    assert graph.edges == {
        "tenants": ("users",),
        "messages": ("tenants", "users"),
        "receipts": ("messages", "users"),
    }
    assert catalogue.ownership_paths(graph)["receipts"] == (
        "root:users <- messages <- receipts | root:users <- receipts | "
        "root:users <- tenants <- messages <- receipts"
    )


@pytest.mark.parametrize(
    "sql,target",
    [
        ("INSERT INTO public.messages(id) VALUES($1)", "messages"),
        ("UPDATE PUBLIC . messages SET id=$1", "messages"),
        ("DELETE FROM public.messages WHERE id=$1", "messages"),
        ("MERGE INTO public.messages USING incoming ON false WHEN MATCHED THEN DELETE", "messages"),
        ("TRUNCATE TABLE public.messages", "messages"),
        ("DROP TABLE IF EXISTS public.messages", "messages"),
        ("CREATE TABLE IF NOT EXISTS public.messages(id uuid)", "messages"),
        ("ALTER TABLE public.messages ADD COLUMN extra uuid", "messages"),
        ("COPY public.messages FROM STDIN", "messages"),
    ],
)
def test_public_mutation_targets_are_identities_not_schema_prefixes(tmp_path, sql, target):
    _, app = _write_tree(tmp_path, _BASE_MIGRATIONS, {"app.py": f"db.execute({sql!r})"})
    (site,) = catalogue.discover_writer_sites(app)
    assert site.tables == (target,) and site.unknown is False


@pytest.mark.parametrize(
    "sql,targets",
    [
        ('INSERT INTO "messages"(id) VALUES($1)', ()),
        ('COPY "messages" FROM STDIN', ()),
        ('COPY public."messages" FROM STDIN', ()),
        ('UPDATE public."messages" SET id=$1', ()),
        ("DELETE FROM archive.messages", ()),
        ("DELETE FROM archive . messages", ()),
        ("UPDATE public.messages.extra SET id=$1", ()),
        ("UPDATE public . messages . extra SET id=$1", ()),
        ("UPDATE ONLY public.messages SET id=$1", ()),
        ("UPDATE messages$alias SET id=$1", ()),
        ("TRUNCATE public.messages, public.users", ("messages",)),
        ("TRUNCATE TABLE archive.messages", ()),
        ('TRUNCATE TABLE public."messages"', ()),
        ("TRUNCATE TABLE ONLY public.messages", ()),
        ("DROP TABLE public.messages, public.users", ("messages",)),
        ("CREATE UNLOGGED TABLE messages(id uuid)", ()),
        ("CREATE TABLE public.messages AS SELECT 1", ("messages",)),
        ("ALTER TABLE public.messages RENAME TO receipts", ("messages",)),
        ("INSERT INTO messages(id) VALUES($1); DELETE FROM archive.users", ("messages",)),
        ("UPDATE SET", ()),
        ("TRUNCATE TABLE archive.messages; DELETE FROM public.messages", ("messages",)),
        ("DROP TABLE IF EXISTS archive.messages; DELETE FROM public.messages", ("messages",)),
        (
            'CREATE TABLE IF NOT EXISTS "messages"(id uuid); DELETE FROM public.messages',
            ("messages",),
        ),
        (
            "ALTER TABLE IF EXISTS archive.messages ADD COLUMN x uuid; DELETE FROM public.messages",
            ("messages",),
        ),
        ("UPDATE SET; DELETE FROM public.messages", ("messages",)),
        ("TRUNCATE TABLE public.messages CASCADE", ("messages",)),
        ("TRUNCATE public.messages RESTART IDENTITY CASCADE", ("messages",)),
        ("DROP TABLE public.messages CASCADE", ("messages",)),
        ("UPDATE public.messages * SET id=$1", ("messages",)),
    ],
)
def test_unsupported_mutation_targets_remain_explicit_unknown_candidates(tmp_path, sql, targets):
    _, app = _write_tree(tmp_path, _BASE_MIGRATIONS, {"app.py": f"db.execute({sql!r})"})
    (site,) = catalogue.discover_writer_sites(app)
    assert site.kind == "sql_inline" and site.unknown is True and site.tables == targets
    assert "public" not in site.tables and "archive" not in site.tables


def test_public_dynamic_and_named_mutations_are_never_lost(tmp_path):
    _, app = _write_tree(
        tmp_path,
        _BASE_MIGRATIONS,
        {
            "app.py": (
                "async def write(db,target):\n"
                " sql='DELETE FROM public.messages'\n await db.execute(sql)\n"
                " await db.execute(f'UPDATE public.{target} SET id=$1')\n"
                " await db.fetchrow(queries[target])\n"
                " await db.execute('INSERT INTO public.messages(id) VALUES($1) ON CONFLICT(id) DO UPDATE SET id=$1')\n"
            )
        },
    )
    sites = catalogue.discover_writer_sites(app)
    assert len(sites) == 4
    assert {(s.kind, s.tables, s.unknown) for s in sites} == {
        ("sql_named", ("messages",), False),
        ("sql_dynamic", (), True),
        ("sql_unresolvable", (), True),
        ("sql_inline", ("messages",), False),
    }


def test_fetch_returning_and_named_sql_changes_and_duplicate_writes_are_detected(tmp_path):
    base = {
        "app.py": "async def write(db):\n sql='INSERT INTO messages(id) VALUES($1)'\n await db.fetchrow(sql, 1)\n"
    }
    migrations, app = _write_tree(tmp_path, _BASE_MIGRATIONS, base)
    manifest = _synthetic_manifest(migrations, app)
    assert catalogue.compare_manifest_to_discovery(migrations, [app], manifest) == []
    for number, source in enumerate(
        (
            "async def write(db):\n sql='UPDATE messages SET id=$1'\n await db.fetchrow(sql, 1)\n",
            base["app.py"] + " await db.fetchrow(sql, 1)\n",
            base["app.py"] + " await db.fetchval('DELETE FROM messages RETURNING id')\n",
        )
    ):
        changed_migrations, changed_app = _write_tree(
            tmp_path / str(number), _BASE_MIGRATIONS, {"app.py": source}
        )
        assert any(
            code.startswith("writers/unclassified-site")
            for code in catalogue.compare_manifest_to_discovery(
                changed_migrations, [changed_app], manifest
            )
        )


def test_filesystem_plus_modes_redis_publication_and_unresolved_fetch_are_candidates(tmp_path):
    source = {
        "app.py": "def work(db,redis,p):\n open(p,'r+')\n os.replace(p,p)\n os.open(p,flags)\n redis.publish('x','y')\n db.fetchrow(build())\n"
    }
    _, app = _write_tree(tmp_path, _BASE_MIGRATIONS, source)
    sites = catalogue.discover_writer_sites(app)
    assert len(sites) == 5
    assert all(site.unknown for site in sites)
    assert {site.kind for site in sites} == {"filesystem_write", "store_write", "sql_unresolvable"}


def test_branch_reassignment_and_augmented_sql_are_not_treated_as_pure_reads(tmp_path):
    source = {
        "app.py": "async def work(db,x):\n sql='DELETE FROM messages'\n if x: sql='SELECT * FROM messages'\n await db.execute(sql)\n query='SELECT 1'\n query+=x\n await db.execute(query)\n"
    }
    _, app = _write_tree(tmp_path, _BASE_MIGRATIONS, source)
    sites = catalogue.discover_writer_sites(app)
    assert len(sites) == 2
    assert sites[0].kind in {"sql_named", "sql_unresolvable"}
    assert {site.kind for site in sites} == {"sql_named", "sql_unresolvable"}


@pytest.mark.parametrize(
    "sql",
    [
        'CREATE TABLE "notes"(id uuid);',
        'CREATE TABLE "public".notes(id uuid);',
        'CREATE TABLE public."notes"(id uuid);',
        "CREATE TABLE archive.notes(id uuid);",
        "CREATE TABLE public.notes.extra(id uuid);",
        "CREATE TABLE public..notes(id uuid);",
        "CREATE TABLE public.notes$extra(id uuid);",
        "CREATE TABLE public.notes AS SELECT 1;",
        "CREATE TABLE IF EXISTS public.notes(id uuid);",
        'CREATE TABLE notes(id uuid REFERENCES "users"(id));',
        "CREATE TABLE notes(id uuid REFERENCES archive.users(id));",
        'CREATE TABLE notes(id uuid REFERENCES public."users"(id));',
        "CREATE TABLE notes(id uuid REFERENCES public.users.extra(id));",
        "CREATE TABLE notes(id uuid REFERENCES public.users);",
        "CREATE TABLE notes(id uuid REFERENCES ONLY public.users(id));",
        'ALTER TABLE "messages" ADD COLUMN owner uuid REFERENCES users(id);',
        "ALTER TABLE archive.messages ADD COLUMN owner uuid REFERENCES users(id);",
        "ALTER TABLE public.messages.extra ADD COLUMN owner uuid REFERENCES users(id);",
        "ALTER TABLE ONLY public.messages ADD COLUMN owner uuid REFERENCES users(id);",
        "ALTER TABLE public.messages;",
        "ALTER TABLE public.messages ADD;",
        "CREATE UNLOGGED TABLE account_notes(id uuid);",
        "CREATE FOREIGN TABLE account_notes(id uuid) SERVER synthetic;",
        "CREATE TEMP TABLE public.account_notes(id uuid);",
    ],
)
def test_unrecognized_schema_grammar_fails_closed(tmp_path, sql):
    migrations, _ = _write_tree(tmp_path, sql, {})
    with pytest.raises(ValueError, match="unsupported"):
        catalogue.discover_tables(migrations)


@pytest.mark.parametrize("name", ["messages", "public.messages"])
def test_renamed_persistent_table_requires_explicit_scanner_support(tmp_path, name):
    migrations, app = _write_tree(tmp_path / "base", _BASE_MIGRATIONS, _BASE_APP)
    manifest = _synthetic_manifest(migrations, app)
    assert catalogue.compare_manifest_to_discovery(migrations, [app], manifest) == []
    renamed, changed_app = _write_tree(
        tmp_path / "renamed",
        _BASE_MIGRATIONS + f"ALTER TABLE {name} RENAME TO account_messages;",
        _BASE_APP,
    )
    with pytest.raises(ValueError, match="unsupported table rename"):
        catalogue.compare_manifest_to_discovery(renamed, [changed_app], manifest)
