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
    C4AI_IMAGE,
    C4AI_IMAGE_ENV,
    C4AI_MEMORY,
    C4AI_SANDBOXED_CONFIG_SHA256,
    C4AI_TMPFS,
    EGRESS_OPTIONS,
    EGRESS_SUBNET,
    IMAGE,
    MEMORY,
    NETWORK_OPTIONS,
    NETWORK_SUBNET,
    OWNER_LABEL,
    TMPFS,
    RUN_LABEL,
    PreflightError,
    browser_create_arguments,
    browser_seccomp_option,
    c4ai_create_arguments,
    c4ai_sandboxed_config,
    create_arguments,
    network_create_arguments,
    networked_create_arguments,
    require_browser_identity,
    require_c4ai,
    require_c4ai_identity,
    require_identity,
    require_network_identity,
    require_networked,
    require_networked_identity,
    require_offline_browser,
    require_offline_gateway,
    require_offline_network,
    require_owned_network,
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


NET = "daemon-net-offline-" + "1" * 24
NET_ID = "9" * 64


def network_fixture() -> dict:
    return {
        "Id": NET_ID,
        "Name": NET,
        "Driver": "bridge",
        "Scope": "local",
        "Internal": True,
        "EnableIPv6": False,
        "Attachable": False,
        "Ingress": False,
        "ConfigOnly": False,
        "Options": dict(NETWORK_OPTIONS),
        "IPAM": {"Driver": "default", "Config": [{"Subnet": NETWORK_SUBNET, "Gateway": "1.2.3.1"}]},
        "Labels": {OWNER_LABEL: NET, RUN_LABEL: RUN},
        "Containers": {},
    }


def test_network_vector_exact_and_refusals() -> None:
    assert network_create_arguments(NET, RUN) == (
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        "--ipv6=false",
        "--subnet",
        "1.2.3.0/29",
        "--opt",
        "com.docker.network.bridge.gateway_mode_ipv4=isolated",
        "--opt",
        "com.docker.network.bridge.enable_ip_masquerade=false",
        "--label",
        OWNER_LABEL + "=" + NET,
        "--label",
        RUN_LABEL + "=" + RUN,
        NET,
    )
    for name, run in ((NAME, RUN), (NET + "x", RUN), (NET, None), (NET, "E" * 32)):
        with pytest.raises(PreflightError):
            network_create_arguments(name, run)  # type: ignore[arg-type]


def test_offline_network_record_and_identity() -> None:
    assert require_offline_network(network_fixture(), NET, RUN, network_id=NET_ID) == NET_ID
    no_gateway = network_fixture()
    del no_gateway["IPAM"]["Config"][0]["Gateway"]
    assert require_offline_network(no_gateway, NET, RUN) == NET_ID
    with pytest.raises(PreflightError):
        require_network_identity(network_fixture(), NET, "f" * 32)
    with pytest.raises(PreflightError):
        require_network_identity(network_fixture(), NET, RUN, network_id="8" * 64)


@pytest.mark.parametrize(
    "key,value",
    [
        ("Internal", False),
        ("EnableIPv6", True),
        ("Driver", "overlay"),
        ("Scope", "swarm"),
        ("Attachable", True),
        ("Ingress", True),
        ("Name", "daemon-net-offline-" + "2" * 24),
        ("Options", {"com.docker.network.bridge.enable_ip_masquerade": "false"}),
        ("Options", {**NETWORK_OPTIONS, "com.docker.network.bridge.enable_icc": "true"}),
        ("IPAM", {"Driver": "default", "Config": [{"Subnet": "10.0.0.0/29"}]}),
        (
            "IPAM",
            {"Driver": "default", "Config": [{"Subnet": NETWORK_SUBNET, "Gateway": "1.2.3.2"}]},
        ),
        (
            "IPAM",
            {"Driver": "default", "Config": [{"Subnet": NETWORK_SUBNET}, {"Subnet": "1.2.3.8/29"}]},
        ),
        ("Labels", {OWNER_LABEL: NET}),
    ],
)
def test_offline_network_refuses_mutations(key: str, value: object) -> None:
    record = network_fixture()
    record[key] = value
    with pytest.raises(PreflightError):
        require_offline_network(record, NET, RUN)


