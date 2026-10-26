"""Caller/context regressions; authored UNRUN in the R2 PRECHANGE source packet.

All registration/bootstrap fixtures are synthetic. Mock boundaries do not grant
host authority or qualify native cgroup/reap semantics. Native accepted evidence
belongs to the earlier exact source; affected final gates remain separate.
"""

from dataclasses import FrozenInstanceError, replace
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import run_managed_backend_pytest as caller
from tests import backend_pytest_supervisor as s


@pytest.fixture
def registration(tmp_path, monkeypatch):
    home = tmp_path / "fictional-home"
    base = home / ".local/share/opencode-work"
    repo = base / "worktrees/model-task"
    resources = base / "resources/model-task"
    evidence = base / "evidence/model-task"
    manifest = home / ".local/state/opencode-work/owned-manifests/model-task.json"
    for directory in [repo, resources, evidence, manifest.parent, repo / ".venv/bin"]:
        directory.mkdir(parents=True, exist_ok=True)
    (repo / ".venv/pyvenv.cfg").write_text("home = /usr/bin\n", encoding="ascii")
    (repo / ".venv/bin/python").symlink_to("/usr/bin/python3")
    context = s.InvocationContext(
        "model-task",
        "ses_Model",
        "a" * 32,
        str(repo),
        str(resources),
        str(evidence),
        str(manifest),
        str(repo / ".venv/bin/python"),
    )

    def identity(path):
        st = path.stat()
        return {"device": st.st_dev, "inode": st.st_ino, "owner_uid": st.st_uid}

    record = {
        "format": "positive-ownership-v2",
        "task": context.task,
        "owner_session": context.owner,
        "nonce": context.nonce,
        "state": "active",
        "completion": None,
        "retirement": None,
        "released_at": None,
        "source": {"path": str(repo), **identity(repo)},
        "resource_identity": identity(resources),
        "evidence_identity": identity(evidence),
        "scopes": {},
    }
    manifest.write_text(json.dumps(record), encoding="utf-8")
    manifest.chmod(0o600)
    monkeypatch.setattr(s, "__file__", str(repo / "tests/backend_pytest_supervisor.py"))
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", ".venv")
    monkeypatch.setattr(s.sys, "executable", context.interpreter)
    monkeypatch.setattr(s.sys, "prefix", str(repo / ".venv"))
    monkeypatch.setattr(s.sys, "base_prefix", "/usr")
    return context, record


def forbid_effects(monkeypatch):
    effects = []

    def forbidden(*args, **kwargs):
        effects.append((args, kwargs))
        raise AssertionError("unexpected effect")

    for name in ["pair", "create_domain", "subreaper", "fork_parked"]:
        monkeypatch.setattr(s, name, forbidden)
    monkeypatch.setattr(s.subprocess, "Popen", forbidden)
    monkeypatch.setattr(s.os, "fork", forbidden)
    return effects


def test_context_is_immutable_canonical_bounded_and_import_only(registration):
    context, _ = registration
    assert s.InvocationContext.parse(context.canonical()) == context
    with pytest.raises(FrozenInstanceError):
        context.owner = "ses_Other"
    assert s.registered_context(context, executing=True)["source"]["path"] == context.source
    # A venv interpreter symlink to system Python is explicitly legitimate.
    assert Path(context.interpreter).is_symlink()
    assert s.local_interpreter(context.source, ".venv") == context.interpreter


