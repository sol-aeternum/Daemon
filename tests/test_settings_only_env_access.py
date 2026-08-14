"""Static convention test for #83: no direct env access outside Settings.

All env-var access in the backend must go through the `Settings` class
(`orchestrator/config.py`). Direct `os.environ.get(...)`, `os.getenv(...)`,
`os.environ[...]`, or membership reads bypass the type validation, defaults,
and env-var prefix convention that Settings provides.

This test scans the backend tree (excluding `config.py` itself, which is
where the canonical field definitions live) and fails the suite if any new
direct env reads are introduced. Detection is AST-based so every supported
direct-access form is caught, not just `os.environ.get(...)`.

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


def _enclosing_function(node: ast.AST) -> ast.AST | None:
    """Return the callable scope in which ``node`` is evaluated.

    Keying alias frames by AST-node identity (not the function name) means two
    same-named methods in different enclosing classes resolve to distinct frames
    — e.g. ``ClassA.configure`` and ``ClassB.configure`` get independent alias
    tables, so ``ClassA.configure``'s ``import os as host_os`` does not collide
    with ``ClassB.configure``'s ``host_os`` parameter.

    Function decorators, defaults, and annotations are evaluated in the
    enclosing scope, before the new function's parameters exist. Lambda
    defaults follow the same rule, while lambda bodies use their own frame.
    """
    child = node
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child in cur.body:
                return cur
        elif isinstance(cur, ast.Lambda) and child is cur.body:
            return cur
        child = cur
        cur = getattr(cur, "parent", None)
    return None


def _enclosing_alias_scope(node: ast.AST) -> ast.AST | None:
    """Return the scope whose bindings are visible while ``node`` runs.

    Class bodies have their own namespace, but function and lambda bodies
    nested inside a class do not close over that namespace. Definition-time
    expressions (bases, decorators, defaults, and annotations) remain in the
    parent scope until execution enters the corresponding ``body`` field.
    """
    child = node
    cur = getattr(node, "parent", None)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if child in cur.body:
                return cur
        elif isinstance(cur, ast.Lambda) and child is cur.body:
            return cur
        child = cur
        cur = getattr(cur, "parent", None)
    return None


def _qualified_function_name(node: ast.AST) -> str | None:
    """Return the dotted path to the effective enclosing function, or None.

    Example: a ``configure`` method inside ``class Foo`` resolves to
    ``"Foo.configure"`` — so the ``FUNCTION_ALLOWLIST`` and the per-scope
    alias frames stay aligned when two same-named methods coexist. Definition-
    time expressions are intentionally attributed to the parent scope, so an
    allowlisted function cannot also exempt direct reads in its defaults or
    decorators.
    """
    parts: list[str] = []
    scope = _enclosing_function(node)
    while scope is not None:
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parts.append(scope.name)
        parent_scope = _enclosing_function(scope)
        cur = getattr(scope, "parent", None)
        while cur is not None and cur is not parent_scope:
            if isinstance(cur, ast.ClassDef):
                parts.append(cur.name)
            cur = getattr(cur, "parent", None)
        scope = parent_scope
    if not parts:
        return None
    return ".".join(reversed(parts))


class _OsAliasVisitor(ast.NodeVisitor):
    """Resolve per-scope bindings of `os` / `os.environ` / `os.getenv`.

    Tracks module, class-body, function, and lambda bindings separately.
    Nested callables inherit aliases from their immediate callable parent
    while bypassing class namespaces, matching Python's lexical lookup.
    Parameters and local assignments shadow same-named outer aliases.
    """

    def __init__(self) -> None:
        # All frames ever pushed, in visitation order. ``self._stack`` is
        # the live parent chain used by the visitor; ``self._frames`` is
        # the cumulative log so the resolver can inspect every scope
        # after the visitor has returned (the visitor pops each frame on
        # the way out, so the live stack is empty by the time
        # ``scopes()`` is called). Each frame records the AST node the
        # visitor pushed so the resolver can key alias tables by
        # node identity (same-named methods in different enclosing
        # classes resolve to distinct frames).
        self._stack: list[dict] = []
        self._frames: list[dict] = []

    def _current(self) -> dict:
        return self._stack[-1]

    def _push(self, node: ast.AST) -> dict:
        # Module → module-level frame keyed by None.
        # FunctionDef/AsyncFunctionDef → method-level frame keyed by the
        # node itself (so two same-named methods in different enclosing
        # classes resolve to distinct frames).
        # ClassDef → class-body frame. Methods bypass this frame because
        # bare names in a method do not resolve through the class namespace.
        # Lambda → lambda frame keyed by the node itself; lambdas inherit
        # aliases from the immediate enclosing function.
        frame: dict
        if isinstance(node, ast.Module):
            frame = {
                "kind": "module",
                "node": node,
                "parent_node": None,
                "bound_names": set(),
                "global_names": set(),
                "nonlocal_names": set(),
                "binding_events": [],
            }
        else:
            assert isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
            )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "function"
            elif isinstance(node, ast.ClassDef):
                kind = "class"
            else:
                kind = "lambda"
            lexical_parent = next(
                (
                    frame["node"]
                    for frame in reversed(self._stack)
                    if frame["kind"] in {"function", "lambda"}
                ),
                None,
            )
            frame = {
                "kind": kind,
                "node": node,
                "parent_node": lexical_parent,
                "bound_names": set(),
                "global_names": set(),
                "nonlocal_names": set(),
                "binding_events": [],
            }
        self._stack.append(frame)
        self._frames.append(frame)
        return frame

    def _pop(self) -> None:
        self._stack.pop()

    def visit_Module(self, node: ast.Module) -> None:
        self._push(node)
        self.generic_visit(node)
        self._pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_definition_expressions(node)
        frame = self._push(node)
        frame["bound_names"].update(self._argument_names(node.args))
        for statement in node.body:
            self.visit(statement)
        self._pop()
        self._record_direct_binding(node.name, None)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_definition_expressions(node)
        frame = self._push(node)
        frame["bound_names"].update(self._argument_names(node.args))
        for statement in node.body:
            self.visit(statement)
        self._pop()
        self._record_direct_binding(node.name, None)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)
        self._push(node)
        for statement in node.body:
            self.visit(statement)
        self._pop()
        self._record_direct_binding(node.name, None)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        frame = self._push(node)
        frame["bound_names"].update(self._argument_names(node.args))
        self.visit(node.body)
        self._pop()

    def _visit_function_definition_expressions(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        """Visit definition-time expressions in the enclosing scope."""
        for decorator in node.decorator_list:
            self.visit(decorator)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.args.vararg is not None and node.args.vararg.annotation is not None:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
            self.visit(node.args.kwarg.annotation)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        for type_param in getattr(node, "type_params", ()):
            self.visit(type_param)

    @staticmethod
    def _argument_names(arguments: ast.arguments) -> set[str]:
        names = {
            argument.arg
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
        }
        if arguments.vararg is not None:
            names.add(arguments.vararg.arg)
        if arguments.kwarg is not None:
            names.add(arguments.kwarg.arg)
        return names

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._current()["bound_names"].add(node.id)
            self._record_direct_binding(node.id, None)
        self.generic_visit(node)

    def _record_direct_binding(self, name: str, source: ast.expr | str | None) -> None:
        """Record a binding event in execution order for alias propagation."""
        self._current()["bound_names"].add(name)
        self._current()["binding_events"].append((name, source))

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
            if isinstance(target, ast.Name):
                self._record_direct_binding(target.id, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        if isinstance(node.target, ast.Name):
            self._record_direct_binding(node.target.id, node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self.visit(node.target)
        if isinstance(node.target, ast.Name):
            self._record_direct_binding(node.target.id, node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._record_direct_binding(node.target.id, None)

    def visit_Global(self, node: ast.Global) -> None:
        self._current()["global_names"].update(node.names)
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self._current()["nonlocal_names"].update(node.names)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self._record_direct_binding(local, "os" if alias.name == "os" else None)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                local = alias.asname or alias.name
                source: str | None = None
                if node.module == "os" and node.level == 0:
                    if alias.name == "getenv":
                        source = "getenv"
                    elif alias.name == "environ":
                        source = "environ"
                self._record_direct_binding(local, source)

    def scopes(self) -> list[dict]:
        """Return one frame per scope (module, class bodies, functions, and lambdas).

        The scanner looks up the effective evaluation scope for each hit.
        Each callable records its immediate callable parent so closure aliases
        are inherited without leaking through class or sibling scopes.
        """
        return self._frames


def _resolve_aliases_for(tree: ast.AST) -> dict[ast.AST | None, dict[str, set[str]]]:
    """Return a mapping from alias-scope node (or None for module level).

    Each scope frame contains its own bindings plus aliases inherited from
    its immediate lexical parent. Local bindings remove same-named inherited
    aliases, while ``global`` and ``nonlocal`` declarations keep the outer
    binding visible. This mirrors closure lookup without leaking aliases
    across sibling functions.

    Keyed by AST-node identity so two same-named methods in different
    enclosing classes (``ClassA.configure`` vs ``ClassB.configure``)
    resolve to distinct frames; a string-name key would let the later
    frame overwrite the earlier one and silently bypass the gate.
    """
    alias_kinds = ("os_names", "getenv_names", "environ_names")

    def _kind_for_source(source: ast.expr | str | None, aliases: dict[str, set[str]]) -> str | None:
        if source == "os":
            return "os_names"
        if source == "getenv":
            return "getenv_names"
        if source == "environ":
            return "environ_names"
        if not isinstance(source, ast.expr):
            return None
        if isinstance(source, ast.Name):
            for alias_kind in alias_kinds:
                if source.id in aliases[alias_kind]:
                    return alias_kind
            return None
        if isinstance(source, ast.Attribute) and isinstance(source.value, ast.Name):
            if source.value.id not in aliases["os_names"]:
                return None
            if source.attr == "getenv":
                return "getenv_names"
            if source.attr == "environ":
                return "environ_names"
        return None

    visitor = _OsAliasVisitor()
    visitor.visit(tree)
    result: dict[ast.AST | None, dict[str, set[str]]] = {}
    empty_aliases = {alias_kind: set() for alias_kind in alias_kinds}
    for frame in visitor.scopes():
        parent_aliases = result.get(frame["parent_node"], empty_aliases)
        shadowed_names = (
            set(frame["bound_names"]) - set(frame["global_names"]) - set(frame["nonlocal_names"])
        )
        merged = {
            alias_kind: set(parent_aliases[alias_kind]) - shadowed_names
            for alias_kind in alias_kinds
        }
        for name, source in frame["binding_events"]:
            for alias_kind in alias_kinds:
                merged[alias_kind].discard(name)
            source_kind = _kind_for_source(source, merged)
            if source_kind is not None:
                merged[source_kind].add(name)

        # Module scope keeps the public ``None`` key. Every other scope is
        # keyed by AST-node identity so same-named classes/methods cannot
        # overwrite one another.
        scope_key = None if frame["kind"] == "module" else frame["node"]
        result[scope_key] = merged
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
      * ``"X" in os.environ`` / ``"X" not in environ``

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
    Function defaults and decorators are evaluated in the enclosing
    scope (the parameter names do not exist yet at default-evaluation
    time), so the resolver visits those nodes with the parent frame
    active. Lambdas get their own scope keyed by the AST node so a
    lambda parameter shadows the enclosing alias while a lambda body
    without a shadow still sees the inherited alias.
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
                scope_node = _enclosing_alias_scope(node)
                if scope_node is not None and scope_node in scopes:
                    return scopes[scope_node]
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
                #   5) "KEY" in <environ>   Compare(..., In/NotIn, <environ expr>)
                if module_allowlist is not None:
                    # If the file has a function-scoped allowlist, only
                    # nodes inside the allowlisted functions are exempt;
                    # other functions in the same file are still scanned.
                    # Use the qualified lexical path (``ClassName.method``)
                    # so two same-named methods in different enclosing
                    # classes resolve to distinct allowlist keys.
                    func_qname = _qualified_function_name(node)
                    if func_qname is not None and func_qname in module_allowlist:
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
                if isinstance(node, ast.Compare):
                    if any(
                        isinstance(operator, (ast.In, ast.NotIn))
                        and _is_environ_expr(comparator, aliases)
                        for operator, comparator in zip(node.ops, node.comparators, strict=True)
                    ):
                        hits.append((path, node.lineno, "membership in os.environ"))
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
        "Direct `os.environ.get(...)` / `os.getenv(...)` / membership / "
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
        'def resolve_database_url(configured_url=os.getenv("DEFAULT"), *, environ=None):\n'
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
    lines = sorted(line for _, line, _ in hits)
    assert lines == [6, 11, 11]


def test_ast_gate_resolves_same_named_methods_in_distinct_classes(tmp_path, monkeypatch) -> None:
    """Regression for the round-4 P2 on PR #268: alias frames are keyed by
    AST-node identity, not function name.

    Two classes with a same-named method that share the file:
        class A:
            def configure(self):
                import os as host_os
                return host_os.getenv("K1")
        class B:
            def configure(self, host_os):  # unrelated parameter
                return host_os.get("K2")

    A string-name key would let B's frame overwrite A's, and
    ``host_os.getenv("K1")`` would silently bypass the gate. With
    node-identity keying, A's frame retains its ``host_os`` alias and
    the direct env read is detected.
    """
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "classes"
    case_dir.mkdir()
    (case_dir / "classes.py").write_text(
        "class A:\n"
        "    def configure(self):\n"
        "        import os as host_os\n"
        '        return host_os.getenv("K1")\n'
        "\n"
        "class B:\n"
        "    def configure(self, host_os):\n"
        '        return host_os.get("K2")\n'
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    snippets = sorted({snippet for _, _, snippet in hits})
    assert snippets == ["os.getenv(...)"], (
        f"node-keyed resolver mis-flagged: expected ['os.getenv(...)'], got {snippets}"
    )
    lines = sorted(ln for _, ln, _ in hits)
    assert lines == [4], (
        f"only A.configure's host_os.getenv (line 4) should be flagged, got lines {lines}"
    )


def test_ast_gate_inherits_aliases_from_immediate_lexical_parent(tmp_path, monkeypatch) -> None:
    """Nested functions inherit parent aliases unless a local binding shadows them."""
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "closures"
    case_dir.mkdir()
    (case_dir / "closures.py").write_text(
        "def inherited():\n"
        "    import os as host_os\n"
        "    def inner():\n"
        '        return host_os.getenv("K1")\n'
        "    return inner\n"
        "\n"
        "def shadowed():\n"
        "    import os as host_os\n"
        "    def inner(host_os):\n"
        '        return host_os.getenv("NOT_OS")\n'
        "    return inner\n"
        "\n"
        "def inherited_alongside_local_alias():\n"
        "    import os as outer_os\n"
        "    def inner():\n"
        "        import os as inner_os\n"
        '        return outer_os.getenv("K2"), inner_os.environ["K3"]\n'
        "    return inner\n"
        "\n"
        "def declared_nonlocal():\n"
        "    import os as host_os\n"
        "    def inner():\n"
        "        nonlocal host_os\n"
        '        return host_os.environ.get("K4")\n'
        "    return inner\n"
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    snippets = sorted(snippet for _, _, snippet in hits)
    assert snippets == [
        "os.environ.get(...)",
        "os.environ[...]",
        "os.getenv(...)",
        "os.getenv(...)",
    ]
    lines = sorted(line for _, line, _ in hits)
    assert lines == [4, 17, 17, 24]


def test_ast_gate_uses_enclosing_scope_for_callable_definition_expressions(
    tmp_path, monkeypatch
) -> None:
    """Defaults, decorators, and annotations run before parameter shadowing."""
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "callable_scopes"
    case_dir.mkdir()
    (case_dir / "callable_scopes.py").write_text(
        "import os\n"
        "\n"
        'def sync_default(os=os.getenv("SYNC")):\n'
        "    return os\n"
        "\n"
        'async def async_default(os=os.environ["ASYNC"]):\n'
        "    return os\n"
        "\n"
        '@decorate(os.getenv("DECORATOR"))\n'
        "def decorated(os):\n"
        "    return os\n"
        "\n"
        'def annotated(value: os.getenv("ANNOTATION"), os=os.environ["DEFAULT"]):\n'
        "    return value, os\n"
        "\n"
        'shadowed = lambda os: (os.getenv("BODY"), os.environ["BODY_SUBSCRIPT"])\n'
        'defaulted = lambda os=os.environ.get("LAMBDA_DEFAULT"): os.getenv("BODY_TOO")\n'
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    assert sorted((line, snippet) for _, line, snippet in hits) == [
        (3, "os.getenv(...)"),
        (6, "os.environ[...]"),
        (9, "os.getenv(...)"),
        (13, "os.environ[...]"),
        (13, "os.getenv(...)"),
        (17, "os.environ.get(...)"),
    ]


def test_ast_gate_detects_environment_membership_checks(tmp_path, monkeypatch) -> None:
    """Membership checks read process configuration, including through aliases."""
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "membership"
    case_dir.mkdir()
    (case_dir / "membership.py").write_text(
        "import os as host_os\n"
        "from os import environ as env\n"
        "OTHER = {}\n"
        'A = "K1" in host_os.environ\n'
        'B = "K2" not in env\n'
        'C = "K3" in host_os.environ in OTHER\n'
        "D = env in OTHER\n"
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    assert sorted((line, snippet) for _, line, snippet in hits) == [
        (4, "membership in os.environ"),
        (5, "membership in os.environ"),
        (6, "membership in os.environ"),
    ]


def test_ast_gate_isolates_class_body_aliases_from_methods(tmp_path, monkeypatch) -> None:
    """Class namespaces neither leak into methods nor hide outer closures."""
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "class_scopes"
    case_dir.mkdir()
    (case_dir / "class_scopes.py").write_text(
        "import os\n"
        "\n"
        "class Rebound:\n"
        "    os = object()\n"
        '    unrelated = os.getenv("NOT_ENV")\n'
        "\n"
        "class LocalAlias:\n"
        "    import os as host_os\n"
        '    class_read = host_os.getenv("CLASS")\n'
        "    def method(self):\n"
        '        return host_os.getenv("NOT_VISIBLE")\n'
        "\n"
        "def outer():\n"
        "    import os as closure_os\n"
        "    class Nested:\n"
        '        class_read = closure_os.getenv("CLASS_CLOSURE")\n'
        "        def method(self):\n"
        '            return closure_os.environ["METHOD_CLOSURE"]\n'
        "    return Nested\n"
        "\n"
        'module_read = os.getenv("MODULE")\n'
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    assert sorted((line, snippet) for _, line, snippet in hits) == [
        (9, "os.getenv(...)"),
        (16, "os.getenv(...)"),
        (18, "os.environ[...]"),
        (21, "os.getenv(...)"),
    ]


def test_ast_gate_propagates_environment_alias_assignments(tmp_path, monkeypatch) -> None:
    """Direct and transitive accessor assignments remain visible to the gate."""
    import sys

    mod = sys.modules[__name__]
    case_dir = tmp_path / "assigned_aliases"
    case_dir.mkdir()
    (case_dir / "assigned_aliases.py").write_text(
        "import os\n"
        "read_env = os.getenv\n"
        "env = os.environ\n"
        "host_os = os\n"
        'A = read_env("A")\n'
        'B = env.get("B")\n'
        'C = host_os.environ["C"]\n'
        "\n"
        "def nested():\n"
        "    local_getenv = os.getenv\n"
        "    local_env = os.environ\n"
        "    transitive = local_getenv\n"
        '    return local_getenv("D"), local_env.get("E"), transitive("F")\n'
        "\n"
        "def unrelated(os):\n"
        "    local_getenv = os.getenv\n"
        '    return local_getenv("NOT_ENV")\n'
    )

    monkeypatch.setattr(mod, "SCAN_DIRS", (case_dir,))
    monkeypatch.setattr(mod, "REPO_ROOT", case_dir)
    hits = mod._scan_for_direct_env_access()

    assert sorted((line, snippet) for _, line, snippet in hits) == [
        (5, "os.getenv(...)"),
        (6, "os.environ.get(...)"),
        (7, "os.environ[...]"),
        (13, "os.environ.get(...)"),
        (13, "os.getenv(...)"),
        (13, "os.getenv(...)"),
    ]