FIXTURE_NAME = "daemon-fixture-" + "3" * 24


def networked_fixture(*, alias: str | None = "openai.com", started: bool = False) -> dict:
    item = fixture()
    item["Name"] = "/" + FIXTURE_NAME
    item["Config"]["Labels"] = {OWNER_LABEL: FIXTURE_NAME, RUN_LABEL: RUN}
    item["HostConfig"]["NetworkMode"] = NET
    item["NetworkSettings"]["Networks"] = {
        NET: {
            "NetworkID": NET_ID if started else "",
            "IPAddress": "1.2.3.2" if started else "",
            "GlobalIPv6Address": "",
            "Aliases": None if alias is None else [alias],
        }
    }
    return item


def test_networked_vector_replaces_only_the_network_attachment() -> None:
    vector = networked_create_arguments(
        FIXTURE_NAME, COMMAND, NET, run_token=RUN, alias="openai.com"
    )
    gateway = create_arguments(NAME, COMMAND, run_token=RUN)
    at = gateway.index("--network")
    assert vector[at : at + 4] == ("--network", NET, "--network-alias", "openai.com")
    assert vector[at + 4 :] == gateway[at + 2 :]
    plain = networked_create_arguments(FIXTURE_NAME, COMMAND, NET, run_token=RUN)
    assert plain[at : at + 2] == ("--network", NET) and "--network-alias" not in plain
    for kwargs in (
        {"alias": "OpenAI.com"},
        {"alias": "-bad.example"},
        {"alias": "openai.com\n"},
        {"alias": "localhost"},
    ):
        with pytest.raises(PreflightError):
            networked_create_arguments(FIXTURE_NAME, COMMAND, NET, run_token=RUN, **kwargs)
    for name, network in ((NAME, NET), (FIXTURE_NAME, "none"), (FIXTURE_NAME, "bridge")):
        with pytest.raises(PreflightError):
            networked_create_arguments(name, COMMAND, network, run_token=RUN)
    with pytest.raises(PreflightError):
        networked_create_arguments(FIXTURE_NAME, COMMAND, NET, run_token=None)  # type: ignore[arg-type]


def test_networked_record_before_and_after_start() -> None:
    for started in (False, True):
        item = networked_fixture(started=started)
        assert (
            require_networked(
                item,
                FIXTURE_NAME,
                COMMAND,
                NET,
                run_token=RUN,
                network_id=NET_ID,
                alias="openai.com",
            )
            == IDENTIFIER
        )
    plain = networked_fixture(alias=None)
    assert require_networked(plain, FIXTURE_NAME, COMMAND, NET, run_token=RUN, network_id=NET_ID)
    assert require_networked_identity(plain, FIXTURE_NAME, run_token=RUN) == IDENTIFIER
    with pytest.raises(PreflightError):
        require_identity(plain, FIXTURE_NAME)  # Role names never cross.


@pytest.mark.parametrize(
    "mutate",
    [
        lambda i: i["HostConfig"].__setitem__("NetworkMode", "none"),
        lambda i: i["HostConfig"].__setitem__("Memory", 2 * MEMORY),
        lambda i: i["HostConfig"].__setitem__("ExtraHosts", ["openai.com:1.2.3.2"]),
        lambda i: i["NetworkSettings"]["Networks"].__setitem__("bridge", {}),
        lambda i: i["NetworkSettings"]["Networks"][NET].__setitem__("IPAddress", "10.0.0.2"),
        lambda i: i["NetworkSettings"]["Networks"][NET].__setitem__("IPAddress", "bogus"),
        lambda i: i["NetworkSettings"]["Networks"][NET].__setitem__("IPAddress", None),
        lambda i: i["NetworkSettings"]["Networks"][NET].__setitem__(
            "GlobalIPv6Address", "2001:db8::2"
        ),
        lambda i: i["NetworkSettings"]["Networks"][NET].__setitem__("NetworkID", "8" * 64),
        lambda i: i["NetworkSettings"]["Networks"][NET].__setitem__("Aliases", ["openai.com", "x"]),
        lambda i: i["NetworkSettings"]["Networks"][NET].__setitem__("Aliases", ["other.com"]),
        lambda i: i["Config"]["Labels"].__setitem__(RUN_LABEL, "f" * 32),
    ],
)
def test_networked_record_refuses_mutations(mutate: object) -> None:
    item = networked_fixture(started=True)
    mutate(item)  # type: ignore[operator]
    with pytest.raises(PreflightError):
        require_networked(
            item, FIXTURE_NAME, COMMAND, NET, run_token=RUN, network_id=NET_ID, alias="openai.com"
        )


