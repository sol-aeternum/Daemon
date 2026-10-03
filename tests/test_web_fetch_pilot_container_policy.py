"""Supplied-record tests only: no Docker, subprocess or network execution."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_container_policy import (
    BROWSER_MEMORY,
    BROWSER_SECCOMP_SHA256,
    BROWSER_SHM,
    BROWSER_TMPFS,
    IMAGE,
    MEMORY,
    OWNER_LABEL,
    TMPFS,
    RUN_LABEL,
    PreflightError,
    browser_create_arguments,
    browser_seccomp_option,
    create_arguments,
    require_browser_identity,
    require_identity,
    require_offline_browser,
    require_offline_gateway,
)

NAME = "daemon-dns-offline-" + "a" * 24
IDENTIFIER = "b" * 64
COMMAND = ("-i", "/usr/local/bin/python", "-I", "-S", "-u", "-c", "trusted fixture")


def fixture() -> dict:
    return {
        "Id": IDENTIFIER,
        "Name": "/" + NAME,
        "Image": IMAGE,
        "Config": {
            "User": "appuser",
            "Labels": {OWNER_LABEL: NAME},
            "Volumes": None,
            "Entrypoint": ["/usr/bin/env"],
            "Cmd": list(COMMAND),
            "Tty": False,
            "OpenStdin": True,
            "StdinOnce": True,
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Healthcheck": {"Test": ["NONE"]},
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Memory": MEMORY,
            "MemorySwap": MEMORY,
            "NanoCpus": 500_000_000,
            "PidsLimit": 32,
            "Tmpfs": copy.deepcopy(TMPFS),
            "IpcMode": "none",
            "PidMode": "",
            "UTSMode": "",
            "UsernsMode": "",
            "CgroupnsMode": "private",
            "PublishAllPorts": False,
            "AutoRemove": False,
            "CapAdd": None,
            "Binds": None,
            "PortBindings": {},
            "Devices": [],
            "DeviceRequests": None,
            "VolumesFrom": None,
            "Links": None,
            "ExtraHosts": None,
            "Dns": [],
            "DnsSearch": [],
            "DnsOptions": [],
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "LogConfig": {"Type": "none", "Config": {}},
        },
        "Mounts": [],
        "NetworkSettings": {
            "Networks": {"none": {"IPAddress": "", "GlobalIPv6Address": ""}},
            "Ports": {},
        },
    }


def test_approved_record_and_cleanup_identity() -> None:
    item = fixture()
    assert require_offline_gateway(item, NAME, COMMAND, container_id=IDENTIFIER) == IDENTIFIER
    item["HostConfig"]["Privileged"] = True
    assert require_identity(item, NAME, container_id=IDENTIFIER) == IDENTIFIER
    with pytest.raises(PreflightError):
        require_offline_gateway(item, NAME, COMMAND)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("", "Image", "sha256:" + "0" * 64),
        ("", "Name", "/foreign"),
        ("", "Id", "short"),
        ("Config", "Labels", {OWNER_LABEL: "foreign"}),
        ("Config", "User", "root"),
        ("Config", "Entrypoint", ["/bin/sh"]),
        ("Config", "Cmd", ["foreign"]),
        ("Config", "Tty", True),
        ("Config", "OpenStdin", False),
        ("Config", "StdinOnce", False),
        ("Config", "AttachStdin", False),
        ("Config", "AttachStdout", False),
        ("Config", "AttachStderr", False),
        ("Config", "Volumes", {"/data": {}}),
        ("Config", "Healthcheck", {"Test": ["CMD", "curl"]}),
        ("HostConfig", "NetworkMode", "bridge"),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "Privileged", True),
        ("HostConfig", "CapDrop", []),
        ("HostConfig", "SecurityOpt", ["no-new-privileges", "seccomp=unconfined"]),
        ("HostConfig", "Memory", MEMORY * 2),
        ("HostConfig", "MemorySwap", -1),
        ("HostConfig", "NanoCpus", 1_000_000_000),
        ("HostConfig", "PidsLimit", 128),
        ("HostConfig", "Tmpfs", {"/tmp": "rw,size=128m"}),
        ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "UTSMode", "host"),
        ("HostConfig", "UsernsMode", "host"),
        ("HostConfig", "CgroupnsMode", "host"),
        ("HostConfig", "PublishAllPorts", True),
        ("HostConfig", "AutoRemove", True),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "Binds", ["/var/run/docker.sock:/docker.sock"]),
        ("HostConfig", "PortBindings", {"443/tcp": [{"HostPort": "443"}]}),
        ("HostConfig", "Devices", [{"PathOnHost": "/dev/fuse"}]),
        ("HostConfig", "DeviceRequests", [{"Driver": "nvidia"}]),
        ("HostConfig", "VolumesFrom", ["foreign"]),
        ("HostConfig", "Links", ["database"]),
        ("HostConfig", "ExtraHosts", ["fixture.invalid:127.0.0.1"]),
        ("HostConfig", "Dns", ["8.8.8.8"]),
        ("HostConfig", "DnsSearch", ["internal"]),
        ("HostConfig", "DnsOptions", ["rotate"]),
        ("HostConfig", "RestartPolicy", {"Name": "always", "MaximumRetryCount": 0}),
        ("HostConfig", "LogConfig", {"Type": "json-file", "Config": {}}),
        ("", "Mounts", [{"Type": "bind", "Destination": "/tmp", "RW": True}]),
        ("NetworkSettings", "Networks", {"bridge": {}}),
        (
            "NetworkSettings",
            "Networks",
            {"none": {"IPAddress": "1.2.3.4", "GlobalIPv6Address": ""}},
        ),
        ("NetworkSettings", "Ports", {"443/tcp": [{"HostPort": "443"}]}),
    ],
)
def test_refuses_boundary_mutations(section: str, key: str, value: object) -> None:
    item = fixture()
    target = item[section] if section else item
    target[key] = value
    with pytest.raises(PreflightError, match="offline gateway preflight refused"):
        require_offline_gateway(item, NAME, COMMAND)


@pytest.mark.parametrize("identifier", [None, False, "", "b" * 63, "b" * 65, "B" * 64])
def test_cleanup_refuses_invalid_id(identifier: object) -> None:
    item = fixture()
    item["Id"] = identifier
    with pytest.raises(PreflightError):
        require_identity(item, NAME)


def test_cleanup_refuses_other_retained_id() -> None:
    with pytest.raises(PreflightError):
        require_identity(fixture(), NAME, container_id="c" * 64)


def test_missing_required_field_and_bool_integer_refused() -> None:
    item = fixture()
    del item["HostConfig"]["Devices"]
    with pytest.raises(PreflightError):
        require_offline_gateway(item, NAME, COMMAND)
    item = fixture()
    item["HostConfig"]["Memory"] = True
    with pytest.raises(PreflightError):
        require_offline_gateway(item, NAME, COMMAND)


def test_tmpfs_inspect_entry_and_image_exposed_but_unpublished_port_allowed() -> None:
    item = fixture()
    item["Mounts"] = [
        {
            "Type": "tmpfs",
            "Destination": "/tmp",
            "RW": True,
            "Source": "",
            "Mode": "",
            "Propagation": "",
        }
    ]
    item["NetworkSettings"]["Ports"] = {"11235/tcp": None}
    assert require_offline_gateway(item, NAME, COMMAND) == IDENTIFIER


def test_create_vector_exact_boundary_without_execution() -> None:
    assert create_arguments(NAME, COMMAND) == (
        "create",
        "--pull",
        "never",
        "--attach",
        "stdin",
        "--attach",
        "stdout",
        "--attach",
        "stderr",
        "--interactive",
        "--name",
        NAME,
        "--label",
        OWNER_LABEL + "=" + NAME,
        "--network",
        "none",
        "--read-only",
        "--user",
        "appuser",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--cpus",
        "0.5",
        "--pids-limit",
        "32",
        "--ipc",
        "none",
        "--cgroupns",
        "private",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=16m,mode=1777",
        "--restart",
        "no",
        "--log-driver",
        "none",
        "--no-healthcheck",
        "--entrypoint",
        "/usr/bin/env",
        "-i",
        IMAGE,
        *COMMAND,
    )


@pytest.mark.parametrize("name", ["foreign", "daemon-dns-offline-" + "a" * 23, NAME + "\n"])
def test_create_refuses_unowned_name(name: str) -> None:
    with pytest.raises(PreflightError):
        create_arguments(name, COMMAND)


@pytest.mark.parametrize(
    "command", [(), ("-i",), ("sh", "-c"), ("-i", "nul\x00"), ["-i", "python"]]
)
def test_create_refuses_malformed_trusted_vector(command: object) -> None:
    with pytest.raises(PreflightError):
        create_arguments(NAME, command)  # type: ignore[arg-type]
    with pytest.raises(PreflightError):
        require_offline_gateway(fixture(), NAME, command)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["Source", "Mode", "Propagation"])
def test_anomalous_tmpfs_metadata_refused(field: str) -> None:
    item = fixture()
    mount = {
        "Type": "tmpfs",
        "Destination": "/tmp",
        "RW": True,
        "Source": "",
        "Mode": "",
        "Propagation": "",
    }
    mount[field] = "foreign"
    item["Mounts"] = [mount]
    with pytest.raises(PreflightError):
        require_offline_gateway(item, NAME, COMMAND)


@pytest.mark.parametrize("section", ["Config", "HostConfig", "NetworkSettings"])
@pytest.mark.parametrize("value", [None, True, "record", []])
def test_malformed_sections_refused(section: str, value: object) -> None:
    item = fixture()
    item[section] = value
    with pytest.raises(PreflightError):
        require_offline_gateway(item, NAME, COMMAND)


def test_extra_network_and_malformed_ports_refused() -> None:
    item = fixture()
    item["NetworkSettings"]["Networks"]["bridge"] = {}
    with pytest.raises(PreflightError):
        require_offline_gateway(item, NAME, COMMAND)
    item = fixture()
    item["NetworkSettings"]["Ports"] = True
    with pytest.raises(PreflightError):
        require_offline_gateway(item, NAME, COMMAND)


PROFILE_PATH = Path(__file__).resolve().parents[1] / "scripts/web_fetch_pilot_browser_seccomp.json"
BROWSER = "daemon-browser-offline-" + "c" * 24


def pinned_profile() -> bytes:
    return PROFILE_PATH.read_bytes()


def browser_fixture() -> dict:
    item = fixture()
    item["Name"] = "/" + BROWSER
    item["Config"]["Labels"] = {OWNER_LABEL: BROWSER}
    host = item["HostConfig"]
    host["SecurityOpt"] = [browser_seccomp_option(pinned_profile()), "no-new-privileges"]
    host["Memory"] = host["MemorySwap"] = BROWSER_MEMORY
    host["NanoCpus"] = 1_000_000_000
    host["PidsLimit"] = 128
    host["ShmSize"] = BROWSER_SHM
    host["Tmpfs"] = copy.deepcopy(BROWSER_TMPFS)
    host["IpcMode"] = "private"
    return item


def test_pinned_browser_profile_bytes_and_stored_option() -> None:
    raw = pinned_profile()
    assert hashlib.sha256(raw).hexdigest() == BROWSER_SECCOMP_SHA256
    option = browser_seccomp_option(raw)
    assert option == "seccomp=" + json.dumps(json.loads(raw), separators=(",", ":"))
    assert json.loads(option[len("seccomp=") :])["defaultAction"] == "SCMP_ACT_ERRNO"
    for tampered in (raw + b" ", raw.replace(b"SCMP_ACT_ERRNO", b"SCMP_ACT_ALLOW", 1)):
        with pytest.raises(PreflightError):
            browser_seccomp_option(tampered)


def test_browser_create_vector_exact_boundary_without_execution() -> None:
    path = str(PROFILE_PATH)
    assert browser_create_arguments(BROWSER, COMMAND, path) == (
        "create",
        "--pull",
        "never",
        "--attach",
        "stdin",
        "--attach",
        "stdout",
        "--attach",
        "stderr",
        "--interactive",
        "--name",
        BROWSER,
        "--label",
        OWNER_LABEL + "=" + BROWSER,
        "--network",
        "none",
        "--read-only",
        "--user",
        "appuser",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "seccomp=" + path,
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "1g",
        "--memory-swap",
        "1g",
        "--cpus",
        "1",
        "--pids-limit",
        "128",
        "--shm-size",
        "256m",
        "--ipc",
        "private",
        "--cgroupns",
        "private",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
        "--restart",
        "no",
        "--log-driver",
        "none",
        "--no-healthcheck",
        "--entrypoint",
        "/usr/bin/env",
        "-i",
        IMAGE,
        *COMMAND,
    )
    for name in (NAME, "daemon-browser-offline-" + "c" * 23, BROWSER + "\n"):
        with pytest.raises(PreflightError):
            browser_create_arguments(name, COMMAND, path)
    for bad_path in ("relative.json", "/a/../b.json", "/a/b\x00.json"):
        with pytest.raises(PreflightError):
            browser_create_arguments(BROWSER, COMMAND, bad_path)
    with pytest.raises(PreflightError):
        browser_create_arguments(BROWSER, ("python",), path)


def test_approved_browser_record_and_identity_separation() -> None:
    option = browser_seccomp_option(pinned_profile())
    item = browser_fixture()
    assert (
        require_offline_browser(item, BROWSER, COMMAND, option, container_id=IDENTIFIER)
        == IDENTIFIER
    )
    assert require_browser_identity(item, BROWSER, container_id=IDENTIFIER) == IDENTIFIER
    with pytest.raises(PreflightError):
        require_identity(item, BROWSER)  # Gateway identity never accepts browser names.
    with pytest.raises(PreflightError):
        require_browser_identity(fixture(), NAME)  # And vice versa.
    with pytest.raises(PreflightError):
        require_offline_gateway(item, BROWSER, COMMAND)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("HostConfig", "SecurityOpt", ["no-new-privileges"]),
        ("HostConfig", "SecurityOpt", ["seccomp=unconfined", "no-new-privileges"]),
        ("HostConfig", "Memory", MEMORY),
        ("HostConfig", "MemorySwap", 2 * BROWSER_MEMORY),
        ("HostConfig", "NanoCpus", 2_000_000_000),
        ("HostConfig", "PidsLimit", 256),
        ("HostConfig", "ShmSize", 64 * 1024 * 1024),
        ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "IpcMode", "none"),
        ("HostConfig", "Tmpfs", copy.deepcopy(TMPFS)),
        ("HostConfig", "NetworkMode", "bridge"),
        ("HostConfig", "CgroupnsMode", "host"),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "Privileged", True),
        ("Config", "StdinOnce", False),
        ("Config", "User", "root"),
        ("", "Image", "sha256:" + "0" * 64),
    ],
)
def test_browser_refuses_boundary_mutations(section: str, key: str, value: object) -> None:
    item = browser_fixture()
    (item[section] if section else item)[key] = value
    with pytest.raises(PreflightError):
        require_offline_browser(item, BROWSER, COMMAND, browser_seccomp_option(pinned_profile()))


def test_browser_refuses_option_not_derived_from_pinned_profile() -> None:
    item = browser_fixture()
    with pytest.raises(PreflightError):
        require_offline_browser(item, BROWSER, COMMAND, "no-new-privileges")
    other = "seccomp=" + json.dumps({"defaultAction": "SCMP_ACT_ALLOW"}, separators=(",", ":"))
    item["HostConfig"]["SecurityOpt"] = [other, "no-new-privileges"]
    with pytest.raises(PreflightError):
        require_offline_browser(item, BROWSER, COMMAND, browser_seccomp_option(pinned_profile()))


RUN = "e" * 32


def test_run_label_follows_owner_label_and_identity_requires_matching_run() -> None:
    vector = create_arguments(NAME, COMMAND, run_token=RUN)
    owner = vector.index(OWNER_LABEL + "=" + NAME)
    assert vector[owner + 1 : owner + 3] == ("--label", RUN_LABEL + "=" + RUN)
    assert create_arguments(NAME, COMMAND) == vector[: owner + 1] + vector[owner + 3 :]
    path = str(PROFILE_PATH)
    browser = browser_create_arguments(BROWSER, COMMAND, path, run_token=RUN)
    assert ("--label", RUN_LABEL + "=" + RUN) == browser[
        browser.index(OWNER_LABEL + "=" + BROWSER) + 1 : browser.index(OWNER_LABEL + "=" + BROWSER)
        + 3
    ]
    for bad in ("E" * 32, "e" * 31, "e" * 33, 7):
        with pytest.raises(PreflightError):
            create_arguments(NAME, COMMAND, run_token=bad)  # type: ignore[arg-type]
    item = fixture()
    item["Config"]["Labels"][RUN_LABEL] = RUN
    assert require_identity(item, NAME, run_token=RUN) == IDENTIFIER
    assert require_offline_gateway(item, NAME, COMMAND, run_token=RUN) == IDENTIFIER
    with pytest.raises(PreflightError):
        require_identity(item, NAME, run_token="f" * 32)
    del item["Config"]["Labels"][RUN_LABEL]
    with pytest.raises(PreflightError):
        require_identity(item, NAME, run_token=RUN)
    browser_item = browser_fixture()
    browser_item["Config"]["Labels"][RUN_LABEL] = RUN
    option = browser_seccomp_option(pinned_profile())
    assert require_offline_browser(browser_item, BROWSER, COMMAND, option, run_token=RUN)
    with pytest.raises(PreflightError):
        require_browser_identity(browser_item, BROWSER, run_token="f" * 32)
