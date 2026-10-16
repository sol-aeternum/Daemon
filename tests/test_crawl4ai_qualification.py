"""Independent synthetic fixtures. No Docker, upstream imports or pilot helpers."""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import io
import json
import os
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.crawl4ai_qualification import config, evidence, lifecycle, policy

CONTENT = b"synthetic: true\n"  # NOT upstream config or runtime qualification evidence.
SPEC = policy.Specification("synthetic-daemon", "sha256:" + "a" * 64, ("C4AI_VERSION=0.9.5",))


def archive(content=CONTENT, *, name="config.yml", kind=tarfile.REGTYPE, extra=False):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w", format=tarfile.USTAR_FORMAT) as stream:
        member = tarfile.TarInfo(name)
        member.type, member.size = kind, len(content)
        if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            member.linkname = "/foreign"
        stream.addfile(member, io.BytesIO(content))
        if extra:
            stream.addfile(tarfile.TarInfo("extra"))
    return out.getvalue()


def record(name, run, path, identifier="b" * 64):
    """Explicit fixture independent of policy implementation; no expected-value inference."""
    return {
        "Id": identifier,
        "Name": "/" + name,
        "Image": "sha256:" + "a" * 64,
        "Config": {
            "Labels": {policy.OWNER_LABEL: name, policy.RUN_LABEL: run},
            "User": "appuser",
            "Cmd": ["bash", "entrypoint.sh"],
            "Entrypoint": None,
            "WorkingDir": "/app",
            "Env": ["C4AI_VERSION=0.9.5"],
            "Volumes": None,
            "Healthcheck": {"Test": ["NONE"]},
            "Tty": False,
            "OpenStdin": False,
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Memory": 2147483648,
            "MemorySwap": 2147483648,
            "NanoCpus": 2000000000,
            "PidsLimit": 256,
            "ShmSize": 268435456,
            "Tmpfs": {
                "/tmp": "rw,nosuid,nodev,size=256m,mode=1777",
                "/var/lib/redis": "rw,nosuid,nodev,size=64m,mode=1777",
                "/home/appuser/.crawl4ai": "rw,nosuid,nodev,size=128m,mode=1777",
            },
            "IpcMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "UsernsMode": "",
            "CgroupnsMode": "private",
            "PublishAllPorts": False,
            "AutoRemove": False,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "LogConfig": {"Type": "none", "Config": {}},
            "Mounts": [
                {"Type": "bind", "Source": path, "Target": "/app/config.yml", "ReadOnly": True}
            ],
            **{
                k: None
                for k in (
                    "CapAdd",
                    "Binds",
                    "PortBindings",
                    "Devices",
                    "DeviceRequests",
                    "VolumesFrom",
                    "Links",
                    "ExtraHosts",
                    "Dns",
                    "DnsSearch",
                    "DnsOptions",
                    "Sysctls",
                    "StorageOpt",
                    "Ulimits",
                    "GroupAdd",
                    "CgroupParent",
                    "Runtime",
                )
            },
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": path,
                "Destination": "/app/config.yml",
                "RW": False,
                "Propagation": "rprivate",
                "Mode": "",
            }
        ],
        "NetworkSettings": {
            "Networks": {"none": {"IPAddress": "", "GlobalIPv6Address": ""}},
            "Ports": {},
        },
        "State": {"Status": "created", "Running": False},
    }


def observations():
    def observe(url, authenticated, status, data):
        return evidence.Observation(
            "c" * 32, "synthetic-endpoint", url, authenticated, status, json.dumps(data).encode()
        )

    control = observe(
        evidence.CONTROL_URL,
        True,
        200,
        {
            "success": True,
            "results": [
                {
                    "url": evidence.CONTROL_URL,
                    "success": True,
                    "markdown": {"raw_markdown": evidence.CONTROL_MARKER},
                }
            ],
        },
    )
    unauth = observe(evidence.CONTROL_URL, False, 401, {"detail": "Invalid or missing token"})
    private = tuple(observe(url, True, 400, evidence.DENIAL) for url in evidence.PRIVATE)
    return control, unauth, private