@pytest.mark.parametrize("missing", ["spec", "loader"])
def test_caller_missing_local_import_spec_or_loader_refuses(monkeypatch, missing):
    value = None if missing == "spec" else SimpleNamespace(loader=None)
    monkeypatch.setattr(caller.importlib.util, "spec_from_file_location", lambda *args: value)
    with pytest.raises(ImportError, match="local supervisor import spec/loader unavailable"):
        caller.load_supervisor(Path("/fictional/source"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("task", "other-task"),
        ("owner", "ses_Other"),
        ("nonce", "b" * 32),
        ("source", "/fictional/other-source"),
        ("resources", "/fictional/other-R"),
        ("evidence", "/fictional/other-E"),
        ("manifest", "/fictional/other-manifest.json"),
        ("interpreter", "/fictional/other-env/bin/python"),
    ],
)
def test_context_mismatches_precede_manager_effects(registration, monkeypatch, field, value):
    context, _ = registration
    effects = forbid_effects(monkeypatch)
    with pytest.raises(s.SupervisionFailure):
        s.manager(["-q"], replace(context, **{field: value}))
    assert effects == []


@pytest.mark.parametrize("field", ["source", "resource_identity", "evidence_identity"])
def test_root_inode_mismatch_precedes_effects(registration, monkeypatch, field):
    context, record = registration
    record[field]["inode"] += 1
    Path(context.manifest).write_text(json.dumps(record), encoding="utf-8")
    effects = forbid_effects(monkeypatch)
    with pytest.raises(s.SupervisionFailure, match="identity changed"):
        s.manager(["-q"], context)
    assert effects == []


def test_manifest_source_equality_not_basename_or_uid(registration, monkeypatch):
    context, record = registration
    record["source"]["path"] = "/other/same-basename/model-task"
    Path(context.manifest).write_text(json.dumps(record), encoding="utf-8")
    effects = forbid_effects(monkeypatch)
    with pytest.raises(s.SupervisionFailure, match="registered source/context"):
        s.manager(["-q"], context)
    assert effects == []


@pytest.mark.parametrize("kind", ["selection", "executing", "prefix", "base_prefix"])
def test_local_interpreter_mismatch_precedes_effects(registration, monkeypatch, kind):
    context, _ = registration
    if kind == "selection":
        monkeypatch.delenv("UV_PROJECT_ENVIRONMENT")
    elif kind == "executing":
        monkeypatch.setattr(s.sys, "executable", "/usr/bin/python3")
    elif kind == "prefix":
        monkeypatch.setattr(s.sys, "prefix", "/borrowed")
    else:
        monkeypatch.setattr(s.sys, "base_prefix", s.sys.prefix)
    effects = forbid_effects(monkeypatch)
    with pytest.raises(s.SupervisionFailure):
        s.manager(["-q"], context)
    assert effects == []


def test_borrowed_and_symlinked_environment_roots_rejected(registration, tmp_path):
    context, _ = registration
    foreign = tmp_path / "borrowed-env"
    foreign.mkdir()
    alias = Path(context.source) / "alias-env"
    alias.symlink_to(foreign, target_is_directory=True)
    for value in [str(foreign), str(alias)]:
        with pytest.raises(s.SupervisionFailure, match="borrowed/symlinked"):
            s.local_interpreter(context.source, value)


def test_nonoriginal_ready_pid_never_opens_cgroup_or_launches(registration, monkeypatch):
    context, record = registration
    unit = "opencode-work-" + context.nonce + "-" + "b" * 32 + ".scope"
    group = f"/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/" + unit
    ready = Path(context.resources) / "scopes" / (unit + ".json")
    ready.parent.mkdir()
    ready.write_text(json.dumps({"unit": unit, "cgroup": group, "pid": os.getpid() + 1}))
    ready.chmod(0o600)
    record["scopes"][unit] = {
        "state": "launching",
        "ready": str(ready),
        "cgroup": None,
        "exit_code": None,
    }
    Path(context.manifest).write_text(json.dumps(record))
    monkeypatch.setattr(s, "self_group", lambda: group)
    original_open = s.os.open

    def opened(path, *args, **kwargs):
        assert str(path) != "/sys", "must reject before cgroup root acquisition"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(s.os, "open", opened)
    effects = forbid_effects(monkeypatch)
    with pytest.raises(s.SupervisionFailure, match="original bootstrap exec"):
        s.manager(["-q"], context)
    assert effects == []


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--manager"],
        ["--manager", "--context", "{}"],
        ["--manager", "--context", "{}", "--", "-q"],
    ],
)
def test_absent_or_invalid_context_no_default_manager(args, monkeypatch):
    effects = forbid_effects(monkeypatch)
    with pytest.raises(s.SupervisionFailure):
        s.entry(args)
    assert effects == []


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "x" * (s.MAX_CONTEXT + 1),
        "{}",
        "[]",
        "null",
        '{"task":"x","task":"y"}',
        "{",
        '{"task":NaN}',
    ],
)
def test_malformed_bounded_context(raw):
    with pytest.raises(s.SupervisionFailure):
        s.InvocationContext.parse(raw)


