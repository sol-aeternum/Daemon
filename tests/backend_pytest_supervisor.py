"""Test-local trusted-same-UID fixture integration; qualification is tracked separately.

Local entry: scripts/run_managed_backend_pytest.py [pytest arguments], selecting
an existing registration and --manager --context CANONICAL_JSON -- pytestargs.
This test-local
adapter must be the original manager bootstrap exec, then starts one direct
pytest interpreter and serves its serialized fixture requests over a private FD.
Each request gets a fresh leaf and a separate sole-subreaper process outside it.

Scope: programme-coordinated, inspected NON-MIGRATING synthetic workloads and
trusted same-UID peers. Deliberate/buggy external migration can invalidate this
ownership assumption. No kernel lifetime exclusion, hostile-peer containment,
escaped-process absence guarantee, root broker or UID/nonce-as-capability claim.
Unexpected evidence fails/retains, never scans or signals outside owned handles.
Prechange/final review and full managed-resource/runtime gates remain required.
"""

from __future__ import annotations

import base64
import contextlib
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import TypedDict, cast

CHANNEL = "DAEMON_TEST_RUNNER_CHANNEL"
MAX_CONTEXT = 8192
MAX_PACKET = 65536
MAX_STREAM = 4 * 1024 * 1024
RESCUE_SECONDS = 10.0
HEX = re.compile(r"[0-9a-f]{32}\Z")
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_client = None
_lock = threading.Lock()


class SupervisionFailure(AssertionError):
    pass


def remaining(deadline):
    now = time.monotonic()
    if not math.isfinite(deadline) or not math.isfinite(now) or now >= deadline:
        raise SupervisionFailure("original phase deadline exhausted")
    return deadline - now


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SupervisionFailure("duplicate IPC/record field")
        result[key] = value
    return result


@dataclass(frozen=True)
class InvocationContext:
    task: str
    owner: str
    nonce: str
    source: str
    resources: str
    evidence: str
    manifest: str
    interpreter: str

    def wire(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    def canonical(self):
        return json.dumps(self.wire(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def digest(self):
        return hashlib.sha256(self.canonical().encode("ascii")).hexdigest()

    @classmethod
    def parse(cls, raw):
        # Shape validation only: imports/models never read live registration.
        if type(raw) is not str or not 0 < len(raw) <= MAX_CONTEXT:
            raise SupervisionFailure("missing/oversize invocation context")
        try:
            data = json.loads(raw, object_pairs_hook=unique)
        except (ValueError, RecursionError) as error:
            raise SupervisionFailure("malformed invocation context") from error
        if (
            type(data) is not dict
            or set(data) != set(cls.__dataclass_fields__)
            or any(type(v) is not str or not v or len(v) > 4096 or "\0" in v for v in data.values())
        ):
            raise SupervisionFailure("malformed invocation context fields")
        value = cls(**data)
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value.task)
            or not re.fullmatch(r"ses_[A-Za-z0-9]+", value.owner)
            or not HEX.fullmatch(value.nonce)
        ):
            raise SupervisionFailure("malformed invocation context identity")
        for name in ["source", "resources", "evidence", "manifest", "interpreter"]:
            path = Path(data[name])
            if not path.is_absolute() or str(path) != data[name] or ".." in path.parts:
                raise SupervisionFailure("noncanonical invocation context path")
        if value.canonical() != raw or len(raw.encode("ascii")) > MAX_CONTEXT:
            raise SupervisionFailure("noncanonical invocation context JSON")
        return value


def layout(source):
    """Only the installed fixed local layout, not HOME/XDG or a registration scan."""
    source = Path(source)
    base = source.parent.parent
    home = base.parent.parent.parent
    if (
        base != home / ".local/share/opencode-work"
        or source.parent != base / "worktrees"
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", source.name)
    ):
        raise SupervisionFailure("checkout is not in the supported registered local layout")
    return base, home / ".local/state/opencode-work", home / ".local/bin/opencode-work"


def local_interpreter(source, selection):
    if type(selection) is not str or not selection:
        raise SupervisionFailure(
            "UV_PROJECT_ENVIRONMENT must select an existing checkout-local environment"
        )
    root = Path(os.path.abspath(Path(source) / selection))
    if (
        root == Path(source)
        or not root.is_relative_to(source)
        or root.resolve(strict=True) != root
        or not root.is_dir()
    ):
        raise SupervisionFailure("borrowed/symlinked environment root")
    # The bin directory must be local; a normal venv python -> system Python
    # symlink is expected and is deliberately NOT resolved for path equality.
    bindir = root / "bin"
    python = bindir / "python"
    config = root / "pyvenv.cfg"
    if (
        bindir.resolve(strict=True) != bindir
        or not bindir.is_dir()
        or config.is_symlink()
        or not config.is_file()
        or not python.is_file()
        or not os.access(python, os.X_OK)
    ):
        raise SupervisionFailure("missing real local venv/interpreter")
    return str(python)


def registered_context(context, *, executing=False):
    """Arguments select one existing record, never replace positive ownership."""
    context = InvocationContext.parse(context.canonical())
    repo = Path(__file__).resolve().parents[1]
    if context.source != str(repo):
        raise SupervisionFailure("supervisor repository/context source mismatch")
    base, state, _ = layout(repo)
    if (
        context.task != repo.name
        or context.resources != str(base / "resources" / context.task)
        or context.evidence != str(base / "evidence" / context.task)
        or context.manifest != str(state / "owned-manifests" / (context.task + ".json"))
    ):
        raise SupervisionFailure("context task/registered canonical roots mismatch")
    # Reject a symlink anywhere in the selected registration directory ancestry.
    for path in [
        repo,
        Path(context.resources),
        Path(context.evidence),
        Path(context.manifest).parent,
    ]:
        if path.resolve(strict=True) != path:
            raise SupervisionFailure("symlinked registered root")
    manifest = private_record(context.manifest)
    if (
        manifest["format"] != "positive-ownership-v2"
        or manifest["task"] != context.task
        or manifest["nonce"] != context.nonce
        or manifest["owner_session"] != context.owner
        or manifest["state"] != "active"
        or any(manifest[key] is not None for key in ["completion", "retirement", "released_at"])
    ):
        raise SupervisionFailure("task owner/state/nonce binding changed")
    if manifest["source"]["path"] != context.source:
        raise SupervisionFailure("registered source/context source mismatch")
    for key, path in [
        ("source", repo),
        ("resource_identity", Path(context.resources)),
        ("evidence_identity", Path(context.evidence)),
    ]:
        info = path.lstat()
        expected = manifest[key]
        if (
            path.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or (info.st_dev, info.st_ino, info.st_uid)
            != (expected["device"], expected["inode"], expected["owner_uid"])
        ):
            raise SupervisionFailure("owned W/R/E identity changed")
    selected = local_interpreter(repo, os.environ.get("UV_PROJECT_ENVIRONMENT"))
    if context.interpreter != selected:
        raise SupervisionFailure("context/local interpreter selection mismatch")
    if executing and (
        os.path.abspath(sys.executable) != selected
        or os.path.abspath(sys.prefix) != str(Path(selected).parent.parent)
        or sys.prefix == sys.base_prefix
    ):
        raise SupervisionFailure("actual interpreter is not selected local venv")
    return manifest


def context_frame(context):
    return {"context": context.canonical(), "context_digest": context.digest()}


def peer_context(data, *, expected=None):
    if type(data) is not dict or not {"context", "context_digest"} <= set(data):
        raise SupervisionFailure("missing private invocation context")
    if hosted_tagged(data.get("context")):
        return hosted_peer(data, expected)
    context = InvocationContext.parse(data["context"])
    if (
        type(data["context_digest"]) is not str
        or data["context_digest"] != context.digest()
        or (expected is not None and context != expected)
    ):
        raise SupervisionFailure("conflicting private context/digest")
    if context.source != str(Path(__file__).resolve().parents[1]):
        raise SupervisionFailure("private context/supervisor source mismatch")
    base, state, _ = layout(context.source)
    if (
        context.task != Path(context.source).name
        or context.resources != str(base / "resources" / context.task)
        or context.evidence != str(base / "evidence" / context.task)
        or context.manifest != str(state / "owned-manifests" / (context.task + ".json"))
        or Path(context.interpreter).name != "python"
        or Path(context.interpreter).parent.name != "bin"
        or not Path(context.interpreter).parent.parent.is_relative_to(context.source)
        or str(Path(context.interpreter).parent.parent) == context.source
    ):
        raise SupervisionFailure("private context canonical roots mismatch")
    return context


def decode_packet(raw, flags=0):
    if not raw or len(raw) > MAX_PACKET or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise SupervisionFailure("EOF/truncated/oversize private IPC")
    packet = json.loads(
        raw,
        object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(
            SupervisionFailure("nonfinite IPC number")
        ),
    )
    if (
        type(packet) is not dict
        or set(packet) != {"v", "nonce", "seq", "kind", "data"}
        or type(packet["v"]) is not int
        or packet["v"] != 1
        or type(packet["seq"]) is not int
        or packet["seq"] < 0
        or type(packet["nonce"]) is not str
        or not HEX.fullmatch(packet["nonce"])
        or type(packet["kind"]) is not str
    ):
        raise SupervisionFailure("malformed private IPC envelope")
    return packet


def receive(channel, nonce, seq, deadline):
    while True:
        remaining(deadline)
        readable, _, _ = select.select([channel], [], [], remaining(deadline))
        if not readable:
            raise SupervisionFailure("private IPC deadline")
        try:
            raw, ancillary, flags, _ = channel.recvmsg(MAX_PACKET + 1)
        except BlockingIOError:
            continue
        if ancillary:
            raise SupervisionFailure("unexpected ancillary control")
        packet = decode_packet(raw, flags)
        if packet["seq"] != seq or (nonce is not None and packet["nonce"] != nonce):
            raise SupervisionFailure("foreign/replayed/out-of-sequence private IPC")
        return packet


def send(channel, nonce, seq, kind, data, deadline):
    raw = json.dumps(
        {"v": 1, "nonce": nonce, "seq": seq, "kind": kind, "data": data},
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    if len(raw) > MAX_PACKET:
        raise SupervisionFailure("bounded IPC frame exceeded")
    while True:
        remaining(deadline)
        _, writable, _ = select.select([], [channel], [], remaining(deadline))
        if not writable:
            raise SupervisionFailure("private IPC send deadline")
        try:
            count = channel.send(raw)
        except BlockingIOError:
            continue
        if count != len(raw):
            raise SupervisionFailure("partial sequenced-packet write")
        return


def pair():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    left.setblocking(False)
    right.setblocking(False)
    return left, right


def fd_identity(fd):
    info = os.fstat(fd)
    return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]


def private_record(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > 1048576
        ):
            raise SupervisionFailure("unsafe original manager record")
        raw = os.read(fd, 1048577)
        if len(raw) > 1048576:
            raise SupervisionFailure("oversize original manager record")
        return json.loads(raw, object_pairs_hook=unique)
    finally:
        os.close(fd)


def self_group():
    with open("/proc/self/cgroup", encoding="ascii") as stream:
        raw = stream.read(4097)
    lines = raw.splitlines()
    if len(raw) > 4096 or len(lines) != 1 or not lines[0].startswith("0::/"):
        raise SupervisionFailure("unknown own unified membership")
    return lines[0][3:]


def bootstrap(context):
    """Only the original managed exec calls this; supervisors never fake its PID."""
    manifest = registered_context(context, executing=True)
    group = self_group()
    unit = group.rsplit("/", 1)[-1]
    prefix = f"/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/app.slice/"
    if group != prefix + unit or not re.fullmatch(
        "opencode-work-" + context.nonce + r"-[0-9a-f]{32}\.scope", unit
    ):
        raise SupervisionFailure("not the exact newly managed own scope")
    ready = Path(context.resources) / "scopes" / (unit + ".json")
    scope = manifest["scopes"].get(unit)
    if scope != {"state": "launching", "ready": str(ready), "cgroup": None, "exit_code": None}:
        raise SupervisionFailure("scope is not original pending launch")
    if private_record(ready) != {"unit": unit, "cgroup": group, "pid": os.getpid()}:
        raise SupervisionFailure(
            "adapter must be original bootstrap exec, not a guessed descendant"
        )
    fd = os.open("/sys", DIR_FLAGS)
    try:
        for part in ["fs", "cgroup", *group.split("/")[1:]]:
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        return group, fd
    except BaseException:
        os.close(fd)
        raise


@dataclass(frozen=True)
class Request:
    argv: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...]
    nonce: str
    deadline: float

    def wire(self):
        return {
            "argv": list(self.argv),
            "cwd": self.cwd,
            "env": dict(self.environment),
            "deadline": self.deadline,
        }