def check_observations(control, unauth, private):
    evidence.require_application_evidence("c" * 32, "synthetic-endpoint", control, unauth, private)


def test_structured_evidence_positive():
    check_observations(*observations())


@pytest.mark.parametrize(
    "fault",
    [
        "401",
        "403",
        "500",
        "bool",
        "transport",
        "malformed",
        "oversize",
        "duplicate",
        "plain",
        "other_detail",
        "identity",
        "auth",
        "missing",
        "extra",
    ],
)
def test_policy_denial_never_inferred_from_unrelated_failures(fault):
    control, unauth, private = observations()
    bad = private[0]
    if fault in ("401", "403", "500"):
        bad = replace(bad, status=int(fault))
    elif fault == "bool":
        bad = replace(bad, status=True)
    elif fault == "transport":
        bad = replace(bad, transport_ok=False)
    elif fault == "malformed":
        bad = replace(bad, raw=b"{")
    elif fault == "oversize":
        bad = replace(bad, raw=b" " * (evidence.MAX_RESPONSE + 1))
    elif fault == "duplicate":
        bad = replace(
            bad, raw=b'{"detail":"bad","detail":"URL blocked (SSRF protection): URL blocked"}'
        )
    elif fault == "plain":
        bad = replace(bad, raw=b'"URL blocked"')
    elif fault == "other_detail":
        bad = replace(bad, raw=b'{"detail":"DNS failed"}')
    elif fault == "identity":
        bad = replace(bad, endpoint="foreign")
    elif fault == "auth":
        bad = replace(bad, authenticated=False)
    private = (bad, *private[1:])
    if fault == "missing":
        private = private[:-1]
    if fault == "extra":
        private = (*private, bad)
    with pytest.raises(config.Refused):
        check_observations(control, unauth, private)


@pytest.mark.parametrize(
    "fault", ["url", "marker", "envelope", "result", "count", "status", "unauth", "run", "nan"]
)
def test_positive_control_must_really_succeed(fault):
    control, unauth, private = observations()
    data = json.loads(control.raw)
    if fault == "url":
        data["results"][0]["url"] = "raw:other"
    if fault == "marker":
        data["results"][0]["markdown"]["raw_markdown"] = "wrong"
    if fault == "envelope":
        data["success"] = False
    if fault == "result":
        data["results"][0]["success"] = False
    if fault == "count":
        data["results"].append(data["results"][0])
    control = replace(control, raw=json.dumps(data).encode())
    if fault == "status":
        control = replace(control, status=500)
    if fault == "unauth":
        unauth = replace(unauth, status=200)
    if fault == "run":
        control = replace(control, run="d" * 32)
    if fault == "nan":
        control = replace(control, raw=b'{"success":NaN}')
    with pytest.raises(config.Refused):
        check_observations(control, unauth, private)


def test_stock_pin_is_upstream_not_old_derivative():
    assert (
        config.STOCK_CONFIG_SHA256
        == "bb332d560b613aec9084bf752e886b070dba33b080f74f2b8d2701258b1f4cfc"
    )
    with pytest.raises(config.Refused):
        config.stock_config(CONTENT)
    with pytest.raises(config.Refused):
        replace(SPEC, image_id=policy.AMD64_MANIFEST)
    with pytest.raises(config.Refused):
        replace(SPEC, image_env=("CRAWL4AI_API_TOKEN=synthetic",))


@pytest.mark.parametrize(
    "fault", ["bytes", "link", "hardlink", "extra", "path", "short", "large", "end"]
)
def test_archive_refuses_nonexact_input(fault):
    variants = {
        "bytes": archive(b"wrong"),
        "link": archive(kind=tarfile.SYMTYPE),
        "hardlink": archive(kind=tarfile.LNKTYPE),
        "extra": archive(extra=True),
        "path": archive(name="../config.yml"),
        "short": archive()[:600],
        "large": archive() + b"\0" * 65536,
        "end": archive()[:1024],
    }
    config.require_config_archive(archive(), CONTENT)
    with pytest.raises(config.Refused):
        config.require_config_archive(variants[fault], CONTENT)