def test_canonical_json_has_no_whitespace_or_extra_fields(registration):
    context, _ = registration
    for raw in [
        " " + context.canonical(),
        json.dumps(context.wire()),
        json.dumps({**context.wire(), "extra": "field"}),
    ]:
        with pytest.raises(s.SupervisionFailure):
            s.InvocationContext.parse(raw)


def test_same_context_and_digest_are_bound_and_cli_has_one_syntax(registration, monkeypatch):
    context, _ = registration
    assert s.peer_context(s.context_frame(context), expected=context) == context
    foreign = replace(context, owner="ses_Other")
    with pytest.raises(s.SupervisionFailure, match="conflicting private"):
        s.peer_context(s.context_frame(foreign), expected=context)
    called = []
    monkeypatch.setattr(s, "manager", lambda args, value: called.append((args, value)) or 43)
    assert s.entry(["--manager", "--context", context.canonical(), "--", "-q"]) == 43
    assert called == [(["-q"], context)]


class Channel:
    def __init__(self, pid):
        self.pid = pid

    def getsockopt(self, *args):
        return s.struct.pack("3i", self.pid, os.getuid(), os.getgid())

    def set_inheritable(self, value):
        pass

    def setblocking(self, value):
        pass


@pytest.mark.parametrize("change", ["digest", "missing", "roots", "nonce", "shape"])
def test_worker_context_failure_before_domain_or_subreaper(registration, monkeypatch, change):
    context, _ = registration
    parent = os.getppid()
    nonce = "d" * 32
    group = "/fictional-own.scope"
    request = s.Request(
        ("/usr/bin/bash", str(Path(context.source) / "scripts/run_backend_pytest.sh")),
        context.source,
        (),
        nonce,
        10,
    )
    data = {
        "request": request.wire(),
        "manager_pid": parent,
        "group": group,
        "path": "/sys/fs/cgroup" + group + "/backend-pytest-" + nonce,
        "fds": [-1] * 4,
        "identities": [[0] * 5] * 4,
        **s.context_frame(context),
    }
    if change == "digest":
        data["context_digest"] = "0" * 64
    elif change == "missing":
        del data["context"]
    elif change == "roots":
        data.update(s.context_frame(replace(context, evidence="/foreign")))
    elif change == "nonce":
        data["context"] = replace(context, nonce="b" * 32).canonical()
    else:
        data["context"] = []
    monkeypatch.setattr(s, "receive", lambda *args: {"kind": "setup", "data": data})
    monkeypatch.setattr(s, "self_group", lambda: group)
    monkeypatch.setattr(s.time, "monotonic", lambda: 1)
    effects = forbid_effects(monkeypatch)
    monkeypatch.setattr(s.Domain, "check", lambda *args: effects.append("domain"))
    with pytest.raises(s.SupervisionFailure):
        s.worker(Channel(parent), nonce, 10)
    assert effects == []