def request_from(data, nonce):
    if type(data) is not dict or set(data) != {"argv", "cwd", "env", "deadline"}:
        raise SupervisionFailure("bad runner request")
    argv, env = data["argv"], data["env"]
    runner = str(Path(__file__).resolve().parents[1] / "scripts/run_backend_pytest.sh")
    if (
        type(argv) is not list
        or len(argv) < 2
        or argv[:2] != ["/usr/bin/bash", runner]
        or any(type(arg) is not str or "\0" in arg for arg in argv)
        or type(data["cwd"]) is not str
        or not Path(data["cwd"]).is_absolute()
        or type(env) is not dict
        or any(
            type(k) is not str or type(v) is not str or "\0" in k or "=" in k or "\0" in v
            for k, v in env.items()
        )
    ):
        raise SupervisionFailure("request is not exact fixture runner/argv/cwd/environment")
    remaining(data["deadline"])
    return Request(tuple(argv), data["cwd"], tuple(sorted(env.items())), nonce, data["deadline"])


@dataclass
class Domain:
    path: str
    fds: tuple[int, int, int, int]  # leaf directory, procs, events, kill
    identities: list

    def check(self):
        if [fd_identity(fd) for fd in self.fds] != self.identities:
            raise SupervisionFailure("original held invocation FD identity lost")

    def populated(self):
        self.check()
        raw = os.pread(self.fds[2], 4097, 0)
        if not raw or len(raw) > 4096 or not raw.endswith(b"\n"):
            raise SupervisionFailure("incomplete held invocation events")
        fields = {}
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) != 2 or parts[0] in fields or not parts[1].isdigit():
                raise SupervisionFailure("ambiguous held invocation events")
            fields[parts[0]] = parts[1]
        if fields.get(b"populated") not in {b"0", b"1"}:
            raise SupervisionFailure("missing populated fact")
        return fields[b"populated"] == b"1"

    def kill_owned(self, deadline):
        remaining(deadline)
        self.check()
        if os.write(self.fds[3], b"1") != 1:
            raise SupervisionFailure("short held invocation kill write")

    def close(self):
        for fd in self.fds:
            os.close(fd)


def create_domain(root_fd, group, nonce, context):
    if not HEX.fullmatch(nonce):
        raise SupervisionFailure("invalid fresh leaf nonce")
    name = "backend-pytest-" + nonce
    path = "/sys/fs/cgroup" + group + "/" + name
    record = Path(context.evidence) / (name + "-intent.json")
    with record.open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "owner": context.owner,
                "task_nonce": context.nonce,
                "scope": group,
                "nonce": nonce,
                "parent_identity": fd_identity(root_fd),
                "leaf": path,
                "scope_limit": "trusted-same-UID/non-migrating",
            },
            stream,
        )
        stream.flush()
        os.fsync(stream.fileno())
    os.mkdir(name, dir_fd=root_fd)  # collision is failure, NEVER adoption
    held = []
    try:
        held.append(os.open(name, DIR_FLAGS, dir_fd=root_fd))
        for filename, access in [
            ("cgroup.procs", os.O_WRONLY),
            ("cgroup.events", os.O_RDONLY),
            ("cgroup.kill", os.O_WRONLY),
        ]:
            held.append(
                os.open(
                    filename, access | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=held[0]
                )
            )
        return Domain(path, tuple(held), [fd_identity(fd) for fd in held])
    except BaseException:
        for fd in held:
            os.close(fd)
        raise  # intent/empty created leaf retained, no alternate target or cleanup