@pytest.mark.parametrize(
    "fault", ["edit", "replace", "symlink", "hardlink", "ancestor", "directory"]
)
def test_staging_refuses_substitution_without_deleting_foreign_input(tmp_path, fault):
    parent = tmp_path / "inputs"
    parent.mkdir(mode=0o700)
    snapshot = config.ConfigSnapshot(str(parent), CONTENT)
    snapshot.create()
    path = Path(snapshot.path)
    original = path.with_name("original")
    if fault == "edit":
        path.chmod(0o600)
        path.write_bytes(b"changed")
    elif fault == "hardlink":
        os.link(path, original)
    elif fault in ("replace", "symlink"):
        path.rename(original)
        if fault == "replace":
            path.write_bytes(CONTENT)
            path.chmod(0o444)
        else:
            path.symlink_to(original)
    elif fault == "ancestor":
        parent.rename(tmp_path / "saved")
        parent.symlink_to(tmp_path / "saved")
    else:
        path.parent.rename(parent / "saved")
        path.parent.mkdir(mode=0o700)
    with pytest.raises((config.Refused, OSError)):
        snapshot.verify()
    if fault != "edit":
        with pytest.raises((config.Refused, OSError)):
            snapshot.cleanup()
        if fault == "hardlink":
            original.unlink()
        elif fault in ("replace", "symlink"):
            path.unlink()
            original.rename(path)
        elif fault == "ancestor":
            parent.unlink()
            (tmp_path / "saved").rename(parent)
        else:
            path.parent.rmdir()
            (parent / "saved").rename(path.parent)
    snapshot.cleanup()
    snapshot.cleanup()
    assert not path.parent.exists()


def test_stage_partial_write_and_symlink_failures_close_descriptors(tmp_path, monkeypatch):
    before = set(os.listdir("/proc/self/fd"))
    snapshot = config.ConfigSnapshot(str(tmp_path), CONTENT)
    monkeypatch.setattr(config.os, "write", lambda *args: 0)
    with pytest.raises(config.Refused):
        snapshot.create()
    snapshot.cleanup()
    assert set(os.listdir("/proc/self/fd")) == before
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    snapshot = config.ConfigSnapshot(str(link), CONTENT)
    with pytest.raises(OSError):
        snapshot.create()
    snapshot.cleanup()


class FakeTransport:
    def __init__(self):
        self.item = None
        self.calls = []
        self.hooks = {}
        self.daemon = SPEC.daemon_id

    async def hook(self, name):
        self.calls.append(name)
        if name in self.hooks:
            await self.hooks[name]()

    async def daemon_identity(self):
        await self.hook("daemon")
        return self.daemon

    async def create(self, spec, name, run, path):
        self.item = record(name, run, path)
        await self.hook("create")
        return self.item["Id"]

    async def find(self, name):
        await self.hook("find")
        return (self.item["Id"],) if self.item else ()

    async def inspect(self, identifier):
        await self.hook("inspect")
        return copy.deepcopy(self.item)

    async def read_config(self, identifier):
        assert self.item is not None
        raw = archive(Path(self.item["Mounts"][0]["Source"]).read_bytes())
        await self.hook("read")
        return raw

    async def start(self, identifier):
        await self.hook("start")
        assert self.item is not None
        self.item["State"] = {"Status": "running", "Running": True}

    async def remove(self, identifier):
        await self.hook("remove")
        self.item = None


@pytest.fixture
def allocation(tmp_path, monkeypatch):
    # Explicit synthetic pin substitution: never claims to exercise stock YAML.
    monkeypatch.setattr(config, "STOCK_CONFIG_SHA256", hashlib.sha256(CONTENT).hexdigest())
    fake = FakeTransport()
    return fake, lifecycle.Allocation(fake, SPEC, str(tmp_path), CONTENT, seconds=0.2)