@pytest.mark.parametrize("change", ["digest", "missing", "nonce", "roots", "peer", "shape"])
def test_client_context_failure_precedes_run_send(registration, monkeypatch, change):
    context, _ = registration
    peer = os.getppid()
    data = {
        "manager_pid": peer,
        "client_pid": os.getpid(),
        "task_nonce": context.nonce,
        "scope": "/fictional-own.scope",
        **s.context_frame(context),
    }
    if change == "digest":
        data["context_digest"] = "0" * 64
    elif change == "missing":
        del data["context"]
    elif change == "nonce":
        data["task_nonce"] = "b" * 32
    elif change == "roots":
        data.update(s.context_frame(replace(context, resources="/foreign")))
    elif change == "peer":
        data["manager_pid"] += 1
    else:
        data["context"] = []
    monkeypatch.setattr(s, "_client", None)
    monkeypatch.setenv(s.CHANNEL, "123:" + "c" * 32 + ":" + str(peer))
    monkeypatch.setattr(s.socket, "socket", lambda **kwargs: Channel(peer))
    monkeypatch.setattr(s, "self_group", lambda: "/fictional-own.scope")
    monkeypatch.setattr(s, "receive", lambda *args: {"kind": "hello", "data": data})
    sent = []
    monkeypatch.setattr(s, "send", lambda *args: sent.append(args))
    with pytest.raises(s.SupervisionFailure):
        s.run_runner(["unused"], cwd=context.source, env={}, bound_seconds=2)
    assert sent == [] and s._client is None


def test_manager_rejects_conflicting_worker_digest_before_launch(registration, monkeypatch):
    context, _ = registration
    channel = SimpleNamespace(fileno=lambda: -200, close=lambda: None)
    domain = SimpleNamespace(path="/fictional-leaf", fds=(-1, -2, -3, -4), identities=[])
    monkeypatch.setattr(s, "create_domain", lambda *args: domain)
    monkeypatch.setattr(s, "pair", lambda: (channel, channel))
    monkeypatch.setattr(s.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=9001))
    monkeypatch.setattr(s.os, "pidfd_open", lambda *args: -201)
    monkeypatch.setattr(
        s,
        "receive",
        lambda *args: {
            "kind": "ready",
            "data": {"pid": 9001, "scope": "/fictional-own.scope", "context_digest": "0" * 64},
        },
    )
    sent = []
    monkeypatch.setattr(s, "send", lambda *args: sent.append(args))
    request = s.Request((), context.source, (), "d" * 32, 10)
    retained = []
    with pytest.raises(s.SupervisionFailure, match="startup/identity"):
        s.manager_one(None, -202, "/fictional-own.scope", request, retained, context)
    assert [args[3] for args in sent] == ["setup"]  # no launch, original handles retained
    assert len(retained) == 1


def test_caller_separate_lifecycle_original_exec_full_selection(registration, monkeypatch):
    context, _ = registration
    monkeypatch.setattr(caller, "load_supervisor", lambda repo: s)
    _, _, lifecycle = s.layout(context.source)
    lifecycle.parent.mkdir(parents=True)
    lifecycle.write_text("fixture only; never executed\n")
    lifecycle.chmod(0o700)
    value, command = caller.invocation(Path(context.source), ".venv", ["-q"])
    assert value == context
    assert command == [
        str(lifecycle),
        "run",
        context.task,
        "--",
        context.interpreter,
        "-I",
        "-B",
        str(Path(context.source) / "tests/backend_pytest_supervisor.py"),
        "--manager",
        "--context",
        context.canonical(),
        "--",
        "-q",
    ]
    assert "uv" not in command and "/usr/bin/bash" not in command
    # No hidden selector/filter is added to default or explicit selections.
    assert caller.invocation(Path(context.source), ".venv", [])[1][-1] == "-q"
    assert caller.invocation(Path(context.source), ".venv", ["-q", "tests", "-x"])[1][-3:] == [
        "-q",
        "tests",
        "-x",
    ]