@dataclass(frozen=True)
class CapturedChild:
    pid: int
    pidfd: int
    stdout: int
    stderr: int
    capture_error: str = ""


@dataclass
class Collector:
    child: CapturedChild
    buffers: dict = field(default_factory=lambda: {"stdout": bytearray(), "stderr": bytearray()})
    eofs: set = field(default_factory=set)
    direct_status: int | None = None
    no_children: bool = False

    def reap(self):
        self.no_children = False
        for _ in range(256):
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                self.no_children = True
                if self.direct_status is None:
                    raise SupervisionFailure("ECHILD without original direct reap")
                return
            if pid == 0:
                return  # NOT live-adopted-child discovery
            if not (os.WIFEXITED(status) or os.WIFSIGNALED(status)):
                raise SupervisionFailure("nonterminal raw wait status")
            if pid == self.child.pid:
                if self.direct_status is not None:
                    raise SupervisionFailure("duplicate original direct reap")
                self.direct_status = os.waitstatus_to_exitcode(status)

    def facts(self, domain_empty):
        return {
            "direct_reaped": self.direct_status is not None,
            "no_children": self.no_children,
            "domain_empty": domain_empty,
            "stdout_eof": "stdout" in self.eofs,
            "stderr_eof": "stderr" in self.eofs,
        }

    def drain(self, control, deadline, *, rescue):
        channels = {self.child.stdout: "stdout", self.child.stderr: "stderr"}
        live = [fd for fd, name in channels.items() if name not in self.eofs]
        readers = live + ([] if rescue else [control])
        readable, _, _ = select.select(readers, [], [], min(0.05, remaining(deadline)))
        if not rescue and control in readable:
            raise SupervisionFailure("manager IPC closed/unexpected during production")
        for fd in live:
            if fd not in readable:
                continue
            try:
                data = os.read(fd, 65536)
            except BlockingIOError:
                continue
            name = channels[fd]
            if not data:
                self.eofs.add(name)
            elif len(self.buffers[name]) + len(data) <= MAX_STREAM:
                self.buffers[name].extend(data)
            elif not rescue:
                raise SupervisionFailure("bounded original stream overflow")
            # Rescue continues draining/discarding overflow, never a normal pass.


def collect(domain, request, collector, control, deadline, *, rescue):
    while True:
        remaining(deadline)
        domain.check()
        collector.reap()
        facts = collector.facts(not domain.populated())
        if all(facts.values()):
            remaining(deadline)
            return facts
        collector.drain(control, deadline, rescue=rescue)


def subreaper():
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)  # clear ignore/SA_NOCLDWAIT
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, supervisor ONLY
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    value = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(value), 0, 0, 0) != 0 or value.value != 1:
        raise SupervisionFailure("supervisor subreaper not established")


def fork_parked(domain, request, control):
    """Retain original direct-child identity before granting self-placement/exec."""
    out_r, out_w = os.pipe2(os.O_CLOEXEC)
    err_r, err_w = os.pipe2(os.O_CLOEXEC)
    parent, child = pair()
    pid = os.fork()
    if pid == 0:
        parent.close()
        control.close()
        os.close(out_r)
        os.close(err_r)
        for fd in [domain.fds[0], domain.fds[2], domain.fds[3]]:
            os.close(fd)
        try:
            message = receive(child, request.nonce, 0, request.deadline)
            if message["kind"] != "place" or message["data"] != {}:
                raise SupervisionFailure("bad startup placement gate")
            if os.write(domain.fds[1], b"0") != 1:
                raise SupervisionFailure("short self-placement write")
            if "/sys/fs/cgroup" + self_group() != domain.path:
                raise SupervisionFailure("child self-placement mismatch")
            os.close(domain.fds[1])
            send(child, request.nonce, 0, "placed", {"path": domain.path}, request.deadline)
            message = receive(child, request.nonce, 1, request.deadline)
            if message["kind"] != "exec" or message["data"] != {}:
                raise SupervisionFailure("bad startup exec release")
            child.close()
            os.dup2(out_w, 1)
            os.dup2(err_w, 2)
            os.close(out_w)
            os.close(err_w)
            os.chdir(request.cwd)
            os.setsid()  # preserve original start_new_session=True; not group targeting
            os.execve(request.argv[0], list(request.argv), dict(request.environment))
        except BaseException:
            os._exit(126)  # parent retains identity/pidfd; EOF/reap cannot be fabricated
    child.close()
    os.close(out_w)
    os.close(err_w)
    os.set_blocking(out_r, False)
    os.set_blocking(err_r, False)
    # Sole parent has not waited: even an early dead child remains unreaped,
    # so this is original-held acquisition, not a late sampled/marker lookup.
    try:
        pidfd = os.pidfd_open(pid, 0)
    except BaseException as error:
        # No exec/placement permission was sent. Child's bounded startup gate
        # exits itself. Preserve its original numeric *wait* identity, never signal it.
        parent.close()
        return CapturedChild(
            pid, -1, out_r, err_r, "pidfd_open(original unreaped child): " + str(error)
        ), None
    return CapturedChild(pid, pidfd, out_r, err_r), parent


def rescue(domain, request, collector, control, placed, deadline):
    remaining(deadline)
    domain.check()
    if collector is None:
        if domain.populated():
            raise SupervisionFailure("unexpected populated leaf before owned capture; NO kill")
        try:
            os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return {"accounted": True, "no_child_started": True}
        return {"accounted": False, "reason": "unexpected child without original capture"}
    collector.reap()
    if not placed and collector.direct_status is None:
        if collector.child.pidfd >= 0:
            remaining(deadline)
            try:
                signal.pidfd_send_signal(collector.child.pidfd, signal.SIGKILL, None, 0)
            except ProcessLookupError:
                pass  # original-held child died in the race; actual reap still required
        # Without pidfd the unopened startup gate was closed: bounded reap/EOF
        # only, NEVER numeric targeting or production release.
    if domain.populated():
        domain.kill_owned(deadline)
    facts = collect(domain, request, collector, control, deadline, rescue=True)
    return {"accounted": all(facts.values()), "facts": facts}


def emit(control, request, collector, final, deadline, seq):
    if collector is not None:
        for name in ["stdout", "stderr"]:
            data = collector.buffers[name]
            for offset in range(0, len(data), 4096):
                send(
                    control,
                    request.nonce,
                    seq,
                    name,
                    base64.b64encode(data[offset : offset + 4096]).decode("ascii"),
                    deadline,
                )
                seq += 1
    send(control, request.nonce, seq, "final", final, deadline)