EGRESS_NET = "daemon-net-egress-" + "7" * 24


def egress_network_fixture() -> dict:
    item = network_fixture()
    item.update(
        Name=EGRESS_NET,
        Internal=False,
        Options=dict(EGRESS_OPTIONS),
        IPAM={
            "Driver": "default",
            "Config": [{"Subnet": EGRESS_SUBNET, "Gateway": "10.251.248.1"}],
        },
        Labels={OWNER_LABEL: EGRESS_NET, RUN_LABEL: RUN},
    )
    return item


def test_egress_network_vector_has_no_internal_flag_and_fixed_options() -> None:
    assert network_create_arguments(EGRESS_NET, RUN) == (
        "network",
        "create",
        "--driver",
        "bridge",
        "--ipv6=false",
        "--subnet",
        "10.251.248.0/29",
        "--opt",
        "com.docker.network.bridge.enable_icc=false",
        "--opt",
        "com.docker.network.bridge.enable_ip_masquerade=true",
        "--label",
        OWNER_LABEL + "=" + EGRESS_NET,
        "--label",
        RUN_LABEL + "=" + RUN,
        EGRESS_NET,
    )
    assert "--internal" in network_create_arguments(NET, RUN)  # Internal kind unchanged.


def test_egress_network_preflight_and_kind_separation() -> None:
    assert require_owned_network(egress_network_fixture(), EGRESS_NET, RUN) == NET_ID
    assert require_owned_network(network_fixture(), NET, RUN) == NET_ID
    with pytest.raises(PreflightError):
        require_offline_network(egress_network_fixture(), EGRESS_NET, RUN)  # Internal only.
    crossed = egress_network_fixture()
    crossed["Internal"] = True  # An egress name must never carry internal config, or vice versa.
    with pytest.raises(PreflightError):
        require_owned_network(crossed, EGRESS_NET, RUN)
    for key, value in (
        ("Options", dict(NETWORK_OPTIONS)),
        ("Options", {**EGRESS_OPTIONS, "com.docker.network.bridge.enable_icc": "true"}),
        ("EnableIPv6", True),
        ("IPAM", {"Driver": "default", "Config": [{"Subnet": NETWORK_SUBNET}]}),
        (
            "IPAM",
            {"Driver": "default", "Config": [{"Subnet": EGRESS_SUBNET, "Gateway": "1.2.3.1"}]},
        ),
    ):
        record = egress_network_fixture()
        record[key] = value
        with pytest.raises(PreflightError):
            require_owned_network(record, EGRESS_NET, RUN)


def test_networked_role_is_held_to_its_network_kinds_subnet() -> None:
    item = networked_fixture(alias=None, started=True)
    item["HostConfig"]["NetworkMode"] = EGRESS_NET
    endpoint = item["NetworkSettings"]["Networks"].pop(NET)
    endpoint["IPAddress"] = "10.251.248.2"
    item["NetworkSettings"]["Networks"][EGRESS_NET] = endpoint
    gateway_name = "daemon-gateway-" + "8" * 24
    item["Name"] = "/" + gateway_name
    item["Config"]["Labels"] = {OWNER_LABEL: gateway_name, RUN_LABEL: RUN}
    assert require_networked(
        item, gateway_name, COMMAND, EGRESS_NET, run_token=RUN, network_id=NET_ID
    )
    endpoint["IPAddress"] = "1.2.3.2"  # The internal subnet is not this network's.
    with pytest.raises(PreflightError):
        require_networked(item, gateway_name, COMMAND, EGRESS_NET, run_token=RUN, network_id=NET_ID)
    vector = networked_create_arguments(gateway_name, COMMAND, EGRESS_NET, run_token=RUN)
    assert ("--network", EGRESS_NET) == vector[
        vector.index("--network") : vector.index("--network") + 2
    ]


