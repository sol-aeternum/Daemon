"""Fixed hosted-only transient-unit pytest recipe; no local fallback or retries.

Importing this module never invokes sudo, systemd or registration interfaces.
Raw private launch contexts stay outside the explicitly uploaded diagnostics.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import selectors
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import TypedDict, cast
import xml.etree.ElementTree as ET


def supervisor():
    from tests import backend_pytest_supervisor

    return backend_pytest_supervisor


DB_URL = "postgresql://tasks:tasks-ci-only@127.0.0.1:5432/tasks_test"
APP_ENV = {
    "PYTHONFAULTHANDLER": "1",
    "PYTHONUNBUFFERED": "1",
    "DAEMON_PYTEST_ASYNCIO_DUMP_S": "90",
    "TASKS_TEST_DATABASE_URL": DB_URL,
}
PROPERTIES = {
    "Delegate": "",
    "DelegateSubgroup": "manager",
    "ExitType": "cgroup",
    "Restart": "no",
    "TimeoutStartSec": "30",
    "TimeoutStopSec": "15",
    "KillMode": "control-group",
    "KillSignal": "SIGABRT",
    "FinalKillSignal": "SIGKILL",
    "SendSIGKILL": "yes",
    "CollectMode": "inactive",
    "NoNewPrivileges": "yes",
    "CapabilityBoundingSet": "",
    "AmbientCapabilities": "",
    "LimitCORE": "0",
    "UMask": "0077",
}
READ_PROPERTIES = (
    "Id",
    "LoadState",
    "Description",
    "Transient",
    "User",
    "Group",
    "InvocationID",
    "MainPID",
    "ExecMainPID",
    "ExecMainCode",
    "ExecMainStatus",
    "ControlGroup",
    "ActiveState",
    "SubState",
    "Result",
    "Delegate",
    "DelegateControllers",
    "DelegateSubgroup",
    "ExitType",
    "CollectMode",
    "NoNewPrivileges",
    "CapabilityBoundingSet",
    "AmbientCapabilities",
    "Type",
    "SupplementaryGroups",
    "WorkingDirectory",
    "RuntimeMaxUSec",
    "TimeoutStartUSec",
    "TimeoutStopUSec",
    "KillMode",
    "KillSignal",
    "FinalKillSignal",
    "SendSIGKILL",
    "Restart",
    "LimitCORE",
    "UMask",
)


def demand(condition, reason):
    supervisor().hosted_demand(condition, reason)


def canonical(value):
    return supervisor().hosted_canonical(value)


def seed_fields():
    return {
        "tag",
        "run",
        "attempt",
        "phase",
        "nonce",
        "unit",
        "source",
        "resources",
        "evidence",
        "interpreter",
        "uid",
        "gid",
        "groups",
        "deadline",
        "home",
        "path",
    }


class LaunchSeed(TypedDict):
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
    home: str
    path: str


def validate_seed(seed):
    s = supervisor()
    demand(type(seed) is dict and set(seed) == seed_fields(), "launch context fields")
    # Structural view for the checks below, not a substitute for validation.
    seed = cast(LaunchSeed, seed)
    # The full hosted parser also validates all shared fields before any side effect.
    probe = {key: value for key, value in seed.items() if key not in {"home", "path"}}
    group = "/system.slice/" + seed["unit"] + "/manager"
    probe.update(
        invocation="0" * 32,
        control=group,
        payload=group + "/payload-" + seed["nonce"],
        control_identity=[1, 1, seed["uid"], seed["gid"], 448],
        payload_identity=[1, 2, seed["uid"], seed["gid"], 448],
    )
    s.HostedContext.parse(canonical(probe))
    for key in ("home", "path"):
        demand(
            type(seed[key]) is str
            and seed[key]
            and "\0" not in seed[key]
            and "\n" not in seed[key],
            "launch environment path",
        )
    demand(Path(seed["home"]).is_absolute(), "absolute HOME")
    for value in seed["path"].split(":"):
        demand(
            value and Path(value).is_absolute() and Path(value).is_dir(), "checked existing PATH"
        )
    source = Path(seed["source"])
    demand(source.resolve(strict=True) == source and source.is_dir(), "real checkout source")
    demand(
        s.local_interpreter(source, str(Path(seed["interpreter"]).parent.parent))
        == seed["interpreter"],
        "selected real checkout-local venv",
    )
    for path in (Path(seed["resources"]), source / ".hosted-pytest"):
        info = path.lstat()
        demand(
            not path.is_symlink()
            and path.resolve(strict=True) == path
            and stat.S_ISDIR(info.st_mode)
            and info.st_uid == seed["uid"]
            and stat.S_IMODE(info.st_mode) == 0o700,
            "private hosted roots",
        )
    s.remaining(seed["deadline"])


def app_environment(seed):
    return {
        "HOME": seed["home"],
        "PATH": seed["path"],
        "UV_PROJECT_ENVIRONMENT": str(Path(seed["interpreter"]).parent.parent),
        "PYTHONPATH": seed["source"],
        **APP_ENV,
    }


def recipe(seed):
    argv = [
        "/usr/bin/sudo",
        "-n",
        "--",
        "/usr/bin/systemd-run",
        "--system",
        "--no-ask-password",
        "--quiet",
        "--unit=" + seed["unit"],
        "--description=" + description(seed),
        "--service-type=exec",
        "--uid=" + str(seed["uid"]),
        "--gid=" + str(seed["gid"]),
        "--working-directory=" + seed["source"],
        "--wait",
        "--pipe",
        "--expand-environment=no",
    ]
    for key, value in PROPERTIES.items():
        argv.append("--property=" + key + "=" + value)
        if key == "Restart":
            argv.append(
                "--property=RuntimeMaxSec=" + str(supervisor().HOSTED_PHASES[seed["phase"]])
            )
    argv.extend("--setenv=" + key + "=" + value for key, value in app_environment(seed).items())
    argv.extend(
        [
            "--",
            seed["interpreter"],
            "-B",
            seed["source"] + "/tests/backend_pytest_supervisor.py",
            "--hosted-manager",
            "--context-file",
            seed["evidence"] + "/context.json",
            "--phase",
            seed["phase"],
        ]
    )
    return argv


def description(seed):
    return (
        "Daemon hosted pytest "
        + seed["run"]
        + "/"
        + seed["attempt"]
        + "/"
        + seed["phase"]
        + "/"
        + seed["nonce"]
    )


def readback(unit, deadline, *, allow_absent=False):
    s = supervisor()
    result = subprocess.run(
        [
            "/usr/bin/systemctl",
            "--system",
            "show",
            "--no-pager",
            *("--property=" + key for key in READ_PROPERTIES),
            "--",
            unit,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
        timeout=min(5, s.remaining(deadline)),
        check=False,
    )
    demand(
        result.returncode in ({0, 1} if allow_absent else {0}) and len(result.stdout) <= 65536,
        "bounded unit readback",
    )
    values = {}
    for line in result.stdout.decode("utf-8", "strict").splitlines():
        key, separator, value = line.partition("=")
        demand(separator and key in READ_PROPERTIES and key not in values, "unit readback shape")
        values[key] = value
    demand(set(values) == set(READ_PROPERTIES), "complete unit readback")
    demand(result.returncode == 0 or values["LoadState"] == "not-found", "readback exit status")
    return values


def validate_properties(values, seed, *, context=None, terminal=False):
    expected = {
        "Id": seed["unit"],
        "LoadState": "loaded",
        "Description": description(seed),
        "Transient": "yes",
        "User": str(seed["uid"]),
        "Group": str(seed["gid"]),
        "Type": "exec",
        "WorkingDirectory": seed["source"],
        "Delegate": "yes",
        "DelegateControllers": "",
        "DelegateSubgroup": "manager",
        "ExitType": "cgroup",
        "CollectMode": "inactive",
        "NoNewPrivileges": "yes",
        "CapabilityBoundingSet": "",
        "AmbientCapabilities": "",
        "Restart": "no",
        "KillMode": "control-group",
        "KillSignal": "6",
        "FinalKillSignal": "9",
        "SendSIGKILL": "yes",
        "LimitCORE": "0",
        "UMask": "0077",
        "TimeoutStartUSec": "30s",
        "TimeoutStopUSec": "15s",
    }
    for key, value in expected.items():
        demand(values.get(key) == value, "exact recipe property " + key)
    # systemctl show renders the runtime interval in human-readable units.
    runtime = {60: "1min", 1200: "20min"}[supervisor().HOSTED_PHASES[seed["phase"]]]
    demand(values["RuntimeMaxUSec"] == runtime, "unchanged runtime limit")
    demand(re.fullmatch(r"[0-9a-f]{32}", values["InvocationID"]), "actual InvocationID")
    if context is not None:
        demand(
            values["InvocationID"] == context.invocation
            and values["ControlGroup"] + "/manager" == context.control,
            "unit/context binding",
        )
    if not terminal:
        demand(
            values["ActiveState"] == "active"
            and values["SubState"] == "running"
            and values["MainPID"].isdigit()
            and int(values["MainPID"]) > 0
            and values["MainPID"] == values["ExecMainPID"],
            "original active manager",
        )


def check_process_group(pid, expected):
    raw = Path("/proc/" + str(pid) + "/cgroup").read_text(encoding="ascii")
    demand(raw == "0::" + expected + "\n", "exact original process cgroup")


def namespace_check(pid):
    for name in ("mnt", "cgroup"):
        own = os.stat("/proc/self/ns/" + name)
        other = os.stat("/proc/" + str(pid) + "/ns/" + name)
        demand((own.st_dev, own.st_ino) == (other.st_dev, other.st_ino), "namespace agreement")
    with open("/proc/self/mountinfo", encoding="utf-8") as stream:
        mounts = [line.split() for line in stream if " - cgroup2 " in line]
    demand(
        len(mounts) == 1 and mounts[0][3:5] == ["/", "/sys/fs/cgroup"], "canonical cgroupv2 mount"
    )


def manager_context(seed, phase, context_type):
    s = supervisor()
    validate_seed(seed)
    demand(seed["phase"] == phase, "phase/seed")
    values = readback(seed["unit"], seed["deadline"])
    validate_properties(values, seed)
    demand(int(values["MainPID"]) == os.getpid(), "original service exec only")
    s.hosted_credentials(SimpleNamespace(**seed))
    namespace_check(os.getpid())
    control = values["ControlGroup"] + "/manager"
    check_process_group(os.getpid(), control)
    root = s.hosted_open_group(control)
    try:
        name = "payload-" + seed["nonce"]
        os.mkdir(name, mode=0o700, dir_fd=root)  # fail collision; never adoption
        payload = os.open(name, s.DIR_FLAGS, dir_fd=root)
        try:
            data = {key: value for key, value in seed.items() if key not in {"home", "path"}}
            data.update(
                invocation=values["InvocationID"],
                control=control,
                payload=control + "/" + name,
                control_identity=s.fd_identity(root),
                payload_identity=s.fd_identity(payload),
            )
            context = context_type.parse(canonical(data))
        finally:
            os.close(payload)
    finally:
        os.close(root)
    return context


def validate_manager(context):
    s = supervisor()
    seed = s.private_record(Path(context.evidence) / "context.json")
    validate_seed(seed)
    demand(
        all(context.wire()[key] == seed[key] for key in seed_fields() - {"home", "path"}),
        "manager immutable launch binding",
    )
    s.hosted_credentials(context)
    demand(
        set(os.environ) <= s.HOSTED_ENV | s.HOSTED_SYSTEM_ENV
        and all(os.environ.get(key) == value for key, value in app_environment(seed).items()),
        "allowlisted actual service environment",
    )
    demand(os.environ.get("INVOCATION_ID") == context.invocation, "systemd environment identity")
    values = readback(context.unit, context.deadline)
    validate_properties(values, seed, context=context)
    demand(
        int(values["MainPID"]) == os.getpid()
        and os.path.abspath(sys.executable) == context.interpreter
        and os.path.abspath(sys.prefix) == str(Path(context.interpreter).parent.parent)
        and sys.prefix != sys.base_prefix,
        "actual manager/interpreter",
    )
    check_process_group(os.getpid(), context.control)
    namespace_check(os.getpid())
    expected = [
        context.interpreter,
        "-B",
        context.source + "/tests/backend_pytest_supervisor.py",
        "--hosted-manager",
        "--context-file",
        context.evidence + "/context.json",
        "--phase",
        context.phase,
    ]
    demand(
        Path("/proc/self/cmdline").read_bytes().split(b"\0")[:-1] == [x.encode() for x in expected],
        "actual fixed service exec",
    )


def read_empty(fd):
    raw = os.pread(fd, 4097, 0)
    demand(bool(raw) and len(raw) <= 4096 and raw.endswith(b"\n"), "actual held events read")
    values = {}
    for line in raw.splitlines():
        parts = line.split()
        demand(len(parts) == 2 and parts[0] not in values and parts[1].isdigit(), "events shape")
        values[parts[0]] = parts[1]
    demand(values.get(b"populated") == b"0", "payload actually empty")


def phase_args(context):
    if context.phase == "positive":
        return ["-q", "tests/backend_pytest_hosted_probe.py::test_native_success"]
    if context.phase == "accounted-rescue":
        return ["-q", "tests/backend_pytest_hosted_probe.py::test_accounted_rescue"]
    demand(context.phase == "suite", "fixed suite phase")
    return [
        "-vv",
        "--durations=30",
        "-o",
        "faulthandler_timeout=120",
        "--junitxml=" + context.source + "/pytest-diagnostics/pytest-results.xml",
    ]


def require_junit(path):
    demand(
        path.is_file() and not path.is_symlink() and path.stat().st_size <= 64 * 1024 * 1024,
        "bounded JUnit exists",
    )
    tree = ET.parse(path)  # only original locally generated pytest output
    demand(tree.getroot().tag in {"testsuites", "testsuite"}, "JUnit root")


class OuterReader:
    def __init__(self, process, log, deadline):
        self.process = process
        self.log = log
        self.deadline = deadline
        self.selector = selectors.DefaultSelector()
        self.eofs = set()
        self.buffer = bytearray()
        self.frames = []
        self.seq = 1
        self.totals = {"stdout": 0, "stderr": 0}
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ, name)

    def tick(self):
        for key, _ in self.selector.select(min(0.05, supervisor().remaining(self.deadline))):
            part = os.read(key.fd, 4096)
            if not part:
                self.eofs.add(key.data)
                self.selector.unregister(key.fileobj)
                continue
            if key.data == "stderr":
                demand(self.log.write(part) == len(part), "outer stderr log write")
                continue
            self.buffer.extend(part)
            while b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                demand(len(line) < supervisor().MAX_PACKET, "outer frame bound")
                self.buffer = bytearray(rest)
                frame = json.loads(line, object_pairs_hook=supervisor().unique)
                demand(
                    type(frame) is dict
                    and set(frame)
                    == {"tag", "nonce", "phase", "context_digest", "seq", "kind", "data"},
                    "outer frame exact fields",
                )
                self.frames.append(frame)
            demand(len(self.buffer) <= supervisor().MAX_PACKET, "outer partial frame bound")

    def next(self):
        while not self.frames:
            demand("stdout" not in self.eofs, "outer EOF before receipt")
            self.tick()
        return self.frames.pop(0)

    def checked(self, context):
        while True:
            frame = self.next()
            demand(
                frame["tag"] == context.tag
                and frame["nonce"] == context.nonce
                and frame["phase"] == context.phase
                and frame["context_digest"] == context.digest()
                and type(frame["seq"]) is int,
                "outer authenticated context",
            )
            if frame["kind"] in {"stdout", "stderr"}:
                demand(
                    frame["seq"] == self.seq and type(frame["data"]) is str, "payload frame state"
                )
                chunk = base64.b64decode(frame["data"], validate=True)
                demand(
                    0 < len(chunk) <= 4096 and self.log.write(chunk) == len(chunk),
                    "exact payload log bytes",
                )
                self.totals[frame["kind"]] += len(chunk)
                self.seq += 1
                continue
            return frame


class InvocationSummary(TypedDict):
    nonce: str
    ok: bool
    rescue_used: bool
    accounted: bool


class FinalReceipt(TypedDict):
    manager_pid: int
    client_pid: int
    client_status: int
    manager_status: int
    facts: dict[str, bool]
    stream_bytes: dict[str, int]
    invocations: list[InvocationSummary]


def verify_receipt(receipt, context, manager_pid):
    s = supervisor()
    demand(
        type(receipt) is dict
        and set(receipt)
        == {
            "manager_pid",
            "client_pid",
            "client_status",
            "manager_status",
            "facts",
            "stream_bytes",
            "invocations",
        }
        and receipt["manager_pid"] == manager_pid
        and type(receipt["client_pid"]) is int
        and receipt["client_pid"] > 0
        and type(receipt["client_status"]) is int
        and type(receipt["manager_status"]) is int
        and s.complete(receipt["facts"]),
        "complete actual final receipt",
    )
    # Nested shapes and values remain checked below before acceptance.
    receipt = cast(FinalReceipt, receipt)
    demand(
        type(receipt["stream_bytes"]) is dict
        and set(receipt["stream_bytes"]) == {"stdout", "stderr"}
        and all(type(v) is int and v >= 0 for v in receipt["stream_bytes"].values()),
        "stream totals",
    )
    demand(
        type(receipt["invocations"]) is list and len(receipt["invocations"]) < 4096, "receipt list"
    )
    for record in receipt["invocations"]:
        demand(
            type(record) is dict
            and set(record) == {"nonce", "ok", "rescue_used", "accounted"}
            and type(record["nonce"]) is str
            and s.HEX.fullmatch(record["nonce"])
            and all(type(record[k]) is bool for k in ("ok", "rescue_used", "accounted")),
            "exact invocation summary",
        )
    demand(
        len({r["nonce"] for r in receipt["invocations"]}) == len(receipt["invocations"]),
        "unique invocation receipts",
    )
    if context.phase == "accounted-rescue":
        demand(
            receipt["manager_status"] == 1
            and receipt["client_status"] == 0
            and len(receipt["invocations"]) == 1
            and receipt["invocations"][0]["rescue_used"]
            and receipt["invocations"][0]["accounted"]
            and not receipt["invocations"][0]["ok"],
            "negative remains failed manager1",
        )
    else:
        demand(
            receipt["manager_status"] == receipt["client_status"] == 0
            and all(r["ok"] and not r["rescue_used"] for r in receipt["invocations"]),
            "normal receipt",
        )


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def terminal_gate(values, seed, context, manager_pid, receipt, sudo_status, manager_dead, eofs):
    validate_properties(values, seed, context=context, terminal=True)
    expected = receipt["manager_status"]
    demand(
        manager_dead
        and sudo_status == expected
        and eofs == {"stdout", "stderr"}
        and values["ExecMainPID"] == str(manager_pid)
        and values["ExecMainCode"] == "1"
        and values["ExecMainStatus"] == str(expected)
        and values["ActiveState"] == ("failed" if expected else "inactive")
        and values["Result"] == ("exit-code" if expected else "success"),
        "actual terminal unit/manager/sudo/EOF correlation",
    )


def run_phase(seed, log, progress):
    s = supervisor()
    validate_seed(seed)
    before = readback(seed["unit"], seed["deadline"], allow_absent=True)
    demand(before["LoadState"] == "not-found", "positive fresh-unit absence")
    write_private(Path(seed["evidence"]) / "context.json", seed)
    process = subprocess.Popen(
        recipe(seed),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
    )
    # All three descriptors are guaranteed by the PIPE constructor arguments.
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    sudo_pidfd = os.pidfd_open(process.pid, 0)  # the actual direct child is sudo
    progress.update(sudo_pid=process.pid, stage="bootstrap-pending")
    reader = OuterReader(process, log, seed["deadline"])
    reader.deadline = min(seed["deadline"], time.monotonic() + 30)
    initial = reader.next()
    demand(
        initial["kind"] == "bootstrap"
        and initial["seq"] == 0
        and type(initial["data"]) is dict
        and set(initial["data"]) == {"pid", "context", "events_identity"},
        "initial bootstrap frame",
    )
    context = s.HostedContext.parse(initial["data"]["context"])
    demand(
        all(context.wire()[key] == seed[key] for key in seed_fields() - {"home", "path"})
        and initial["tag"] == context.tag
        and initial["nonce"] == context.nonce
        and initial["phase"] == context.phase
        and initial["context_digest"] == context.digest(),
        "original launch/bootstrap correlation",
    )
    pid = initial["data"]["pid"]
    demand(type(pid) is int and pid > 0, "original MainPID")
    manager_pidfd = os.pidfd_open(pid, 0)
    progress.update(manager_pid=pid, invocation=context.invocation)
    values = readback(context.unit, context.deadline)
    validate_properties(values, seed, context=context)
    demand(int(values["MainPID"]) == pid, "original blocked manager identity")
    namespace_check(pid)
    check_process_group(pid, context.control)
    root = s.hosted_open_group(values["ControlGroup"])
    control = s.hosted_open_group(context.control, context.control_identity)
    payload = s.hosted_open_group(context.payload, context.payload_identity)
    events = os.open("cgroup.events", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=payload)
    identities = {fd: s.fd_identity(fd) for fd in (root, control, payload, events)}
    demand(identities[events] == initial["data"]["events_identity"], "same held events")
    read_empty(events)
    s.hosted_only_manager(root, context, pid)
    demand(not select.select([manager_pidfd], [], [], 0)[0], "manager alive before startup ACK")
    # The manager itself checks actual credentials; outer checks all process IDs,
    # namespaces and the fixed unit readback independently before release.
    s.hosted_frame(process.stdin.fileno(), context, 0, "startup-ack", {}, reader.deadline)
    reader.deadline = context.deadline
    progress["stage"] = "payload-running"
    final = reader.checked(context)
    demand(final["kind"] == "final" and final["seq"] == reader.seq, "single terminal receipt")
    receipt = final["data"]
    verify_receipt(receipt, context, pid)
    progress.update(stage="payload-receipt", receipt=receipt)
    demand(receipt["stream_bytes"] == reader.totals, "independent stream byte counts")
    for fd, identity in identities.items():
        demand(s.fd_identity(fd) == identity, "held identity continuity")
    read_empty(events)
    s.hosted_only_manager(root, context, pid, [r["nonce"] for r in receipt["invocations"]])
    values = readback(context.unit, context.deadline)
    validate_properties(values, seed, context=context)
    demand(
        int(values["MainPID"]) == pid and not select.select([manager_pidfd], [], [], 0)[0],
        "same active manager at final barrier",
    )
    log.flush()
    os.fsync(log.fileno())
    digest = hashlib.sha256(canonical(receipt).encode("ascii")).hexdigest()
    s.hosted_frame(
        process.stdin.fileno(),
        context,
        1,
        "final-ack",
        {"receipt_digest": digest},
        context.deadline,
    )
    process.stdin.close()
    progress["stage"] = "final-ack-sent"
    while process.poll() is None or reader.eofs != {"stdout", "stderr"}:
        reader.tick()
        demand(not reader.frames and not reader.buffer, "unexpected post-ACK output/frame")
    dead = bool(select.select([manager_pidfd], [], [], 0)[0])
    progress.update(
        sudo_direct_status=process.returncode,
        manager_dead=dead,
        outer_stdout_eof="stdout" in reader.eofs,
        outer_stderr_eof="stderr" in reader.eofs,
    )
    values = readback(context.unit, context.deadline)
    terminal_gate(values, seed, context, pid, receipt, process.returncode, dead, reader.eofs)
    if context.phase == "suite":
        require_junit(Path(context.source) / "pytest-diagnostics/pytest-results.xml")
    record = {
        "tag": context.tag,
        "run": context.run,
        "attempt": context.attempt,
        "phase": context.phase,
        "nonce": context.nonce,
        "unit": context.unit,
        "invocation": context.invocation,
        "manager_pid": pid,
        "sudo_pid": process.pid,
        "sudo_direct_status": process.returncode,
        "systemd_run_status": "propagated-through-sudo",
        "manager_dead": dead,
        "receipt": receipt,
        "payload_identity": identities[payload],
        "outer_stdout_eof": True,
        "outer_stderr_eof": True,
        "uid": context.uid,
        "gid": context.gid,
        "groups": list(context.groups),
        "ExecMainCode": values["ExecMainCode"],
        "ExecMainStatus": values["ExecMainStatus"],
        "Result": values["Result"],
        "scope": "trusted-same-UID/non-migrating",
    }
    for fd in (*identities, sudo_pidfd, manager_pidfd):
        os.close(fd)
    reader.selector.close()
    process.stdout.close()
    process.stderr.close()
    return record


def main():
    s = supervisor()
    demand(sys.argv[1:] == [], "fixed hosted runner takes no arguments")
    demand(
        os.getuid() > 0 and os.getgid() > 0 and os.environ.get("GITHUB_ACTIONS") == "true",
        "hosted nonroot caller only",
    )
    version = subprocess.run(
        ["/usr/bin/systemd-run", "--version"],
        capture_output=True,
        timeout=5,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C"},
    )
    match = re.match(rb"systemd ([0-9]+)", version.stdout)
    demand(version.returncode == 0 and match and int(match[1]) >= 254, "installed systemd>=254")
    source = Path(__file__).resolve().parents[1]
    interpreter = s.local_interpreter(source, os.environ.get("UV_PROJECT_ENVIRONMENT"))
    private = source / ".hosted-pytest"
    if not private.exists():
        private.mkdir(mode=0o700)
    diagnostics = source / "pytest-diagnostics"
    diagnostics.mkdir(exist_ok=True)
    log_path = diagnostics / "pytest.log"
    records = []
    failed = True
    with log_path.open("xb") as log:
        try:
            for phase, seconds in s.HOSTED_PHASES.items():
                nonce = secrets.token_hex(16)
                evidence = private / nonce
                evidence.mkdir(mode=0o700)
                run = os.environ.get("GITHUB_RUN_ID", "")
                attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
                seed = {
                    "tag": s.HOSTED_TAG,
                    "run": run,
                    "attempt": attempt,
                    "phase": phase,
                    "nonce": nonce,
                    "unit": "daemon-pytest-"
                    + run
                    + "-"
                    + attempt
                    + "-"
                    + phase
                    + "-"
                    + nonce
                    + ".service",
                    "source": str(source),
                    "resources": str(evidence),
                    "evidence": str(evidence),
                    "interpreter": interpreter,
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "groups": sorted(os.getgroups()),
                    "deadline": time.monotonic() + seconds,
                    "home": os.environ.get("HOME", ""),
                    "path": os.environ.get("PATH", ""),
                }
                records.append(
                    {
                        "status": "UNKNOWN/failed",
                        "stage": "precreation",
                        "unit": seed["unit"],
                        "nonce": nonce,
                        "phase": phase,
                        "sudo_direct_status": None,
                        "manager_dead": None,
                    }
                )
                records[-1] = run_phase(seed, log, records[-1])
            failed = False
        finally:
            # Explicit sanitized output only; UNKNOWN lacks a forged completion receipt.
            write_private(
                diagnostics / "hosted-closure.json",
                {"status": "UNKNOWN/failed" if failed else "qualified", "phases": records},
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