def worker(control, nonce, deadline):
    # Original private manager-created socket, NOT equality with bootstrap PID.
    setup = receive(control, nonce, 0, deadline)
    if setup["kind"] not in {"setup", "hosted-setup"}:
        raise SupervisionFailure("missing original private setup")
    data = setup["data"]
    if type(data) is not dict or set(data) != {
        "request",
        "manager_pid",
        "group",
        "path",
        "fds",
        "identities",
        "context",
        "context_digest",
    }:
        raise SupervisionFailure("malformed private setup")
    if setup["kind"] == "hosted-setup":
        hosted_worker_binding(data, control)
    else:
        hosted_demand(not hosted_tagged(data["context"]), "local worker context")
    request = request_from(data["request"], nonce)
    peer_pid, peer_uid, _ = struct.unpack(
        "3i", control.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    )
    if (
        request.deadline != deadline
        or data["manager_pid"] != os.getppid()
        or peer_pid != os.getppid()
        or peer_uid != os.getuid()
        or self_group() != data["group"]
    ):
        raise SupervisionFailure("supervisor private-parent/scope binding mismatch")
    context = peer_context(data)
    if data["path"] != "/sys/fs/cgroup" + data["group"] + "/backend-pytest-" + nonce:
        raise SupervisionFailure("private setup leaf mismatch")
    domain = Domain(data["path"], tuple(data["fds"]), data["identities"])
    domain.check()
    if domain.populated():
        raise SupervisionFailure("new invocation leaf unexpectedly populated; NO launch or kill")
    subreaper()
    send(
        control,
        nonce,
        0,
        "ready",
        {"pid": os.getpid(), "scope": data["group"], "context_digest": context.digest()},
        deadline,
    )
    collector = None
    startup = None
    placed = False
    try:
        gate = receive(control, nonce, 1, deadline)
        if gate["kind"] != "launch" or gate["data"] != {}:
            raise SupervisionFailure("bad manager launch gate")
        captured, startup = fork_parked(domain, request, control)
        collector = Collector(captured)
        if startup is None:
            raise SupervisionFailure(captured.capture_error)
        send(startup, nonce, 0, "place", {}, deadline)
        ack = receive(startup, nonce, 0, deadline)
        if ack["kind"] != "placed" or ack["data"] != {"path": domain.path}:
            raise SupervisionFailure("invalid original child placement ACK")
        placed = True
        send(startup, nonce, 1, "exec", {}, deadline)
        startup.close()
        startup = None
        facts = collect(domain, request, collector, control, deadline, rescue=False)
        final = {
            "ok": True,
            "returncode": collector.direct_status,
            "facts": facts,
            "rescue_used": False,
            "end": deadline,
        }
    except BaseException as original:
        end = min(
            time.monotonic() + RESCUE_SECONDS, request.deadline + RESCUE_SECONDS
        )  # ONE original ceiling, including IPC
        if startup is not None:
            startup.close()
            startup = None
        try:
            send(control, nonce, 1, "rescue-start", {"end": end}, end)
        except BaseException:
            pass  # broken result channel must not prevent original-owned rescue
        try:
            accounting = rescue(domain, request, collector, control, placed, end)
        except BaseException as failure:
            accounting = {"accounted": False, "reason": str(failure)[:1000]}
        final = {
            "ok": False,
            "rescue_used": True,
            "error": str(original)[:1000],
            "accounting": accounting,
            "end": end,
        }
    try:
        emit(control, request, collector, final, final["end"], 1 if final["ok"] else 2)
    except BaseException:
        if final["ok"] or final["accounting"]["accounted"]:
            return 1  # terminal workload accounted, but IPC failure can never pass
    # On unresolved accounting keep this sole reaper and original handles alive.
    # The parent marks UNKNOWN/retains; it never kills this supervisor to finish.
    if not final["ok"] and not final["accounting"]["accounted"]:
        while True:
            if collector is not None:
                try:
                    collector.reap()
                except BaseException:
                    pass  # preserve original process/handles on lost wait ownership
            time.sleep(0.1)
    return 0 if final["ok"] else 1


def finish_direct(process, pidfd, deadline):
    while process.poll() is None:  # actual sole-parent wait/reap, no numeric liveness probe
        select.select([pidfd], [], [], min(0.05, remaining(deadline)))
    remaining(deadline)
    return process.returncode