@pytest.mark.asyncio
async def test_owned_staging_readback_then_cleanup_and_no_reuse(allocation, tmp_path):
    fake, run = allocation
    original = tmp_path / "source.yml"
    original.write_bytes(CONTENT)
    # Allocation owns copied bytes, never mounts this source path.
    original.rename(tmp_path / "old.yml")
    original.write_bytes(b"changed")
    await run.start()
    assert run.filesystem_verified and run.started and not run.absence_verified
    assert Path(run.snapshot.path).read_bytes() == CONTENT
    assert run.snapshot.path != str(original)
    await run.close()
    assert run.absence_verified and not Path(run.snapshot.path).exists() and not run.pending
    assert fake.calls.index("read") < fake.calls.index("start") < fake.calls.index("remove")
    with pytest.raises(config.Refused):
        await run.start()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["read", "state", "mount", "identity", "stage", "post_read", "daemon"]
)
async def test_bad_readback_input_or_identity_prevents_start(allocation, fault):
    fake, run = allocation

    async def create():
        if fault == "state":
            fake.item["State"]["Running"] = True
        if fault == "mount":
            fake.item["Mounts"][0]["Source"] = "/foreign"
        if fault == "identity":
            fake.item["Config"]["Labels"][policy.RUN_LABEL] = "foreign"
        if fault == "daemon":
            fake.daemon = "foreign"
        if fault == "stage":
            path = Path(run.snapshot.path)
            path.chmod(0o600)
            path.write_bytes(b"changed")

    async def read():
        if fault == "read":
            raise OSError("synthetic")
        if fault == "post_read":
            path = Path(run.snapshot.path)
            path.chmod(0o600)
            path.write_bytes(b"changed")

    fake.hooks.update(create=create, read=read)
    with pytest.raises((config.Refused, OSError)):
        await run.start()
    assert "start" not in fake.calls
    if fault in ("identity", "daemon"):
        assert run.cleanup_failed and Path(run.snapshot.path).exists()
        # Test-owned fake reconciliation only; never real container deletion.
        fake.item = None
        run.snapshot.cleanup()


@pytest.mark.asyncio
async def test_unknown_create_reconciles_only_owned_identity(allocation):
    fake, run = allocation

    async def fail():
        raise OSError("synthetic lost create acknowledgement")

    fake.hooks["create"] = fail
    with pytest.raises(OSError):
        await run.start()
    assert fake.item is None and run.absence_verified and not Path(run.snapshot.path).exists()
    assert "start" not in fake.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["create", "read", "start", "remove"])
async def test_repeated_cancellation_keeps_owned_teardown(allocation, stage):
    fake, run = allocation
    entered, release = asyncio.Event(), asyncio.Event()

    async def block():
        entered.set()
        await release.wait()

    fake.hooks[stage] = block
    if stage == "remove":
        await run.start()
        task = asyncio.create_task(run.close())
    else:
        task = asyncio.create_task(run.start())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    waiter = asyncio.create_task(run.close())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert Path(run.snapshot.path).exists() and run.pending
    release.set()
    await run.close()
    assert not run.pending and run.absence_verified and not Path(run.snapshot.path).exists()
    if stage in ("create", "read"):
        assert "start" not in fake.calls


@pytest.mark.asyncio
async def test_unresponsive_create_pins_not_success(allocation):
    fake, run = allocation
    entered, release = asyncio.Event(), asyncio.Event()
    run.seconds = 0.01

    async def block():
        entered.set()
        await release.wait()

    fake.hooks["create"] = block
    with pytest.raises(config.Refused):
        await run.start()
    assert run.cleanup_failed and run.pending and not run.absence_verified
    assert Path(run.snapshot.path).exists() and "remove" not in fake.calls
    release.set()
    await asyncio.gather(*run.pending, return_exceptions=True)
    # Explicit test-owned reconciliation after pending fake settles, not a retry policy.
    run.snapshot.cleanup()


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("HostConfig", "Privileged", True),
        ("HostConfig", "Memory", 0),
        ("HostConfig", "NetworkMode", "host"),
        ("HostConfig", "PidsLimit", -1),
        ("HostConfig", "Devices", ["foreign"]),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "SecurityOpt", ["seccomp=unconfined"]),
        ("Config", "Env", ["CRAWL4AI_ALLOW_INTERNAL_URLS=true"]),
        ("Config", "User", "root"),
        ("Config", "Cmd", ["foreign"]),
    ],
)
def test_container_constraint_drift_refused(section, key, value):
    name, run, path = "daemon-c4ai-" + "a" * 24, "c" * 32, "/synthetic/config.yml"
    item = record(name, run, path)
    policy.created(item, SPEC, name, run, "b" * 64, path)
    item[section][key] = value
    with pytest.raises(config.Refused):
        policy.created(item, SPEC, name, run, "b" * 64, path)