C4AI = "daemon-c4ai-" + "b" * 24
API = "c" * 64
ALIAS = "reader.pilot.test"
OVERRIDE_PATH = (
    Path(__file__).resolve().parents[1] / "scripts/web_fetch_pilot_c4ai_config_sandboxed.yml"
)


def c4ai_fixture(*, sandboxed: bool = False) -> dict:
    env = [f"{name}=x" for name in sorted(C4AI_IMAGE_ENV)] + ["CRAWL4AI_API_TOKEN=" + API]
    host = {
        "NetworkMode": NET,
        "ReadonlyRootfs": True,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "SecurityOpt": (
            [browser_seccomp_option(pinned_profile()), "no-new-privileges"]
            if sandboxed
            else ["no-new-privileges"]
        ),
        "Memory": C4AI_MEMORY,
        "MemorySwap": C4AI_MEMORY,
        "NanoCpus": 2_000_000_000,
        "PidsLimit": 256,
        "ShmSize": 256 * 1024 * 1024,
        "Tmpfs": dict(C4AI_TMPFS),
        "IpcMode": "private",
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
    }
    mounts: list[dict[str, object]] = [
        {"Type": "tmpfs", "Destination": target} for target in C4AI_TMPFS
    ]
    if sandboxed:
        mounts.append(
            {
                "Type": "bind",
                "Source": str(OVERRIDE_PATH),
                "Destination": "/app/config.yml",
                "RW": False,
            }
        )
    return {
        "Id": IDENTIFIER,
        "Name": "/" + C4AI,
        "Image": C4AI_IMAGE,
        "Config": {
            "User": "appuser",
            "Labels": {OWNER_LABEL: C4AI, RUN_LABEL: RUN},
            "Entrypoint": None,
            "Cmd": ["bash", "entrypoint.sh"],
            "WorkingDir": "/app",
            "Env": env,
            "Volumes": None,
            "Tty": False,
            "OpenStdin": True,
            "StdinOnce": True,
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Healthcheck": {"Test": ["NONE"]},
        },
        "HostConfig": host,
        "Mounts": mounts,
        "NetworkSettings": {
            "Networks": {
                NET: {"NetworkID": "", "IPAddress": "", "GlobalIPv6Address": "", "Aliases": [ALIAS]}
            },
            "Ports": {},
        },
    }


def check_c4ai(item: dict, *, sandboxed: bool = False) -> str:
    return require_c4ai(
        item,
        C4AI,
        NET,
        ALIAS,
        run_token=RUN,
        network_id=NET_ID,
        api_token=API,
        sandboxed=sandboxed,
        seccomp_option=browser_seccomp_option(pinned_profile()) if sandboxed else None,
        config_path=str(OVERRIDE_PATH) if sandboxed else None,
    )


def test_c4ai_override_removes_exactly_the_no_sandbox_line() -> None:
    override = OVERRIDE_PATH.read_bytes()
    assert hashlib.sha256(override).hexdigest() == C4AI_SANDBOXED_CONFIG_SHA256
    assert b'- "--no-sandbox"' not in override and b"--disable-dev-shm-usage" in override
    with pytest.raises(PreflightError):
        c4ai_sandboxed_config(override)  # Not the shipped bytes.