@pytest.mark.parametrize("status", [0, 1, 2, 7, 43, -15])
def test_caller_exact_status_no_success_conversion(registration, monkeypatch, status):
    context, _ = registration
    monkeypatch.setattr(
        caller, "__file__", str(Path(context.source) / "scripts/run_managed_backend_pytest.py")
    )
    command = ["fictional lifecycle argv"]
    monkeypatch.setattr(caller, "invocation", lambda *args: (context, command))
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=status)

    monkeypatch.setattr(caller.subprocess, "run", run)
    assert caller.main(["-q"]) == (status if status >= 0 else 128 - status)
    assert len(calls) == 1 and calls[0][0] == command
    assert calls[0][1]["cwd"] == context.source and calls[0][1]["check"] is False
    assert calls[0][1]["env"]["UV_PROJECT_ENVIRONMENT"] == str(
        Path(context.interpreter).parent.parent
    )


def test_caller_missing_registration_refuses_without_launch(registration, monkeypatch):
    context, _ = registration
    Path(context.manifest).unlink()  # exact disposable synthetic fixture only
    monkeypatch.setattr(
        caller, "__file__", str(Path(context.source) / "scripts/run_managed_backend_pytest.py")
    )
    monkeypatch.setattr(caller, "load_supervisor", lambda repo: s)
    called = []
    monkeypatch.setattr(caller.subprocess, "run", lambda *args, **kwargs: called.append(args))
    assert caller.main(["-q"]) == 2 and called == []


def test_caller_rejects_real_but_type_incompatible_override_before_lifecycle(
    registration, monkeypatch
):
    context, _ = registration
    repo = Path(context.source)
    (repo / "custom-env/bin").mkdir(parents=True)
    (repo / "custom-env/pyvenv.cfg").write_text("home = /usr/bin\n")
    (repo / "custom-env/bin/python").symlink_to("/usr/bin/python3")
    monkeypatch.setattr(caller, "__file__", str(repo / "scripts/run_managed_backend_pytest.py"))
    monkeypatch.setattr(caller, "load_supervisor", lambda root: s)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "custom-env")
    called = []
    monkeypatch.setattr(caller.subprocess, "run", lambda *args, **kwargs: called.append(args))
    # Valid local venv, but uv's override cannot redirect configured type analysis.
    assert s.local_interpreter(repo, "custom-env") == str(repo / "custom-env/bin/python")
    with pytest.raises(s.SupervisionFailure, match="both basedpyright .venv selectors"):
        caller.invocation(repo, "custom-env", ["-q"])
    assert caller.main(["-q"]) == 2 and called == []


def test_local_ci_only_pytest_row_changed_and_aggregation_retained():
    source = (Path(__file__).resolve().parents[1] / "scripts/local_ci.sh").read_text()
    rows = source.split("GATE_TABLE <<'EOF'", 1)[1].split("\nEOF", 1)[0].splitlines()[1:]
    assert next(row for row in rows if row.startswith("backend|pytest|")) == (
        "backend|pytest|blocking|python -I -S -B scripts/run_managed_backend_pytest.py -q"
    )
    expected_other_backend = [
        "backend|ruff-check|blocking|uv run ruff check .",
        "backend|ruff-format|blocking|uv run ruff format --check .",
        "backend|basedpyright|blocking|uv run basedpyright --level error",
        "backend|bandit-high|blocking|uv run bandit -r orchestrator providers scripts tests -lll",
        "backend|bandit|inventory|uv run bandit -r orchestrator providers scripts tests",
        "backend|pip-audit|blocking|uv run pip-audit",
    ]
    assert [
        row for row in rows if row.startswith("backend|") and "|pytest|" not in row
    ] == expected_other_backend
    assert 'eval "$cmd"\n    )\n    local rc=$?' in source
    assert 'FAILED_BLOCKING+=("$family/$gate (exit=$rc)")' in source
    assert "if [[ ${#FAILED_BLOCKING[@]} -gt 0 ]]" in source