def test_dependency_cut_has_no_excluded_imports_or_native_execution():
    root = Path(__file__).resolve().parents[1]
    for path in (root / "scripts/crawl4ai_qualification").glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(
                "pilot" in n or n.startswith(("subprocess", "socket", "httpx", "urllib"))
                for n in names
            )


@pytest.mark.parametrize("section", ["HostConfig", "Config"])
def test_unknown_native_settings_are_incompatible_not_silently_allowed(section):
    name, run, path = "daemon-c4ai-" + "a" * 24, "c" * 32, "/synthetic/config.yml"
    item = record(name, run, path)
    item[section]["UnreviewedNativeSetting"] = True
    with pytest.raises(config.Refused):
        policy.created(item, SPEC, name, run, "b" * 64, path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["foreign", "multiple", "inspect", "remove", "absence", "listing"]
)
async def test_cleanup_uncertainty_retains_input_and_never_claims_absence(allocation, fault):
    fake, run = allocation
    await run.start()
    if fault == "foreign":
        fake.item["Config"]["Labels"][policy.OWNER_LABEL] = "foreign"
    elif fault == "multiple":
        run.identifier = None  # Model lost-ack reconciliation with ambiguous exact-name matches.

        async def multiple(name):
            return ("b" * 64, "d" * 64)

        fake.find = multiple
    elif fault in ("inspect", "remove", "listing"):

        async def fail():
            raise OSError("synthetic infrastructure failure")

        fake.hooks[{"listing": "find"}.get(fault, fault)] = fail
    else:

        async def no_effect(identifier):
            await fake.hook("remove")  # Acknowledged, but the object still exists.

        fake.remove = no_effect
    with pytest.raises((config.Refused, OSError)):
        await run.close()
    assert run.cleanup_failed and not run.absence_verified and Path(run.snapshot.path).exists()
    if fault in ("foreign", "multiple", "inspect"):
        assert "remove" not in fake.calls
    fake.item = None
    run.snapshot.cleanup()  # Only fake test resources; real uncertainty stays pinned.


@pytest.mark.asyncio
async def test_authoritative_already_absent_does_not_invent_exit_state(allocation):
    fake, run = allocation
    await run.start()
    fake.item = None
    await run.close()
    assert run.absence_verified and "remove" not in fake.calls
    assert not hasattr(run, "exit_code")


@pytest.mark.asyncio
async def test_closed_never_started_allocation_refuses_without_transport_io(allocation):
    fake, run = allocation
    await run.close()
    with pytest.raises(config.Refused):
        await run.start()
    assert not fake.calls and not run.pending and run._setup is None


@pytest.mark.asyncio
async def test_post_readback_staged_replacement_prevents_start_and_is_retained(allocation):
    fake, run = allocation

    async def replace_stage():
        path = Path(run.snapshot.path)
        path.rename(path.with_name("original"))
        path.write_bytes(CONTENT)
        path.chmod(0o444)

    fake.hooks["read"] = replace_stage
    with pytest.raises(config.Refused):
        await run.start()
    assert "start" not in fake.calls and run.cleanup_failed
    path = Path(run.snapshot.path)
    assert path.exists()  # Do not delete a byte-identical but foreign replacement.
    path.unlink()
    path.with_name("original").rename(path)
    run.snapshot.cleanup()


def test_production_remains_direct_only():
    from orchestrator.services.fetch.service import FetchService

    service = FetchService()
    assert service.crawl4ai_strategy is None
    assert tuple(name for name, _ in service._default_strategy_chain()) == ("direct",)
