"""Static account-writer/ownership classification catalogue: bounded scanner + change detector.

Test infrastructure for the J1c classification. NOT a runtime guard, NOT a coverage
proof: nothing here may be used to claim the runtime is fully fenced (an
``all_runtime_fenced`` key or an overall ``coverage`` boolean in the manifest is
invalid by contract, enforced in :mod:`tests.test_account_writer_catalogue`).

Deterministic and stdlib-only; runtime source modules are parsed as text/AST and
are never imported, so scanning cannot execute or infer from repository code.

Boundaries (deliberate, and asserted in tests):

- symbolic SQL execution is out of scope; writes reached only through triggers, SQL
  shipped as data, or effect channels outside the discernible patterns below are
  NOT detected (not even flagged unknown) rather than silently misclassified;
- every unresolvable dynamic ``execute`` argument occurring in a scanned file is
  explicitly recorded as a ``sql_unresolvable`` site — unknown, never skipped;
- classification is explicit per table and per mutation site in the manifest; there
  are no wildcard allowlists.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_SCHEMA = "journal+account_writer_catalogue/v1"
MANIFEST_PATH = Path(__file__).resolve().parent / "fixtures" / "account_writer_catalogue.json"

# Only this concrete title SQL site may record bounded integration. The scanner
# does not verify the guard: real runtime proofs live in the named contract suite.
TITLE_GUARDED_SITE = ("orchestrator.memory.store", "MemoryStore.save_generated_conversation_title")
TITLE_GUARD_CONTRACT = "tests/test_title_account_guard_postgres.py"

#: Classification categories; every manifest entry must name exactly one.
CATEGORIES = ("content", "control", "shared_unowned", "deferred")
#: Categories that may carry ``unknown: true`` (write target not statically resolvable).
UNKNOWN_CATEGORIES = ("deferred", "shared_unowned")

MUTATION_SQL_RE = re.compile(
    r"\b(insert\s+into|update|delete\s+from|merge\s+into|truncate|"
    r"alter\s+table|create\s+table|drop\s+table|copy\s+)\b",
    re.IGNORECASE,
)
_CREATE_TABLE_RE = re.compile(
    r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?([a-z_][a-z_0-9]*)\b", re.IGNORECASE
)
_FK_RE = re.compile(r"\breferences\s+([a-z_][a-z_0-9]*)\b", re.IGNORECASE)
_ALTER_TABLE_RE = re.compile(r"\balter\s+table\s+([a-z_][a-z_0-9]*)\b", re.IGNORECASE)
TABLE_TARGET_RE = re.compile(
    r"\b(?:insert\s+into|delete\s+from|update|merge\s+into|truncate|"
    r"alter\s+table|drop\s+table|create\s+table|copy)\s+([a-z_][a-z_0-9]*)\b",
    re.IGNORECASE,
)

_EXECUTABLE_ATTRS = frozenset(
    {"execute", "executemany", "execute_many", "fetch", "fetchrow", "fetchval"}
)
FS_MUTATION_ATTRS = frozenset(
    {
        "write_text",
        "write_bytes",
        "rmtree",
        "unlink",
        "remove",
        "truncate",
        "write",
        "ftruncate",
        "rename",
        "replace",
        "link",
        "symlink",
        "mkdir",
        "makedirs",
        "rmdir",
        "fsync",
        "flush",
        "mkstemp",
        "dump",
    }
)
STORE_MUTATION_ATTRS = frozenset(
    {
        "set",
        "setex",
        "setnx",
        "hset",
        "hdel",
        "sadd",
        "srem",
        "rpush",
        "lpush",
        "expire",
        "incr",
        "incrby",
        "delete",
        "publish",
        "enqueue_job",
        "copy_records_to_table",
        "copy_to_table",
    }
)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _fingerprint(kind: str, text: str) -> str:
    """Deterministic per-mutation fingerprint of the source snippet."""
    return hashlib.sha256(f"{kind}\x00{_normalize(text)}".encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class TableGraph:
    """Tables and FK edges discovered in the migration files."""

    tables: dict[str, str]  # table name -> migration basename
    edges: dict[str, tuple[str, ...]]  # ALL child -> parent dependencies, not policy ownership


@dataclass(frozen=True, slots=True)
class WriterSite:
    """One discovered potential mutation entry point."""

    module: str
    qualname: str
    kind: str
    line: int
    fingerprint: str
    tables: tuple[str, ...]
    unknown: bool


# ---------------------------------------------------------------------------
# schema discovery (SQL regex over migration files)
# ---------------------------------------------------------------------------


def discover_tables(migrations_dir: Path) -> TableGraph:
    """Discover forward CREATE/ALTER reference dependencies, retaining all parents.

    Bounded unquoted SQL grammar, not live constraint introspection. Historical
    references remain conservative dependencies if later constraints are dropped.
    FK reachability is not sufficient to decide semantic ownership/retention.
    """
    tables: dict[str, str] = {}
    raw_edges: dict[str, set[str]] = {}
    for path in sorted(migrations_dir.glob("*.sql")):
        text = re.sub(r"--[^\n]*|/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        if re.search(r"\balter\s+table\b[^;]*\brename\s+to\b", text, re.I):
            # A persistent rename creates a new classification identity. Do not
            # let the historical CREATE name make that migration pass unseen.
            raise ValueError(f"unsupported table rename in {path.name}")
        # Do not silently accept a new quoted/schema-qualified grammar that the
        # bounded parser cannot represent: it needs an explicit scanner update.
        for start in re.finditer(
            r"\bcreate\s+(?:(?:unlogged|temporary|temp|foreign|global|local)\s+)*table\b",
            text,
            re.I,
        ):
            match = _CREATE_TABLE_RE.match(text, start.start())
            if match is None or text[match.end() :].lstrip().startswith("."):
                raise ValueError(f"unsupported table declaration in {path.name}")
        for start in re.finditer(r"\balter\s+table\b", text, re.I):
            match = _ALTER_TABLE_RE.match(text, start.start())
            if match is None or text[match.end() :].lstrip().startswith("."):
                raise ValueError(f"unsupported ALTER declaration in {path.name}")
        for start in re.finditer(r"\breferences\b", text, re.I):
            match = _FK_RE.match(text, start.start())
            if match is None or text[match.end() :].lstrip().startswith("."):
                raise ValueError(f"unsupported reference declaration in {path.name}")
        for match in _CREATE_TABLE_RE.finditer(text):
            tables.setdefault(match.group(1), path.name)
        for statement in text.split(";"):
            declaration = re.search(
                r"\b(?:create\s+table\s+(?:if\s+not\s+exists\s+)?|alter\s+table\s+)"
                r"([a-z_][a-z_0-9]*)\b",
                statement,
                re.I,
            )
            if declaration:
                child = declaration.group(1).lower()
                for parent in _FK_RE.findall(statement):
                    raw_edges.setdefault(child, set()).add(parent.lower())
    edges = {child: tuple(sorted(parents)) for child, parents in raw_edges.items()}
    return TableGraph(tables=tables, edges=edges)


def ownership_paths(graph: TableGraph) -> dict[str, str]:
    """All FK dependency paths (including ambiguity/cycles), NOT owner authority."""

    def resolve(child: str, visiting: frozenset[str]) -> list[str]:
        if child in visiting:
            return [f"<cycle:{child}>"]
        parents = graph.edges.get(child, ())
        if not parents:
            return [f"root:{child}"]
        return sorted(
            {
                f"{path} <- {child}"
                for parent in parents
                for path in resolve(parent, visiting | {child})
            }
        )

    return {table: " | ".join(resolve(table, frozenset())) for table in sorted(graph.tables)}


# ---------------------------------------------------------------------------
# writer discovery (AST traversal, no runtime import)
# ---------------------------------------------------------------------------


@dataclass
class _FileState:
    module: str
    source: str
    literals: dict[str, set[str]] = field(default_factory=dict)  # all scope|name bindings
    dynamic_names: set[str] = field(default_factory=set)
    sites: list[WriterSite] = field(default_factory=list)


def _string_constant(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _joinedstr_text(node: ast.JoinedStr) -> str:
    parts = [
        v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
    ]
    return "".join(parts)


_TABLE_NAME_EXCLUSIONS = frozenset({"set", "values", "from", "where", "on", "if", "exists"})


def _table_targets(sql: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                name.lower()
                for name in TABLE_TARGET_RE.findall(
                    re.sub(r"\bfor\s+(?:no\s+key\s+)?update\b", "", sql, flags=re.I)
                )
                if name.lower() not in _TABLE_NAME_EXCLUSIONS
            }
        )
    )


def discover_writer_sites(python_root: Path) -> list[WriterSite]:
    """Discover potential mutation entry points under ``python_root`` (recursive)."""
    files = sorted(python_root.rglob("*.py"))
    sites: list[WriterSite] = []
    for path in files:
        relative = path.relative_to(python_root).with_suffix("")
        module = ".".join((python_root.name, *relative.parts))
        sites.extend(_scan_one_file(path, module))
    sites.sort(key=lambda site: (site.module, site.qualname, site.kind, site.fingerprint))
    return sites


def _walk_nodes(
    nodes: list[ast.stmt], scope: tuple[str, ...]
) -> "list[tuple[ast.AST, tuple[str, ...]]]":
    """Every AST node paired with its innermost enclosing scope name stack.

    Nested ``def``/``class`` bodies push their name; decorator/argument expressions
    of a scope stay at the outer scope (bounded simplification).
    """
    out: list[tuple[ast.AST, tuple[str, ...]]] = []
    for node in nodes:
        out.extend(_walk_node(node, scope))
    return out


def _walk_node(node: ast.AST, scope: tuple[str, ...]) -> "list[tuple[ast.AST, tuple[str, ...]]]":
    out: list[tuple[ast.AST, tuple[str, ...]]] = [(node, scope)]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        inner = scope + (node.name,)
        for child in node.body:
            out.extend(_walk_node(child, inner))
    else:
        for child in ast.iter_child_nodes(node):
            out.extend(_walk_node(child, scope))
    return out


def _scan_one_file(path: Path, module: str) -> list[WriterSite]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = _walk_nodes(tree.body, ())
    state = _FileState(module=module, source=source)

    # Pass 1: named SQL strings (assignments to string constants / f-strings).
    for node, scope in nodes:
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            state.dynamic_names.add(f"{'.'.join(scope)}|{node.target.id}")
            continue
        name, value = _binding_pair(node)
        if name is None or value is None:
            continue
        key = f"{'.'.join(scope)}|{name}"
        literal = _string_constant(value)
        if literal is not None:
            state.literals.setdefault(key, set()).add(literal)
        else:
            state.dynamic_names.add(key)

    # Pass 2: call sites.
    for node, scope in nodes:
        if isinstance(node, ast.Call):
            _handle_call(node, scope, state)
    return state.sites


def _binding_pair(node: ast.AST) -> "tuple[str | None, ast.expr | None]":
    target: ast.expr | None = None
    value: ast.expr | None = None
    if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
        target, value = node.targets[0], node.value
    elif (
        isinstance(node, ast.AnnAssign)
        and node.value is not None
        and isinstance(node.target, ast.Name)
    ):
        target, value = node.target, node.value
    if target is None:
        return None, None
    return target.id, value


def _call_name(callee: ast.expr) -> str:
    if isinstance(callee, ast.Attribute):
        return callee.attr
    if isinstance(callee, ast.Name):
        return callee.id
    return ""


def _scope_key(scope: tuple[str, ...]) -> str:
    return ".".join(scope)


def _handle_call(call: ast.Call, scope: tuple[str, ...], state: _FileState) -> None:
    qualname = _scope_key(scope) or "<module>"
    name = _call_name(call.func)
    if name in _EXECUTABLE_ATTRS:
        _handle_sql_call(call, scope, qualname, name, state)
        return
    if name == "open" and _open_has_write_mode(call):
        _record(state, qualname, "filesystem_write", call, (), unknown=True)
        return
    if not isinstance(call.func, ast.Name):
        if name in FS_MUTATION_ATTRS:
            _record(state, qualname, "filesystem_write", call, (), unknown=True)
        elif name in STORE_MUTATION_ATTRS:
            _record(state, qualname, "store_write", call, (), unknown=True)


def _mutates(sql: str) -> bool:
    # FOR UPDATE is a lock, not an UPDATE mutation. Other SQL function side
    # effects remain explicitly outside this bounded textual change detector.
    return bool(
        MUTATION_SQL_RE.search(re.sub(r"\bfor\s+(?:no\s+key\s+)?update\b", "", sql, flags=re.I))
    )


def _handle_sql_call(
    call: ast.Call, scope: tuple[str, ...], qualname: str, name: str, state: _FileState
) -> None:
    first = call.args[0] if call.args else None
    if first is None:
        _record(state, qualname, "sql_unresolvable", call, (), unknown=True)
        return
    literal = _string_constant(first)
    if literal is not None:
        if _mutates(literal):
            _record(state, qualname, "sql_inline", call, _table_targets(literal), unknown=False)
        return
    key = ""
    if isinstance(first, ast.Name):
        for size in range(len(scope), -1, -1):
            candidate = f"{_scope_key(scope[:size])}|{first.id}"
            if candidate in state.literals or candidate in state.dynamic_names:
                key = candidate
                break
    if key in state.dynamic_names:
        _record(state, qualname, "sql_unresolvable", call, (), unknown=True)
        return
    if key in state.literals:
        named_sql = "\n".join(sorted(state.literals[key]))
        if _mutates(named_sql):
            _record(
                state,
                qualname,
                "sql_named",
                call,
                _table_targets(named_sql),
                unknown=False,
                resolved=named_sql,
            )
        return
    if isinstance(first, ast.JoinedStr):
        text = _joinedstr_text(first)
        # Interpolation is not proven read-only or a complete table target.
        _record(state, qualname, "sql_dynamic", call, _table_targets(text), unknown=True)
        return
    # Unknown/unresolvable argument: recorded explicitly, never skipped.
    _record(state, qualname, "sql_unresolvable", call, (), unknown=True)


def _record(
    state: _FileState,
    qualname: str,
    kind: str,
    call: ast.Call,
    tables: "tuple[str, ...]",
    unknown: bool,
    resolved: str = "",
) -> None:
    segment = ast.get_source_segment(state.source, call) or "@grabbed"
    snippet = segment + resolved
    fingerprint = _fingerprint(kind, snippet)
    occurrence = 1
    while any(
        site.qualname == qualname and site.fingerprint == fingerprint for site in state.sites
    ):
        occurrence += 1
        fingerprint = _fingerprint(kind, f"{snippet}:occurrence{occurrence}")
    state.sites.append(
        WriterSite(
            module=state.module,
            qualname=qualname,
            kind=kind,
            line=call.lineno,
            fingerprint=fingerprint,
            tables=tables,
            unknown=unknown,
        )
    )


def _open_has_write_mode(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Attribute):
        return True  # includes os.open flags and unknown receiver overloads
    values: list[ast.expr] = []
    if len(call.args) > 1:
        values.append(call.args[1])
    values.extend(kw.value for kw in call.keywords if kw.arg == "mode")
    for value in values:
        literal = _string_constant(value)
        if literal is None or re.search(r"[wax+]", literal):
            return True
    return False


# ---------------------------------------------------------------------------
# manifest validation and change detection
# ---------------------------------------------------------------------------

_TABLE_KEY_RE = re.compile(r"^[a-z_][a-z_0-9]*$")


def site_identity(site: WriterSite) -> str:
    """Stable identity string for a discovered write site."""
    return f"{site.module}::{site.qualname}::{site.fingerprint}"


def entry_identity(entry: dict[str, object]) -> str:
    """Stable identity string for a manifest entry must exist."""
    module = str(entry.get("owner_source", ""))
    qualname = str(entry.get("qualname", ""))
    fingerprint = str(entry.get("fingerprint", ""))
    return f"{module}::{qualname}::{fingerprint}"


def validate_manifest_shape(manifest: object) -> list[str]:
    """Contract checks that do not depend on discovery (schema, claims, fields)."""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["shape/manifest-not-an-object"]
    schema = manifest.get("schema")
    if schema != MANIFEST_SCHEMA:
        problems.append(f"shape/schema-mismatch: {schema!r} != {MANIFEST_SCHEMA!r}")
    purpose = manifest.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        problems.append("shape/missing-purpose")

    # Forbidden completeness claims: the catalogue must not assert a runtime fence.
    # The limits block is documentation and is exempt; it records the gaps.
    banned_keys = {"all_runtime_fenced", "coverage", "fully_fenced", "complete_fence"}
    for key in manifest:
        if isinstance(key, str) and key.lower() in banned_keys:
            problems.append(f"claim/forbidden-key: {key}")

    def check_claims(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and key.lower() in banned_keys:
                    problems.append(f"claim/forbidden-key: {key}")
                check_claims(child)
        elif isinstance(value, list):
            for child in value:
                check_claims(child)

    check_claims({key: value for key, value in manifest.items() if key != "limits"})

    limits = manifest.get("limits")
    if not isinstance(limits, dict) or not all(
        isinstance(v, str) and v.strip() for v in limits.values()
    ):
        problems.append("shape/limits-must-be-nonempty-strings")
    elif not limits:
        problems.append("shape/missing-limits")

    tables: object = manifest.get("tables", {})
    if not isinstance(tables, dict) or not tables:
        problems.append("tables/missing-explicit-map")
    else:
        for name, entry in tables.items():
            if not isinstance(name, str) or not _TABLE_KEY_RE.match(name):
                problems.append(f"tables/wildcard-or-malformed-key: {name!r}")
            problems.extend(_validate_entry(name, entry, expect_paths=True))

    fk_edges: object = manifest.get("fk_edges", {})
    if not isinstance(fk_edges, dict) or not fk_edges:
        problems.append("edges/missing-explicit-map")
    else:
        for child, parent in fk_edges.items():
            if (
                not isinstance(child, str)
                or not isinstance(parent, list)
                or not parent
                or not all(isinstance(p, str) and p for p in parent)
            ):
                problems.append(f"edges/malformed-entry: {child!r}->{parent!r}")

    paths: object = manifest.get("ownership_paths", {})
    if not isinstance(paths, dict) or not paths:
        problems.append("paths/missing-explicit-map")
    else:
        for name, path in paths.items():
            if not isinstance(path, str) or not path.strip():
                problems.append(f"paths/malformed-entry: {name!r}")

    sites: object = manifest.get("writer_sites", [])
    if not isinstance(sites, list) or not sites:
        problems.append("writers/missing-explicit-list")
    else:
        for entry in sites:
            problems.extend(_validate_entry("writer_site", entry, expect_paths=False))
    return problems


def _validate_entry(name: str, entry: object, expect_paths: bool) -> list[str]:
    problems: list[str] = []
    if not isinstance(entry, dict):
        return [f"{name}/entry-not-an-object"]
    category = entry.get("category")
    if category not in CATEGORIES:
        problems.append(f"{name}/invalid-category: {category!r}")
    rationale = entry.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        problems.append(f"{name}/missing-rationale")
    owner_source = entry.get("owner_source")
    if not isinstance(owner_source, str) or not owner_source.strip():
        problems.append(f"{name}/missing-owner-source")
    if expect_paths:
        path = entry.get("ownership_path")
        if not isinstance(path, str) or not path.strip():
            problems.append(f"{name}/missing-ownership-path")
    else:
        uncovered = entry.get("uncovered")
        title_integration = (
            uncovered is False
            and (entry.get("owner_source"), entry.get("qualname")) == TITLE_GUARDED_SITE
            and entry.get("guard_contract") == TITLE_GUARD_CONTRACT
            and category == "content"
            and entry.get("kind") == "sql_inline"
            and entry.get("tables") == ["conversations"]
            and entry.get("unknown") is False
        )
        if uncovered is not True and not title_integration:
            problems.append(f"{name}/coverage-claim: uncovered={uncovered!r}")
        if "guard_contract" in entry and not title_integration:
            problems.append(f"{name}/unsupported-guard-contract")
        if entry.get("unknown") is True and category not in UNKNOWN_CATEGORIES:
            problems.append(f"{name}/unknown-flag-not-allowed-for-category: {category!r}")
        fingerprint = entry.get("fingerprint")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{16}", fingerprint):
            problems.append(f"{name}/malformed-fingerprint: {fingerprint!r}")
        qualname = entry.get("qualname")
        if not isinstance(qualname, str) or not qualname:
            problems.append(f"{name}/missing-qualname")
        if entry.get("kind") not in KNOWN_SITE_KINDS:
            problems.append(f"{name}/invalid-kind: {entry.get('kind')!r}")
        tables = entry.get("tables")
        if tables is not None and not (
            isinstance(tables, list) and all(isinstance(t, str) for t in tables)
        ):
            problems.append(f"{name}/tables-not-listed")
    return problems


KNOWN_SITE_KINDS = frozenset(
    {
        "sql_inline",
        "sql_named",
        "sql_dynamic",
        "sql_unresolvable",
        "filesystem_write",
        "store_write",
    }
)


def compare_manifest_to_discovery(
    migrations_dir: Path, python_dirs: "list[Path]", manifest: object
) -> list[str]:
    """Full audit: shape validation plus exact diff against current discovery.

    Returns a sorted list of discriminative problem codes; an empty list means the
    discovered set matches the explicit manifest exactly.
    """
    problems = validate_manifest_shape(manifest)
    if not isinstance(manifest, dict):
        return sorted(set(problems))

    graph = discover_tables(migrations_dir)
    discovered_paths = ownership_paths(graph)

    declared_tables = manifest.get("tables")
    if isinstance(declared_tables, dict):
        missing = sorted(graph.tables.keys() - declared_tables.keys())
        stale = sorted(declared_tables.keys() - graph.tables.keys())
        for table in missing:
            problems.append(f"tables/undeclared-discovered: {table}")
        for table in stale:
            problems.append(f"tables/declared-but-absent: {table}")

    declared_edges = manifest.get("fk_edges")
    if isinstance(declared_edges, dict):
        discovered_edges = {
            (child, parent) for child, parents in graph.edges.items() for parent in parents
        }
        declared_edge_set = {
            (child, parent)
            for child, parents in declared_edges.items()
            if isinstance(parents, list)
            for parent in parents
            if isinstance(parent, str)
        }
        missing_edges = sorted(discovered_edges - declared_edge_set)
        stale_edges = [
            f"{child}->{parent}" for child, parent in declared_edge_set - discovered_edges
        ]
        for child, parent in missing_edges:
            problems.append(f"edges/undeclared-discovered: {child}->{parent}")
        for edge in stale_edges:
            problems.append(f"edges/declared-mismatch: {edge}")

    declared_paths = manifest.get("ownership_paths")
    if isinstance(declared_paths, dict):
        for table, path in discovered_paths.items():
            if declared_paths.get(table) != path:
                problems.append(
                    f"paths/mismatch: {table}: {declared_paths.get(table)!r} != {path!r}"
                )

    declared_sites = manifest.get("writer_sites")
    if isinstance(declared_sites, list):
        discovered = {
            (site.module, site.qualname, site.fingerprint): site
            for site in _discover_sites(python_dirs)
        }
        declared = {}
        for entry in declared_sites:
            if isinstance(entry, dict):
                key = (
                    str(entry.get("owner_source", "")),
                    str(entry.get("qualname", "")),
                    str(entry.get("fingerprint", "")),
                )
                if key in declared:
                    problems.append(f"writers/duplicate-entry: {key}")
                declared[key] = entry
        for key in sorted(set(discovered) - set(declared)):
            site = discovered[key]
            problems.append(
                f"writers/unclassified-site: {site.module} {site.qualname} {site.kind} {site.fingerprint}"
            )
        for key in sorted(set(declared) - set(discovered)):
            problems.append(f"writers/stale-manifest-entry: {key[0]} {key[1]} {key[2]}")
        for key in sorted(set(discovered) & set(declared)):
            entry = declared[key]
            site = discovered[key]
            if str(entry.get("kind", "")) != site.kind:
                problems.append(f"writers/kind-mismatch: {key[0]} {key[1]} {key[2]}")
            if entry.get("unknown") is not site.unknown:
                problems.append(f"writers/unknown-flag-mismatch: {key[0]} {key[1]} {key[2]}")
            if not isinstance(entry.get("tables"), list):
                problems.append(f"writers/tables-not-listed: {key[0]} {key[1]} {key[2]}")
            else:
                targets = [str(t) for t in entry["tables"]]
                if set(site.tables) != set(targets):
                    problems.append(
                        f"writers/table-target-mismatch: {key[0]} {key[1]} {site.tables} != {targets}"
                    )
                for target in targets:
                    if isinstance(declared_tables, dict) and target not in declared_tables:
                        problems.append(f"writers/unclassified-target: {key[0]} {key[1]} {target}")
                problems.extend(_category_consistency(key, targets, declared_tables, entry))

    return sorted(set(problems))


def _category_consistency(
    key: "tuple[str, str, str]",
    targets: "list[str]",
    declared_tables: object,
    entry: dict[str, object],
) -> "list[str]":
    problems: list[str] = []
    if not isinstance(declared_tables, dict):
        return ["writers/category-validation-blocked"]
    target_categories = {
        cat
        for cat in (
            getattr(declared_tables.get(t), "get", lambda _k, d: d)("category", None)
            for t in targets
        )
        if cat is not None
    }
    if len(target_categories) == 1:
        expected = next(iter(target_categories))
        if entry.get("category") != expected and entry.get("unknown") is not True:
            problems.append(f"writers/category-mismatch: {key[0]} {key[1]} {key[2]}")
    elif len(target_categories) > 1 and entry.get("category") not in ("deferred", None):
        problems.append(f"writers/mixed-target-requires-deferred: {key[0]} {key[1]} {key[2]}")
    return problems


def _discover_sites(python_dirs: "list[Path]") -> "Iterator[WriterSite]":
    for root in python_dirs:
        yield from discover_writer_sites(root)
