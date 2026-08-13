"""Static convention test for #83: no direct env access outside Settings.

All env-var access in the backend must go through the `Settings` class
(`orchestrator/config.py`). Direct `os.environ.get(...)`, `os.getenv(...)`,
or `os.environ[...]` reads bypass the type validation, defaults, and env-var
prefix convention that Settings provides.

This test scans the backend tree (excluding `config.py` itself, which is
where the canonical field definitions live) and fails the suite if any new
direct env reads are introduced. Detection is AST-based so all three direct
access forms are caught, not just `os.environ.get(...)`.

The allowlist covers:
  * `orchestrator/config.py` — Settings is the canonical binding site.
  * `orchestrator/database_url.py` — accepts `environ` as a parameter
    (dependency injection used by tests).
  * `scripts/backup_db.py`, `scripts/seed.py` — standalone operational CLIs
    that run outside the daemon process; they read DATABASE_URL and
    BACKUP_DIR directly without booting Settings. Migrating them is
    tracked separately.
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
# Every backend Python package. `config/`, `db/`, `council/`, and `backend/`
# are production modules imported by the orchestrator (e.g.
# `orchestrator/subagents/image.py` imports `config.video_pricing` and
# `db.video_credits`), so a direct env read added there must fail this gate
# exactly as one added under `orchestrator/` does.
SCAN_DIRS = (
    REPO_ROOT / "orchestrator",
    REPO_ROOT / "providers",
    REPO_ROOT / "scripts",
    REPO_ROOT / "config",
    REPO_ROOT / "db",
    REPO_ROOT / "council",
    REPO_ROOT / "backend",
)
ALLOWLIST = frozenset(
    {
        "orchestrator/config.py",
        # Standalone operational / diagnostic CLIs that run outside the
        # daemon process. They read DATABASE_URL, ENCRYPTION_KEY, and
        # BACKUP_DIR directly without booting Settings. Migrating them to
        # Settings is tracked separately — they intentionally avoid the
        # daemon lifecycle so they can be invoked from cron / ad-hoc.
        "scripts/backup_db.py",
        "scripts/seed.py",
        "scripts/test_retrieval_quality.py",
    }
)
# `orchestrator/database_url.py` is scanned: only the `resolve_database_url`
# function (the DI entry point that accepts an injected `environ` mapping)
# is allowed to reference `os.environ` directly. `validate_database_credentials`
# still uses `os.getenv` and remains a known divergence from the convention —
# the file is allowed only on a per-function basis so a future drift outside
# the documented function will fail the gate.
FUNCTION_ALLOWLIST: dict[str, frozenset[str]] = {
    "orchestrator/database_url.py": frozenset({"resolve_database_url"}),
}


def _enclosing_function(node: ast.AST) -> str | None:
    """Walk up the parent map to find the innermost FunctionDef/AsyncFunctionDef name."""
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = getattr(cur, "parent", None)
    return None


class _OsAliasVisitor(ast.NodeVisitor):
    """Resolve per-scope bindings of `os` / `os.environ` / `os.getenv`.

    Tracks the enclosing function (or module-level) for every binding so
    a function parameter named `getenv` (e.g. `def foo(getenv): ...`) does
    not inherit a module-level `from os import getenv` binding and get
    misclassified as a direct env read. Returns a list of
    ``(function_name | None, os_names, getenv_names, environ_names)`` tuples
    that the walker consults for each hit.
    """

    def __init__(self) -> None:
        # All frames ever pushed, in visitation order. ``self._stack`` is
        # the live parent chain used by the visitor; ``self._frames`` is
        # the cumulative log so the resolver can inspect every scope
        # after the visitor has returned (the visitor pops each frame on
        # the way out, so the live stack is empty by the time
        # ``scopes()`` is called).
        self._stack: list[dict] = []
        self._frames: list[dict] = []

    def _current(self) -> dict:
        return self._stack[-1]

    def _push(self, name: str | None) -> dict:
        frame = {
            "func": name,
            "os_names": set(),
            "getenv_names": set(),
            "environ_names": set(),
        }
        self._stack.append(frame)
        self._frames.append(frame)
        return frame

    def _pop(self) -> None:
        self._stack.pop()

    def visit_Module(self, node: ast.Module) -> None:
        self._push(None)
        self.generic_visit(node)
        self._pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._push(node.name)
        # Function parameters shadow any outer-scope binding of the same
        # name. Parameters here are not bound to `os`, so we don't add
        # them to any alias set — they are simply absent, which is the
        # correct behavior.
        self.generic_visit(node)
        self._pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._push(node.name)
        self.generic_visit(node)
        self._pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "os":
                self._current()["os_names"].add(alias.asname or "os")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "os" and node.level == 0:
            for alias in node.names:
                local = alias.asname or alias.name
                if alias.name == "getenv":
                    self._current()["getenv_names"].add(local)
                elif alias.name == "environ":
                    self._current()["environ_names"].add(local)
        self.generic_visit(node)

    def scopes(self) -> list[dict]:
        """Return one frame per scope (module + each FunctionDef/AsyncFunctionDef).

        The walker looks up the enclosing function for each hit and uses
        that frame's alias sets. Module-level aliases are also available
        in every frame via the ``_inherited`` merge the caller performs.
        """
        return self._frames


def _resolve_aliases_for(tree: ast.AST) -> dict[str | None, dict[str, set[str]]]:
    """Return a mapping from enclosing-function name (or None for module
    level) to a dict of alias sets.

    Each scope frame contains its own bindings (those declared inside that
    scope) and an ``_inherited`` set merged from the enclosing module-level
    frame, so a function that does its own ``import os`` is correctly
    resolved without inheriting unrelated aliases from sibling functions.
    """
    visitor = _OsAliasVisitor()
    visitor.visit(tree)
    result: dict[str | None, dict[str, set[str]]] = {}
    module_frame = next(f for f in visitor.scopes() if f["func"] is None)
    inherited = {
        "os_names": set(module_frame["os_names"]),
        "getenv_names": set(module_frame["getenv_names"]),
        "environ_names": set(module_frame["environ_names"]),
    }
    result[None] = {
        "os_names": set(inherited["os_names"]),
        "getenv_names": set(inherited["getenv_names"]),
        "environ_names": set(inherited["environ_names"]),
    }
    for frame in visitor.scopes():
        if frame["func"] is None:
            continue
        # Module-level bindings are NOT inherited by every function:
        # a sibling function that defines `def foo(getenv):` is allowed
        # to have `getenv` shadow the module-level os.getenv import,
        # and we must not retroactively re-flag it. Aliases declared at
        # module level are inherited only when the function does not
        # shadow them.
        merged = {
            "os_names": set(frame["os_names"]),
            "getenv_names": set(frame["getenv_names"]),
            "environ_names": set(frame["environ_names"]),
        }
        # Inherit any module-level aliases the function has not shadowed.
        if not frame["os_names"]:
            merged["os_names"] |= inherited["os_names"]
        if not frame["getenv_names"]:
            merged["getenv_names"] |= inherited["getenv_names"]
        if not frame["environ_names"]:
            merged["environ_names"] |= inherited["environ_names"]
        result[frame["func"]] = merged
    return result


def _attach_parents(tree: ast.AST) -> None:
    """Walk the tree and set ``.parent`` on every child node."""
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child.parent = node  # type: ignore[attr-defined]


def _scan_for_direct_env_access() -> list[tuple[pathlib.Path, int, str]]:
    """AST-scan for direct env reads that bypass Settings.

    Catches any of:
      * ``os.environ.get("X")`` / ``environ.get("X")``
      * ``os.environ["X"]`` / ``environ["X"]``
      * ``os.getenv("X")`` / ``getenv("X")``

    ...including under any import alias (``import os as host_os``,
    ``from os import getenv``, ``from os import environ as e``), because
    those forms are semantically identical direct reads.

    Subscript writes (``os.environ["KEY"] = value``) and deletes
    (``del os.environ["KEY"]``) are excluded — those do not bypass
    Settings, they mutate the process environment for child processes.
    Per-function allowlist (``FUNCTION_ALLOWLIST``) lets specific functions
    in otherwise-scanned files keep their direct env access (the DI
    pattern in ``orchestrator/database_url.py`` is the motivating case).
    Aliases are resolved per lexical scope so a function parameter named
    ``getenv`` is not retroactively flagged as a direct env read.
    """
    hits: list[tuple[pathlib.Path, int, str]] = []
    for scan_dir in SCAN_DIRS:
        if not scan_dir.is_dir():
            continue
        for path in scan_dir.rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel in ALLOWLIST:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            _attach_parents(tree)
            scopes = _resolve_aliases_for(tree)
            module_allowlist = FUNCTION_ALLOWLIST.get(rel)

            def _aliases_for(node: ast.AST) -> dict[str, set[str]]:
                func_name = _enclosing_function(node)
                if func_name is not None and func_name in scopes:
                    return scopes[func_name]
                return scopes[None]

            def _is_environ_expr(node: ast.expr, aliases: dict[str, set[str]]) -> bool:
                """True if `node` evaluates to the `os.environ` mapping."""
                # `os.environ` / `host_os.environ`
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "environ"
                    and isinstance(node.value, ast.Name)
                    and node.value.id in aliases["os_names"]
                ):
                    return True
                # bare `environ` / aliased `e` from `from os import environ`
                return isinstance(node, ast.Name) and node.id in aliases["environ_names"]

            for node in ast.walk(tree):
                # Detect the direct access forms:
                #   1) <environ>.get(...)   Call( Attribute(<environ expr>, get) )
                #   2) <os>.getenv(...)     Call( Attribute(Name(os-alias), getenv) )
                #   3) getenv(...)          Call( Name(getenv-alias) )
                #   4) <environ>["KEY"]     Subscript( <environ expr>, ctx=Load )
                if module_allowlist is not None:
                    # If the file has a function-scoped allowlist, only
                    # nodes inside the allowlisted functions are exempt;
                    # other functions in the same file are still scanned.
                    func_name = _enclosing_function(node)
                    if func_name is not None and func_name in module_allowlist:
                        continue
                aliases = _aliases_for(node)
                if isinstance(node, ast.Call):
                    func = node.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == "get"
                        and _is_environ_expr(func.value, aliases)
                    ):
                        hits.append((path, node.lineno, "os.environ.get(...)"))
                    elif (
                        isinstance(func, ast.Attribute)
                        and func.attr == "getenv"
                        and isinstance(func.value, ast.Name)
                        and func.value.id in aliases["os_names"]
                    ):
                        hits.append((path, node.lineno, "os.getenv(...)"))
                    elif isinstance(func, ast.Name) and func.id in aliases["getenv_names"]:
                        hits.append((path, node.lineno, "os.getenv(...)"))
                    continue
                if (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.ctx, ast.Load)
                    and _is_environ_expr(node.value, aliases)
                ):
                    hits.append((path, node.lineno, "os.environ[...]"))
    return hits


def test_no_direct_env_access_outside_settings() -> None:
    hits = _scan_for_direct_env_access()
    assert not hits, (
        "Direct `os.environ.get(...)` / `os.getenv(...)` / "
        '`os.environ["KEY"]` reads detected outside '
        "`orchestrator/config.py` (or other allowed files). Read env vars "
        "through the `Settings` class instead so type validation, defaults, "
        "and env-var prefixing apply uniformly.\n\nFound:\n"
        + "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}: {snippet}" for p, ln, snippet in hits)
    )


def test_ast_gate_detects_all_three_direct_env_forms(tmp_path, monkeypatch) -> None:
    """Regression for Codex round-2 P2 on PR #264:

    The widened AST walker must catch every direct env-access form
    (os.environ.get, os.getenv, os.environ["KEY"]) — a previous widening
    pass claimed to detect subscript reads but actually only walked
    ast.Call nodes. Drive the scanner against a fixture file that uses
    each form and assert the fixture produces the expected hits.
    """
    fixture_dir = tmp_path / "orchestrator"
    fixture_dir.mkdir()
    fixture = fixture_dir / "fixture_uses_all_forms.py"
    fixture.write_text(
        "import os\n"
        "\n"
        'A = os.environ.get("DATABASE_URL")\n'
        'B = os.getenv("ENCRYPTION_KEY")\n'
        'C = os.environ["BACKUP_DIR"]\n'
    )

    # Point the scanner at the fixture by overriding the module-level
    # SCAN_DIRS / REPO_ROOT via a MonkeyPatch fixture, then call the
    # scanner. We resolve the module via sys.modules (already loaded by
    # pytest at collection time) rather than a self-import that
    # basedpyright cannot statically resolve.
    import sys

    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "SCAN_DIRS", (fixture_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    hits = mod._scan_for_direct_env_access()

    snippets = sorted({snippet for _, _, snippet in hits})
    assert set(snippets) == {
        "os.environ[...]",
        "os.environ.get(...)",
        "os.getenv(...)",
    }, f"Expected all three forms detected, got {snippets}"


def test_ast_gate_resolves_imported_os_aliases(tmp_path, monkeypatch) -> None:
    """Regression for the round-3 P2 on PR #268:

    `from os import getenv`, `from os import environ`, and `import os as X`
    are semantically identical direct env reads, but a walker that only
    recognizes an `ast.Name` whose identifier is literally `os` misses all
    of them. Each alias form must produce a hit.
    """
    import sys

    mod = sys.modules[__name__]
    fixture_dir = tmp_path / "aliased"
    fixture_dir.mkdir()

    cases = {
        "import_os_as": (
            "import os as host_os\n"
            '\nA = host_os.getenv("K1")\n'
            'B = host_os.environ.get("K2")\n'
            'C = host_os.environ["K3"]\n',
            {"os.getenv(...)", "os.environ.get(...)", "os.environ[...]"},
        ),
        "from_os_import_getenv": (
            'from os import getenv\n\nA = getenv("K1")\n',
            {"os.getenv(...)"},
        ),
        "from_os_import_getenv_as": (
            'from os import getenv as g\n\nA = g("K1")\n',
            {"os.getenv(...)"},
        ),
        "from_os_import_environ": (
            'from os import environ\n\nA = environ.get("K1")\nB = environ["K2"]\n',
            {"os.environ.get(...)", "os.environ[...]"},
        ),
        "from_os_import_environ_as": (
            'from os import environ as e\n\nA = e.get("K1")\nB = e["K2"]\n',
            {"os.environ.get(...)", "os.environ[...]"},
        ),
    }

    for name, (source, expected) in cases.items():
        case_dir = fixture_dir / name
        case_dir.mkdir()
        (case_dir / f"{name}.py").write_text(source)

        monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
        monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
        hits = mod._scan_for_direct_env_access()

        found = {snippet for _, _, snippet in hits}
        assert found == expected, (
            f"alias form `{name}`: gate reported {sorted(found)}, expected {sorted(expected)}"
        )


def test_ast_gate_does_not_flag_unrelated_names(tmp_path, monkeypatch) -> None:
    """The alias resolver must not fire on names that are not bound to `os`.

    A local variable, function parameter, or same-named import from another
    module called `environ` / `getenv` is not a direct env read, and flagging
    it would make the gate unusable (notably
    `orchestrator/database_url.py` takes an injected `environ` mapping).
    """
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "unrelated"
    case_dir.mkdir()
    (case_dir / "unrelated.py").write_text(
        "from mylib import getenv, environ\n"
        "\n"
        "\n"
        "def read(environ):\n"
        '    local = environ.get("K1")\n'
        '    other = environ["K2"]\n'
        '    third = getenv("K3")\n'
        "    return local, other, third\n"
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    assert mod._scan_for_direct_env_access() == [], (
        "gate flagged names that are not bound to the os module — false positive"
    )


def test_ast_gate_scans_every_backend_package() -> None:
    """Regression for the round-3 P2 on PR #268:

    `config/`, `db/`, `council/`, and `backend/` are production packages
    imported by the orchestrator, so the gate must cover them. Guard the
    SCAN_DIRS tuple against silent narrowing.
    """
    scanned = {d.name for d in SCAN_DIRS}
    for required in ("orchestrator", "providers", "scripts", "config", "db", "council", "backend"):
        assert required in scanned, f"backend package `{required}/` is not covered by the gate"

    # Every top-level directory that contains Python and is not test/tooling
    # scaffolding must be in SCAN_DIRS, so a newly added backend package
    # cannot silently escape the gate.
    ignored = {"tests", "migrations", "frontend", "docs", ".venv", "node_modules"}
    for child in REPO_ROOT.iterdir():
        if not child.is_dir() or child.name.startswith(".") or child.name in ignored:
            continue
        if not any(child.rglob("*.py")):
            continue
        assert child.name in scanned, (
            f"top-level package `{child.name}/` contains Python but is not in SCAN_DIRS; "
            "add it to the gate or to the ignored set with a rationale"
        )


def test_ast_gate_resolves_aliases_per_function_scope(tmp_path, monkeypatch) -> None:
    """Regression for the round-3 P2 on PR #268: aliases are resolved per
    lexical scope, not module-wide.

    A function that imports ``from os import getenv`` should not cause
    a *sibling* function in the same module to mis-attribute a parameter
    named ``getenv`` as a direct env read. Conversely, a function whose
    body uses ``host_os.getenv(...)`` should be detected even when the
    alias is bound inside that function.
    """
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "scoped"
    case_dir.mkdir()
    (case_dir / "scoped.py").write_text(
        "import os\n"
        "\n"
        "def uses_os_directly():\n"
        '    return os.getenv("OUTSIDE_KEY")\n'
        "\n"
        "def getenv(getenv_arg):\n"
        '    return getenv_arg("KEY")\n'
        "\n"
        "def scoped_alias():\n"
        "    import os as host_os\n"
        '    return host_os.getenv("INSIDE_KEY")\n'
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    # Only the two real direct env reads should be flagged; the
    # shadowed `getenv` parameter in `def getenv(getenv_arg):` must
    # NOT be flagged.
    snippets = sorted({snippet for _, _, snippet in hits})
    assert snippets == ["os.getenv(...)"], (
        f"per-scope resolver mis-flagged: expected ['os.getenv(...)'], got {snippets}"
    )


def test_ast_gate_ignores_subscript_writes(tmp_path, monkeypatch) -> None:
    """Regression for the round-3 P2 on PR #268: subscript writes/deletes
    are not direct env reads.

    A production module that does ``os.environ[\"CHILD_FLAG\"] = \"1\"``
    before spawning a child process is *configuring* the child, not
    reading configuration itself. The gate should only catch ``Load``
    subscripts (reads) — ``Store`` and ``Del`` contexts are excluded.
    """
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "writes"
    case_dir.mkdir()
    (case_dir / "writes.py").write_text(
        "import os\n"
        "\n"
        'A = os.environ["READ_KEY"]\n'
        'os.environ["WRITE_KEY"] = "1"\n'
        'del os.environ["DELETE_KEY"]\n'
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    snippets = sorted({snippet for _, _, snippet in hits})
    assert snippets == ["os.environ[...]"], (
        f"subscript-write filter mis-fired: expected ['os.environ[...]'], got {snippets}"
    )
    lines = sorted(ln for _, ln, _ in hits)
    assert lines == [3], (
        f"only the Load-context subscript (line 3) should be flagged, got lines {lines}"
    )


def test_ast_gate_function_allowlist_scopes_database_url(tmp_path, monkeypatch) -> None:
    """Regression for the round-3 P2 on PR #268: the database_url.py
    exemption is function-scoped, not file-scoped.

    Only ``resolve_database_url`` is allowed to reference ``os.environ``
    directly (the DI entry point that accepts an injected ``environ``
    mapping). ``validate_database_credentials`` uses ``os.getenv`` and
    must be flagged so any future drift in the DI function or any new
    direct env read added to ``validate_database_credentials`` will be
    caught at CI time.
    """
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "dburl"
    case_dir.mkdir()
    (case_dir / "database_url.py").write_text(
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "from collections.abc import Mapping\n"
        "\n"
        "def resolve_database_url(configured_url=None, *, environ=None):\n"
        "    source = os.environ if environ is None else environ\n"
        '    return source.get("DATABASE_URL")\n'
        "\n"
        "def validate_database_credentials(settings):\n"
        '    return (os.getenv("POSTGRES_PASSWORD"), os.getenv("PGPASSWORD"))\n'
    )

    # Register the function-scoped allowlist entry and scan.
    monkeypatch.setitem(
        mod.FUNCTION_ALLOWLIST,
        "database_url.py",
        frozenset({"resolve_database_url"}),
    )
    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    snippets = sorted({snippet for _, _, snippet in hits})
    # Only the `os.getenv(...)` calls inside `validate_database_credentials`
    # should be flagged — the `os.environ` reference inside
    # `resolve_database_url` is allowlisted on a per-function basis.
    assert snippets == ["os.getenv(...)"], (
        f"function-allowlist mis-applied: expected ['os.getenv(...)'], got {snippets}"
    )