def test_c4ai_vectors_shipped_and_sandboxed() -> None:
    shipped = c4ai_create_arguments(C4AI, NET, ALIAS, run_token=RUN, api_token=API, sandboxed=False)
    assert shipped[-1] == C4AI_IMAGE and "--entrypoint" not in shipped and "--mount" not in shipped
    assert ("--network", NET, "--network-alias", ALIAS) == shipped[
        shipped.index("--network") : shipped.index("--network") + 4
    ]
    assert (
        "CRAWL4AI_API_TOKEN=" + API in shipped
        and "-p" not in shipped
        and "--publish" not in shipped
    )
    sandboxed = c4ai_create_arguments(
        C4AI,
        NET,
        ALIAS,
        run_token=RUN,
        api_token=API,
        sandboxed=True,
        profile_path=str(PROFILE_PATH),
        config_path=str(OVERRIDE_PATH),
    )
    assert "seccomp=" + str(PROFILE_PATH) in sandboxed
    assert f"type=bind,source={OVERRIDE_PATH},target=/app/config.yml,readonly" in sandboxed
    for kwargs in (
        {"api_token": "short"},
        {"sandboxed": False, "profile_path": str(PROFILE_PATH)},
        {"sandboxed": True, "profile_path": "relative", "config_path": str(OVERRIDE_PATH)},
        {"sandboxed": True, "profile_path": str(PROFILE_PATH), "config_path": "/a,b"},
    ):
        base = {"run_token": RUN, "api_token": API, "sandboxed": False}
        with pytest.raises(PreflightError):
            c4ai_create_arguments(C4AI, NET, ALIAS, **{**base, **kwargs})  # type: ignore[arg-type]
    with pytest.raises(PreflightError):
        c4ai_create_arguments(BROWSER, NET, ALIAS, run_token=RUN, api_token=API, sandboxed=False)


def test_c4ai_preflight_accepts_both_modes_and_refuses_drift() -> None:
    assert check_c4ai(c4ai_fixture()) == IDENTIFIER
    assert check_c4ai(c4ai_fixture(sandboxed=True), sandboxed=True) == IDENTIFIER
    with pytest.raises(PreflightError):
        check_c4ai(c4ai_fixture(sandboxed=True))  # A bind mount in shipped mode.
    with pytest.raises(PreflightError):
        check_c4ai(c4ai_fixture(), sandboxed=True)  # Sandboxed mode needs its seccomp and bind.
    mutations = [
        lambda i: i["HostConfig"].__setitem__("ReadonlyRootfs", False),
        lambda i: i["HostConfig"].__setitem__("Memory", 4 * C4AI_MEMORY),
        lambda i: i["HostConfig"].__setitem__("PidsLimit", 1024),
        lambda i: i["HostConfig"].__setitem__("CapAdd", ["SYS_ADMIN"]),
        lambda i: i["HostConfig"].__setitem__(
            "PortBindings", {"11235/tcp": [{"HostPort": "11235"}]}
        ),
        lambda i: i["HostConfig"].__setitem__("Binds", ["/:/host"]),
        lambda i: i["Config"].__setitem__("Cmd", ["bash", "-c", "id"]),
        lambda i: i["Config"].__setitem__("Entrypoint", ["/bin/sh"]),
        lambda i: i["Config"]["Env"].append("CRAWL4AI_HOOKS_ENABLED=true"),
        lambda i: i["Config"]["Env"].__setitem__(-1, "CRAWL4AI_API_TOKEN=" + "d" * 64),
        lambda i: i.__setitem__("Image", IMAGE),
        lambda i: i["NetworkSettings"]["Networks"][NET].__setitem__("Aliases", ["openai.com"]),
        lambda i: i["Mounts"].append(
            {"Type": "bind", "Source": "/etc", "Destination": "/x", "RW": True}
        ),
    ]
    for mutate in mutations:
        item = c4ai_fixture()
        mutate(item)
        with pytest.raises(PreflightError):
            check_c4ai(item)
    assert require_c4ai_identity(c4ai_fixture(), C4AI, run_token=RUN) == IDENTIFIER
    with pytest.raises(PreflightError):
        require_c4ai_identity(c4ai_fixture(), C4AI, run_token="f" * 32)


def test_client_role_name_is_networked() -> None:
    name = "daemon-client-" + "1" * 24
    assert networked_create_arguments(name, COMMAND, NET, run_token=RUN)[11] == name