def manager_one(client, root_fd, group, request, retained, context):
    domain = create_domain(root_fd, group, request.nonce, context)
    retained.append(
        {
            "domain": domain,
            "process": None,
            "pidfd": None,
            "control": None,
            "release_verified": False,
        }
    )
    job = retained[-1]
    local, remote = pair()
    job["control"] = local
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(Path(__file__).resolve()),
            "--worker",
            str(remote.fileno()),
            request.nonce,
            repr(request.deadline),
        ],
        stdin=subprocess.DEVNULL,
        close_fds=True,
        pass_fds=(*domain.fds, remote.fileno()),
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        **hosted_worker_stdio(context),
    )
    job["process"] = process
    remote.close()
    pidfd = os.pidfd_open(process.pid, 0)  # original unreaped dedicated child
    job["pidfd"] = pidfd
    send(
        local,
        request.nonce,
        0,
        "hosted-setup" if isinstance(context, HostedContext) else "setup",
        {
            "request": request.wire(),
            "manager_pid": os.getpid(),
            "group": group,
            "path": domain.path,
            "fds": list(domain.fds),
            "identities": domain.identities,
            **context_frame(context),
        },
        request.deadline,
    )
    ready = receive(local, request.nonce, 0, request.deadline)
    if ready["kind"] != "ready" or ready["data"] != {
        "pid": process.pid,
        "scope": group,
        "context_digest": context.digest(),
    }:
        raise SupervisionFailure("dedicated supervisor startup/identity failure")
    send(client, request.nonce, 0, "ready", {}, request.deadline)
    send(local, request.nonce, 1, "launch", {}, request.deadline)
    seq = 1
    result_deadline = request.deadline + RESCUE_SECONDS
    totals = {"stdout": 0, "stderr": 0}
    while True:
        # Worker owns the one rescue budget. This is a ceiling, not a reset.
        packet = receive(local, request.nonce, seq, result_deadline)
        kind, data = packet["kind"], packet["data"]
        if kind == "rescue-start":
            end = data["end"]
            if seq != 1 or end > result_deadline:
                raise SupervisionFailure("repeated/extended rescue budget")
            remaining(end)
            result_deadline = end
            send(client, request.nonce, seq, kind, data, end)
            seq += 1
            continue
        if kind in totals:
            chunk = base64.b64decode(data, validate=True)
            totals[kind] += len(chunk)
            if len(chunk) > 4096 or totals[kind] > MAX_STREAM:
                raise SupervisionFailure("worker stream bound lost")
            send(client, request.nonce, seq, kind, data, result_deadline)
            seq += 1
            continue
        if kind != "final" or type(data) is not dict:
            raise SupervisionFailure("unexpected worker result")
        if data["end"] > result_deadline:
            raise SupervisionFailure("rescue deadline extended")
        record = Path(context.evidence) / ("backend-pytest-" + request.nonce + "-result.json")
        with record.open("x", encoding="utf-8") as stream:
            json.dump(
                {
                    "nonce": request.nonce,
                    "scope": group,
                    "leaf": domain.path,
                    "result": data,
                    "stream_bytes": totals,
                },
                stream,
                allow_nan=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        remaining(result_deadline)
        if data["ok"]:
            if data["rescue_used"] or not complete(data["facts"]):
                raise SupervisionFailure("worker normal closure is incomplete")
            if finish_direct(process, pidfd, request.deadline) != 0:
                raise SupervisionFailure("dedicated supervisor did not exit normally")
            domain.check()
            if domain.populated():
                raise SupervisionFailure("invocation populated after terminal receipt")
            job["release_verified"] = True
            send(client, request.nonce, seq, "final", data, request.deadline)
            domain.close()
            os.close(pidfd)
            local.close()
            retained.remove(job)
        else:
            # Failure is NEVER turned into a normal result, even if rescued.
            accounting = data["accounting"]
            if accounting.get("accounted") is True:
                no_child = accounting == {"accounted": True, "no_child_started": True}
                if not no_child and not complete(accounting.get("facts")):
                    raise SupervisionFailure("worker rescue accounting is incomplete")
                if finish_direct(process, pidfd, data["end"]) != 1:
                    raise SupervisionFailure("dedicated supervisor failure exit mismatch")
                domain.check()
                if domain.populated():
                    raise SupervisionFailure("invocation populated after rescue receipt")
                job["release_verified"] = True
            send(client, request.nonce, seq, "final", data, data["end"])
        return


def manager(pytest_args, context):
    group, root_fd = bootstrap(context)
    local, remote = pair()
    suite_nonce = secrets.token_hex(16)
    env = dict(os.environ)
    env["UV_PROJECT_ENVIRONMENT"] = str(Path(context.interpreter).parent.parent)
    env[CHANNEL] = f"{remote.fileno()}:{suite_nonce}:{os.getpid()}"
    client = subprocess.Popen(
        [context.interpreter, "-m", "tests.backend_pytest_client", *pytest_args],
        env=env,
        close_fds=True,
        pass_fds=(remote.fileno(),),
    )
    remote.close()
    retained = []
    failed = False
    try:
        send(
            local,
            suite_nonce,
            0,
            "hello",
            {
                "manager_pid": os.getpid(),
                "client_pid": client.pid,
                "task_nonce": context.nonce,
                "scope": group,
                **context_frame(context),
            },
            time.monotonic() + 30,
        )
        while client.poll() is None:
            readable, _, _ = select.select([local], [], [], 0.1)
            if not readable:
                continue
            if local.recv(1, socket.MSG_PEEK) == b"":
                break  # actual client EOF; reap its original direct handle below
            packet = receive(local, None, 0, time.monotonic() + 1)
            if failed or packet["kind"] != "run":
                raise SupervisionFailure("fixture requests refused after failure/unexpected IPC")
            request = request_from(packet["data"], packet["nonce"])
            manager_one(local, root_fd, group, request, retained, context)
            failed = bool(retained)
    except BaseException as error:
        failed = True
        print("test-local manager FAILED/retain: " + str(error)[:1000], file=sys.stderr, flush=True)
        # EOF tells any active sole-reaper to rescue its own original leaf only.
        for job in retained:
            if job["control"] is not None:
                job["control"].shutdown(socket.SHUT_WR)
        local.close()  # testclient gets an explicit failure/EOF, never a normal status
    finally:
        if any(job.get("release_verified") is not True for job in retained):
            # Worker exit is not workload accounting. Even an exited/missing
            # worker leaves UNKNOWN ownership pinned, without a new rescue.
            print(
                "UNKNOWN: original handles retained; managed scope remains pinned",
                file=sys.stderr,
                flush=True,
            )
            while True:
                time.sleep(0.1)
        while client.poll() is None:
            time.sleep(0.1)  # direct client only; no signal or guessed PID
        os.close(root_fd)
    return 1 if failed else client.returncode


def complete(facts):
    return (
        type(facts) is dict
        and set(facts)
        == {"direct_reaped", "no_children", "domain_empty", "stdout_eof", "stderr_eof"}
        and all(value is True for value in facts.values())
    )


@dataclass(frozen=True)
class Closure:
    request: Request
    returncode: int
    facts: dict
    rescue_used: bool

    @property
    def complete(self):
        return type(self.returncode) is int and complete(self.facts)


@dataclass(frozen=True)
class Result:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    closure: Closure


@dataclass(frozen=True)
class StartupMarker:
    path: Path
    nonce: str

    @classmethod
    def fresh(cls, root, label):
        nonce = secrets.token_hex(16)
        path = root / f"{label}-{nonce}.started"
        if path.exists():
            raise SupervisionFailure("startup marker collision; no adoption")
        return cls(path, nonce)

    @property
    def payload(self):
        return f"started:{self.path.name}\n"


def client_channel(deadline):
    global _client
    if _client is not None:
        if _client[0] != os.getpid():
            raise SupervisionFailure("private channel inherited by another testclient process")
        return _client[1]
    value = os.environ.get(CHANNEL)
    if value is None:
        raise SupervisionFailure("missing original managed testclient handoff; runner NOT launched")
    fd_text, nonce, pid_text = value.split(":")
    if not HEX.fullmatch(nonce):
        raise SupervisionFailure("bad manager handshake nonce")
    channel = socket.socket(fileno=int(fd_text))
    channel.set_inheritable(False)
    channel.setblocking(False)
    peer_pid, peer_uid, _ = struct.unpack(
        "3i", channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    )
    hello = receive(channel, nonce, 0, deadline)
    if hello["kind"] == "hosted-hello":
        context = hosted_client_hello(channel, hello, nonce, int(pid_text), deadline)
        _client = (os.getpid(), channel, context)
        return channel
    data = hello["data"]
    if type(data) is not dict or set(data) != {
        "manager_pid",
        "client_pid",
        "task_nonce",
        "scope",
        "context",
        "context_digest",
    }:
        raise SupervisionFailure("malformed private hello")
    if (
        peer_pid != int(pid_text)
        or peer_uid != os.getuid()
        or hello["kind"] != "hello"
        or data["manager_pid"] != peer_pid
        or data["client_pid"] != os.getpid()
        or peer_pid != os.getppid()
        or data["scope"] != self_group()
    ):
        raise SupervisionFailure("original private manager/client identity mismatch")
    hosted_demand(not hosted_tagged(data["context"]), "local client context")
    context = peer_context(data)
    if data["task_nonce"] != context.nonce:
        raise SupervisionFailure("hello task/context nonce mismatch")
    _client = (os.getpid(), channel, context)
    return channel


def client_context(deadline):
    client_channel(deadline)
    snapshot = _client
    if snapshot is None:
        raise SupervisionFailure("missing authenticated client context")
    return snapshot[2]


@contextlib.contextmanager
def serialized(deadline):
    if not _lock.acquire(timeout=remaining(deadline)):
        raise SupervisionFailure("original deadline exhausted waiting for serialized fixture")
    try:
        yield
    finally:
        _lock.release()


def run_runner(argv, *, cwd, env, bound_seconds, startup=None):
    if not math.isfinite(bound_seconds) or bound_seconds <= 0:
        raise ValueError("invalid original harness bound")
    request = Request(
        tuple(argv),
        str(cwd),
        tuple(sorted(env.items())),
        startup.nonce if startup else secrets.token_hex(16),
        time.monotonic() + bound_seconds,
    )
    with serialized(request.deadline):  # one launch/control owner, original bound
        channel = client_channel(request.deadline)
        # client_channel already authenticates and caches the real context.
        # Avoid a second handoff lookup on the unchanged local execution path.
        if _client is not None:
            hosted_request_budget(_client[2], request)
        send(channel, request.nonce, 0, "run", request.wire(), request.deadline)
        ready = receive(channel, request.nonce, 0, request.deadline)
        if ready["kind"] != "ready" or ready["data"] != {}:
            raise SupervisionFailure("supervisor never reached original startup gate")
        seq = 1
        result_deadline = request.deadline + RESCUE_SECONDS
        rescued = False
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        while True:
            packet = receive(channel, request.nonce, seq, result_deadline)
            kind, data = packet["kind"], packet["data"]
            if kind == "rescue-start":
                if rescued or seq != 1 or data["end"] > result_deadline:
                    raise SupervisionFailure("repeated/extended client rescue budget")
                result_deadline = data["end"]
                remaining(result_deadline)
                rescued = True
                seq += 1
                continue
            if kind in buffers:
                chunk = base64.b64decode(data, validate=True)
                if len(chunk) > 4096 or len(buffers[kind]) + len(chunk) > MAX_STREAM:
                    raise SupervisionFailure("client stream byte bound exceeded")
                buffers[kind].extend(chunk)
                seq += 1
                continue
            if kind != "final" or type(data) is not dict:
                raise SupervisionFailure("unexpected final private IPC shape")
            if rescued or not data.get("ok") or data.get("rescue_used"):
                raise SupervisionFailure(
                    "runner failed/rescued/UNKNOWN; retain invocation: "
                    + str(data.get("error", "invalid completion"))[:1000]
                )
            remaining(request.deadline)
            proof = Closure(request, data["returncode"], data["facts"], False)
            if not proof.complete:
                raise SupervisionFailure("normal result lacks genuine whole-tree closure")
            return Result(
                request.argv,
                proof.returncode,
                buffers["stdout"].decode(errors="replace"),
                buffers["stderr"].decode(errors="replace"),
                proof,
            )


def assert_startup_and_normal_closure(marker, result, marker_variable):
    proof = result.closure
    if (
        not proof.complete
        or proof.rescue_used
        or proof.request.nonce != marker.nonce
        or dict(proof.request.environment).get(marker_variable) != str(marker.path)
    ):
        raise SupervisionFailure("startup marker lacks exact same-run pre-rescue tree closure")
    if marker.path.read_bytes() != marker.payload.encode("ascii"):
        raise SupervisionFailure("foreign/missing/partial same-run startup marker")


# Hosted-only contract. Local registration and its exact eight fields stay separate.
HOSTED_TAG = "github-systemd-v1"
HOSTED_PHASES = {"positive": 60, "accounted-rescue": 60, "suite": 1200}
HOSTED_ENV = {
    "HOME",
    "PATH",
    "UV_PROJECT_ENVIRONMENT",
    "PYTHONPATH",
    "PYTHONFAULTHANDLER",
    "PYTHONUNBUFFERED",
    "DAEMON_PYTEST_ASYNCIO_DUMP_S",
    "TASKS_TEST_DATABASE_URL",
}
HOSTED_SYSTEM_ENV = {"USER", "LOGNAME", "SHELL", "INVOCATION_ID", "SYSTEMD_EXEC_PID"}


def hosted_demand(condition, reason):
    if not condition:
        raise SupervisionFailure("hosted: " + reason)


def hosted_canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def hosted_tagged(raw):
    if type(raw) is not str:
        return False
    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except ValueError:
        return False
    return type(value) is dict and value.get("tag") == HOSTED_TAG


class HostedWire(TypedDict):
    tag: str
    run: str
    attempt: str
    phase: str
    nonce: str
    unit: str
    source: str
    resources: str
    evidence: str
    interpreter: str
    uid: int
    gid: int
    groups: list[int]
    deadline: float
    invocation: str
    control: str
    payload: str
    control_identity: list[int]
    payload_identity: list[int]


@dataclass(frozen=True)
class HostedContext:
    tag: str
    run: str
    attempt: str
    phase: str
    nonce: str
    unit: str
    source: str
    resources: str
    evidence: str
    interpreter: str
    uid: int
    gid: int
    groups: tuple
    deadline: float
    invocation: str
    control: str
    payload: str
    control_identity: tuple
    payload_identity: tuple

    @property
    def task(self):
        return self.unit

    @property
    def owner(self):
        return HOSTED_TAG

    def wire(self):
        data = {name: getattr(self, name) for name in self.__dataclass_fields__}
        for name in ("groups", "control_identity", "payload_identity"):
            data[name] = list(data[name])
        return data

    def canonical(self):
        return hosted_canonical(self.wire())

    def digest(self):
        return hashlib.sha256(self.canonical().encode("ascii")).hexdigest()

    @classmethod
    def parse(cls, raw):
        hosted_demand(type(raw) is str and 0 < len(raw) <= MAX_CONTEXT, "context size")
        data = json.loads(raw, object_pairs_hook=unique)
        hosted_demand(
            type(data) is dict and set(data) == set(cls.__dataclass_fields__),
            "exact hosted context fields",
        )
        # Structural view only: every field is still runtime-validated below
        # before constructing a trusted context. This cast accepts no input.
        data = cast(HostedWire, data)
        hosted_demand(
            data["tag"] == HOSTED_TAG and data["phase"] in HOSTED_PHASES, "context tag/phase"
        )
        for key in ("run", "attempt"):
            hosted_demand(
                type(data[key]) is str and re.fullmatch(r"[1-9][0-9]{0,19}", data[key]),
                "numeric run/attempt",
            )
        hosted_demand(type(data["nonce"]) is str and HEX.fullmatch(data["nonce"]), "nonce")
        expected = (
            "daemon-pytest-"
            + data["run"]
            + "-"
            + data["attempt"]
            + "-"
            + data["phase"]
            + "-"
            + data["nonce"]
            + ".service"
        )
        hosted_demand(data["unit"] == expected, "fresh unit identity")
        for key in ("source", "resources", "evidence", "interpreter"):
            value = data[key]
            hosted_demand(
                type(value) is str
                and 0 < len(value) < 4096
                and "\0" not in value
                and str(Path(value)) == value
                and Path(value).is_absolute()
                and ".." not in Path(value).parts,
                "canonical hosted path",
            )
        hosted_demand(
            data["resources"] == data["evidence"]
            and Path(data["evidence"]).parent == Path(data["source"]) / ".hosted-pytest"
            and Path(data["evidence"]).name == data["nonce"]
            and Path(data["interpreter"]).name == "python"
            and Path(data["interpreter"]).parent.name == "bin"
            and Path(data["interpreter"]).parent.parent.is_relative_to(data["source"]),
            "hosted roots/interpreter",
        )
        for key in ("uid", "gid"):
            hosted_demand(type(data[key]) is int and data[key] > 0, "nonroot credentials")
        hosted_demand(
            type(data["groups"]) is list
            and all(type(n) is int and n >= 0 for n in data["groups"])
            and data["groups"] == sorted(set(data["groups"])),
            "group set",
        )
        hosted_demand(
            type(data["deadline"]) in (int, float)
            and math.isfinite(data["deadline"])
            and data["deadline"] > 0,
            "deadline",
        )
        hosted_demand(
            type(data["invocation"]) is str and HEX.fullmatch(data["invocation"]), "InvocationID"
        )
        for key in ("control", "payload"):
            value = data[key]
            hosted_demand(
                type(value) is str
                and value.startswith("/")
                and str(Path(value)) == value
                and ".." not in Path(value).parts,
                "exact cgroup path",
            )
        hosted_demand(
            data["control"].endswith("/" + expected + "/manager")
            and data["payload"] == data["control"] + "/payload-" + data["nonce"],
            "assigned control/payload groups",
        )
        for key in ("control_identity", "payload_identity"):
            value = data[key]
            hosted_demand(
                type(value) is list
                and len(value) == 5
                and all(type(n) is int and n >= 0 for n in value)
                and value[2] == data["uid"],
                "directory identity",
            )
        value = cls(
            tag=data["tag"],
            run=data["run"],
            attempt=data["attempt"],
            phase=data["phase"],
            nonce=data["nonce"],
            unit=data["unit"],
            source=data["source"],
            resources=data["resources"],
            evidence=data["evidence"],
            interpreter=data["interpreter"],
            uid=data["uid"],
            gid=data["gid"],
            groups=tuple(data["groups"]),
            deadline=data["deadline"],
            invocation=data["invocation"],
            control=data["control"],
            payload=data["payload"],
            control_identity=tuple(data["control_identity"]),
            payload_identity=tuple(data["payload_identity"]),
        )
        hosted_demand(value.canonical() == raw, "canonical hosted JSON")
        return value


def hosted_peer(data, expected=None):
    context = HostedContext.parse(data["context"])
    hosted_demand(
        data.get("context_digest") == context.digest()
        and (expected is None or expected == context)
        and context.source == str(Path(__file__).resolve().parents[1]),
        "peer context/source digest",
    )
    return context


def hosted_credentials(context):
    hosted_demand(
        os.getresuid() == (context.uid,) * 3
        and os.getresgid() == (context.gid,) * 3
        and sorted(os.getgroups()) == list(context.groups),
        "actual UID/GID/groups",
    )
    status = {}
    with open("/proc/self/status", encoding="ascii") as stream:
        for line in stream:
            key, _, value = line.partition(":")
            if key in {"CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs"}:
                hosted_demand(key not in status, "duplicate status")
                status[key] = value.strip()
    hosted_demand(
        set(status) == {"CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb", "NoNewPrivs"}
        and status["NoNewPrivs"] == "1"
        and all(int(value, 16) == 0 for key, value in status.items() if key != "NoNewPrivs"),
        "actual zero capabilities/NNP",
    )


def hosted_open_group(group, identity=None):
    # Component-by-component nofollow traversal, no prefix ownership inference.
    fd = os.open("/sys", DIR_FLAGS)
    try:
        for part in ("fs", "cgroup", *group.split("/")[1:]):
            hosted_demand(part not in {"", ".", ".."}, "cgroup component")
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        if identity is not None:
            hosted_demand(fd_identity(fd) == list(identity), "held cgroup directory identity")
        return fd
    except BaseException:
        os.close(fd)
        raise


def hosted_place(context, role, parent_pid, channel):
    peer_pid, peer_uid, _ = struct.unpack(
        "3i", channel.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    )
    hosted_demand(
        role in {"client", "worker"}
        and parent_pid == os.getppid()
        and peer_pid == parent_pid
        and peer_uid == context.uid
        and self_group() == context.control,
        "original private parent/control role",
    )
    hosted_credentials(context)
    root = hosted_open_group(context.payload, context.payload_identity)
    try:
        fd = os.open("cgroup.procs", os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=root)
        try:
            hosted_demand(os.write(fd, b"0") == 1, "short self-placement")
        finally:
            os.close(fd)
        hosted_demand(self_group() == context.payload, "exact payload placement")
    finally:
        os.close(root)


def hosted_worker_binding(data, control):
    context = hosted_peer(data)
    hosted_demand(
        data["group"] == context.payload
        and data["path"].startswith("/sys/fs/cgroup" + context.payload + "/backend-pytest-")
        and data["manager_pid"] == os.getppid(),
        "worker assigned payload",
    )
    hosted_place(context, "worker", data["manager_pid"], control)
    return context


class HostedWorkerStdio(TypedDict, total=False):
    stdout: int
    stderr: int


def hosted_worker_stdio(context) -> HostedWorkerStdio:
    return (
        {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if isinstance(context, HostedContext)
        else {}
    )


def hosted_client_hello(channel, hello, nonce, parent_pid, deadline):
    data = hello["data"]
    hosted_demand(
        type(data) is dict
        and set(data) == {"manager_pid", "client_pid", "role", "context", "context_digest"}
        and data["role"] == "client"
        and data["client_pid"] == os.getpid()
        and data["manager_pid"] == parent_pid,
        "exact direct client hello",
    )
    context = hosted_peer(data)
    end = min(deadline, context.deadline)
    remaining(end)
    hosted_place(context, "client", parent_pid, channel)
    send(
        channel,
        nonce,
        0,
        "hosted-placed",
        {
            "pid": os.getpid(),
            "role": "client",
            "group": context.payload,
            "context_digest": context.digest(),
        },
        end,
    )
    gate = receive(channel, nonce, 1, end)
    hosted_demand(
        gate["kind"] == "hosted-exec" and gate["data"] == {"context_digest": context.digest()},
        "client release",
    )
    return context


def hosted_request_budget(context, request):
    if isinstance(context, HostedContext):
        hosted_demand(
            request.deadline + RESCUE_SECONDS < context.deadline,
            "original fixture and rescue ceilings exceed phase budget",
        )


def hosted_frame(fd, context, seq, kind, data, deadline):
    raw = (
        hosted_canonical(
            {
                "tag": HOSTED_TAG,
                "nonce": context.nonce,
                "phase": context.phase,
                "context_digest": context.digest(),
                "seq": seq,
                "kind": kind,
                "data": data,
            }
        )
        + "\n"
    ).encode("ascii")
    hosted_demand(len(raw) <= MAX_PACKET, "outer frame size")
    view = memoryview(raw)
    while view:
        _, writable, _ = select.select([], [fd], [], remaining(deadline))
        hosted_demand(bool(writable), "outer write timeout")
        try:
            count = os.write(fd, view)
        except BlockingIOError:
            continue
        hosted_demand(count > 0, "outer write lost")
        view = view[count:]


def hosted_read_frame(fd, context, seq, kind, deadline):
    # ACK transport has exactly one LF-delimited bounded frame per state. A queued
    # duplicate in the same read is rejected instead of being silently discarded.
    raw = bytearray()
    while not raw.endswith(b"\n"):
        ready, _, _ = select.select([fd], [], [], remaining(deadline))
        hosted_demand(bool(ready), "ACK timeout")
        part = os.read(fd, MAX_PACKET + 1 - len(raw))
        hosted_demand(bool(part), "ACK EOF")
        raw.extend(part)
        hosted_demand(len(raw) <= MAX_PACKET and raw.count(b"\n") <= 1, "ACK framing")
    frame = json.loads(raw, object_pairs_hook=unique)
    hosted_demand(
        type(frame) is dict
        and set(frame) == {"tag", "nonce", "phase", "context_digest", "seq", "kind", "data"}
        and frame["tag"] == HOSTED_TAG
        and frame["nonce"] == context.nonce
        and frame["phase"] == context.phase
        and frame["context_digest"] == context.digest()
        and type(frame["seq"]) is int
        and frame["seq"] == seq
        and frame["kind"] == kind,
        "ACK identity/state",
    )
    return frame["data"]


def hosted_only_manager(root, context, manager_pid, nonces=()):
    # All enumerated directories remain held until the observation finishes.
    # There is no path adoption, signaling, removal or vanished=>empty rule.
    pending = [(root, context.control.rsplit("/", 1)[0])]
    opened = []
    members = {}
    try:
        while pending:
            fd, path = pending.pop()
            hosted_demand(len(members) < 4096, "unit hierarchy bound")
            procs = os.open("cgroup.procs", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            try:
                raw = os.read(procs, 65537)
                hosted_demand(len(raw) <= 65536, "membership size")
                values = raw.splitlines()
                hosted_demand(all(v.isdigit() and int(v) > 0 for v in values), "membership parse")
                members[path] = [int(v) for v in values]
            finally:
                os.close(procs)
            for name in os.listdir(fd):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode):
                    continue
                child_path = path + "/" + name
                hosted_demand(
                    child_path in {context.control, context.payload}
                    or (
                        path == context.payload
                        and name in {"backend-pytest-" + nonce for nonce in nonces}
                    ),
                    "unexpected owned-unit subgroup",
                )
                child = os.open(name, DIR_FLAGS, dir_fd=fd)
                opened.append(child)
                pending.append((child, child_path))
        hosted_demand(
            members.get(context.control) == [manager_pid]
            and context.payload in members
            and all(not pids for path, pids in members.items() if path != context.control),
            "only original control manager remains",
        )
        return members
    finally:
        for fd in opened:
            os.close(fd)


class HostedPipes:
    """One thread per already-owned client pipe, no processes or new authority."""

    def __init__(self, process, context, lock):
        self.context = context
        self.lock = lock
        self.eofs = set()
        self.errors = []
        self.totals = {"stdout": 0, "stderr": 0}
        self.seq = 1
        self.threads = []
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            thread = threading.Thread(target=self.drain, args=(name, stream), daemon=True)
            thread.start()
            self.threads.append(thread)

    def drain(self, name, stream):
        try:
            fd = stream.fileno()
            os.set_blocking(fd, False)
            while True:
                ready, _, _ = select.select([fd], [], [], remaining(self.context.deadline))
                hosted_demand(bool(ready), "payload drain deadline")
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    self.eofs.add(name)
                    return
                self.totals[name] += len(chunk)
                with self.lock:
                    hosted_frame(
                        1,
                        self.context,
                        self.seq,
                        name,
                        base64.b64encode(chunk).decode("ascii"),
                        self.context.deadline,
                    )
                    self.seq += 1
        except BaseException:
            self.errors.append(name)
        finally:
            stream.close()

    def check(self):
        hosted_demand(not self.errors, "payload drain/write failure")

    def finish(self):
        for thread in self.threads:
            thread.join(remaining(self.context.deadline))
            hosted_demand(not thread.is_alive(), "payload drainer still active")
        self.check()
        hosted_demand(self.eofs == {"stdout", "stderr"}, "payload EOFs missing")


def hosted_manager(context):
    # Import of the fixed launcher module performs no systemd or other live I/O.
    sys.path.insert(0, context.source)
    from scripts import run_hosted_backend_pytest as hosted

    hosted.validate_manager(context)
    subreaper()
    root = hosted_open_group(context.control.rsplit("/", 1)[0])
    control_root = hosted_open_group(context.control, context.control_identity)
    payload_root = hosted_open_group(context.payload, context.payload_identity)
    payload_events = os.open(
        "cgroup.events", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=payload_root
    )
    events_identity = fd_identity(payload_events)
    os.set_blocking(0, False)
    os.set_blocking(1, False)
    bootstrap_end = min(context.deadline, time.monotonic() + 30)
    hosted_frame(
        1,
        context,
        0,
        "bootstrap",
        {"pid": os.getpid(), "context": context.canonical(), "events_identity": events_identity},
        bootstrap_end,
    )
    hosted_demand(
        hosted_read_frame(0, context, 0, "startup-ack", bootstrap_end) == {}, "startup ACK payload"
    )
    hosted.validate_manager(context)
    local, remote = pair()
    suite_nonce = secrets.token_hex(16)
    environment = {key: os.environ[key] for key in HOSTED_ENV}
    environment[CHANNEL] = f"{remote.fileno()}:{suite_nonce}:{os.getpid()}"
    client = subprocess.Popen(
        [context.interpreter, "-m", "tests.backend_pytest_client", *hosted.phase_args(context)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        close_fds=True,
        pass_fds=(remote.fileno(),),
    )
    remote.close()
    client_pidfd = os.pidfd_open(client.pid, 0)
    write_lock = threading.Lock()
    pipes = HostedPipes(client, context, write_lock)
    retained = []
    failed = False
    receipts = []
    send(
        local,
        suite_nonce,
        0,
        "hosted-hello",
        {
            "manager_pid": os.getpid(),
            "client_pid": client.pid,
            "role": "client",
            **context_frame(context),
        },
        bootstrap_end,
    )
    ack = receive(local, suite_nonce, 0, bootstrap_end)
    hosted_demand(
        ack["kind"] == "hosted-placed"
        and ack["data"]
        == {
            "pid": client.pid,
            "role": "client",
            "group": context.payload,
            "context_digest": context.digest(),
        },
        "direct client placement ACK",
    )
    hosted.check_process_group(client.pid, context.payload)
    send(local, suite_nonce, 1, "hosted-exec", {"context_digest": context.digest()}, bootstrap_end)
    while client.poll() is None:
        remaining(context.deadline)
        pipes.check()
        ready, _, _ = select.select([local], [], [], min(0.05, remaining(context.deadline)))
        if not ready:
            continue
        if local.recv(1, socket.MSG_PEEK) == b"":
            break
        packet = receive(local, None, 0, context.deadline)
        hosted_demand(not failed and packet["kind"] == "run", "request after failure/bad state")
        request = request_from(packet["data"], packet["nonce"])
        hosted_request_budget(context, request)
        manager_one(local, payload_root, context.payload, request, retained, context)
        failed = bool(retained)
        # The manager wrote this exact per-invocation receipt after worker proof.
        record = private_record(
            Path(context.evidence) / ("backend-pytest-" + request.nonce + "-result.json")
        )
        result = record["result"]
        accounted = result.get("accounting", {}).get("accounted") is True and complete(
            result.get("accounting", {}).get("facts")
        )
        hosted_demand(
            complete(result.get("facts")) if result.get("ok") else accounted,
            "per-invocation five facts",
        )
        receipts.append(
            {
                "nonce": request.nonce,
                "ok": result["ok"],
                "rescue_used": result["rescue_used"],
                "accounted": accounted,
            }
        )
    client_status = finish_direct(client, client_pidfd, context.deadline)
    pipes.finish()
    hosted_demand(all(job["release_verified"] is True for job in retained), "unresolved worker")
    # Known direct children were individually reaped above. Any remaining/adopted
    # child is sticky failure, even if this wait then leaves ECHILD.
    unexpected = False
    while True:
        remaining(context.deadline)
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        unexpected = True
        if pid == 0:
            raise SupervisionFailure("hosted: outstanding unexpected/adopted child")
    hosted_demand(not unexpected, "unexpected adopted child was reaped")
    hosted_demand(not select.select([client_pidfd], [], [], 0)[0] == [], "client pidfd death")
    hosted_demand(
        fd_identity(payload_events) == events_identity, "original payload events identity"
    )
    hosted.read_empty(payload_events)
    hosted_only_manager(root, context, os.getpid(), [r["nonce"] for r in receipts])
    status = 1 if failed else client_status
    if context.phase == "accounted-rescue":
        hosted_demand(
            status == 1
            and client_status == 0
            and len(receipts) == 1
            and receipts[0]["rescue_used"]
            and receipts[0]["accounted"]
            and not receipts[0]["ok"],
            "exact accounted-rescue negative",
        )
    else:
        hosted_demand(
            status == 0 and all(r["ok"] and not r["rescue_used"] for r in receipts),
            "normal phase status/proof",
        )
    if context.phase == "suite":
        hosted.require_junit(Path(context.source) / "pytest-diagnostics/pytest-results.xml")
    receipt = {
        "manager_pid": os.getpid(),
        "client_pid": client.pid,
        "client_status": client_status,
        "manager_status": status,
        "facts": dict.fromkeys(
            ("direct_reaped", "no_children", "domain_empty", "stdout_eof", "stderr_eof"), True
        ),
        "stream_bytes": pipes.totals,
        "invocations": receipts,
    }
    # Finish every drain/log/readback task before the terminal protocol boundary.
    local.close()
    os.close(client_pidfd)
    for fd in (payload_events, payload_root, control_root, root):
        os.close(fd)
    remaining(context.deadline)
    hosted_frame(1, context, pipes.seq, "final", receipt, context.deadline)
    digest = hashlib.sha256(hosted_canonical(receipt).encode("ascii")).hexdigest()
    hosted_demand(
        hosted_read_frame(0, context, 1, "final-ack", context.deadline)
        == {"receipt_digest": digest},
        "final ACK receipt binding",
    )
    # No Python shutdown callbacks/finally/logging/new subprocess after valid ACK.
    os._exit(status)


def hosted_entry(argv):
    hosted_demand(
        len(argv) == 5
        and argv[0] == "--hosted-manager"
        and argv[1] == "--context-file"
        and argv[3] == "--phase"
        and argv[4] in HOSTED_PHASES,
        "fixed hosted entry",
    )
    from scripts import run_hosted_backend_pytest as hosted

    data = private_record(argv[2])
    context = hosted.manager_context(data, argv[4], HostedContext)
    hosted_demand(context.phase == argv[4], "entry phase")
    return hosted_manager(context)


def entry(argv):
    if argv and argv[0] == "--hosted-manager":
        return hosted_entry(argv)
    if len(argv) >= 1 and argv[0] == "--manager":
        if len(argv) < 5 or argv[1] != "--context" or argv[3] != "--":
            raise SupervisionFailure("requires --manager --context CANONICAL_JSON -- pytestargs")
        context = InvocationContext.parse(argv[2])
        return manager(argv[4:], context)
    if len(argv) == 4 and argv[0] == "--worker":
        control = socket.socket(fileno=int(argv[1]))
        control.setblocking(False)
        return worker(control, argv[2], float(argv[3]))
    raise SupervisionFailure(
        "requires original managed --manager or private dedicated --worker entry"
    )


if __name__ == "__main__":
    raise SystemExit(entry(sys.argv[1:]))
