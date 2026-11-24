"""Pure offline gateway/browser preflight; not a launcher or containment authority.

Only trusted supervisor values and bounded, parsed Docker inspect records belong
here. A match never proves actual namespace/cgroup enforcement or child cleanup.
No Docker, filesystem, process, network or import-time I/O is performed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

IMAGE = "sha256:4d8b065bf185962733cb5f9701f4122d03383fa1ab6b5f6a9873f04fa0416a84"
OWNER_LABEL = "daemon.reader-pilot.owner"
# Per-run scope: a fresh 128-bit token stamped on every resource a run creates.
RUN_LABEL = "daemon.reader-pilot.run"
_RUN = re.compile(r"[0-9a-f]{32}\Z")
MEMORY = 128 * 1024 * 1024
TMPFS = {"/tmp": "rw,nosuid,nodev,size=16m,mode=1777"}
_NAME = re.compile(r"daemon-dns-offline-[0-9a-f]{24}\Z")
BROWSER_NAME = re.compile(r"daemon-browser-offline-[0-9a-f]{24}\Z")
BROWSER_MEMORY = 1024 * 1024 * 1024
BROWSER_SHM = 256 * 1024 * 1024
BROWSER_TMPFS = {"/tmp": "rw,nosuid,nodev,size=128m,mode=1777"}
# Qualified offline browser profile (pinned moby default plus exact clone flags),
# stored byte-for-byte at scripts/web_fetch_pilot_browser_seccomp.json.
BROWSER_SECCOMP_SHA256 = "ec97bb9f172a136a19a3af5eb0f6ed1236476e015e6c2966196f4a85bd9f1915"
_ID = re.compile(r"[0-9a-f]{64}\Z")


class PreflightError(ValueError):
    """Fixed-message refusal; never include raw configuration or environment."""


def _require(condition: bool) -> None:
    if not condition:
        raise PreflightError("offline gateway preflight refused")


def _map(value: object) -> Mapping[str, object]:
    _require(type(value) is dict)
    return value  # type: ignore[return-value]


def _equal(record: Mapping[str, object], key: str, expected: object) -> None:
    value = record.get(key)
    _require(type(value) is type(expected) and value == expected)


def _empty(record: Mapping[str, object], key: str) -> None:
    _require(key in record)
    value = record[key]
    _require(value is None or type(value) in (list, dict) and not value)


def _run_label(run_token: str | None) -> tuple[str, ...]:
    if run_token is None:
        return ()
    _require(type(run_token) is str and _RUN.fullmatch(run_token) is not None)
    return ("--label", RUN_LABEL + "=" + run_token)


def create_arguments(
    name: str, command: tuple[str, ...], *, run_token: str | None = None
) -> tuple[str, ...]:
    """Pure fixed Docker subcommand vector; does not execute or select a daemon.

    Only a supervisor-reviewed bootstrap may supply command. Its first argument
    is env's -i, not a shell fragment. The future command runner must explicitly
    target the qualified local daemon and scrub its own host environment.
    """
    _require(type(name) is str and _NAME.fullmatch(name) is not None)
    return _gateway_vector(name, command, run_token, ("--network", "none"))


def _gateway_vector(
    name: str, command: tuple[str, ...], run_token: str | None, network: tuple[str, ...]
) -> tuple[str, ...]:
    _require(type(command) is tuple and len(command) > 1 and command[0] == "-i")
    _require(all(type(arg) is str and "\x00" not in arg for arg in command))
    return (
        "create",
        "--pull",
        "never",
        "--attach",
        "stdin",
        "--attach",
        "stdout",
        "--attach",
        "stderr",
        "--interactive",  # OpenStdin: the bootstrap reads its payload from stdin.
        "--name",
        name,
        "--label",
        OWNER_LABEL + "=" + name,
        *_run_label(run_token),
        *network,
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
        "/tmp:" + TMPFS["/tmp"],
        "--restart",
        "no",
        "--log-driver",
        "none",
        "--no-healthcheck",
        "--entrypoint",
        "/usr/bin/env",
        "-i",
        IMAGE,
        *command,
    )


def require_identity(
    record: object,
    name: str,
    *,
    container_id: str | None = None,
    run_token: str | None = None,
) -> str:
    """Minimal authority check for cleanup, independent of configuration success.

    Never delete by a child-supplied ID/name. The owning launcher must choose a
    fresh unpredictable name and retain the returned full ID. This function only
    checks a supplied record; discovery, local-daemon provenance and removal
    observation remain the launcher's responsibility.
    """
    return _require_identity(record, name, _NAME, container_id, run_token)


def _require_identity(
    record: object,
    name: str,
    pattern: re.Pattern[str],
    container_id: str | None,
    run_token: str | None,
) -> str:
    _require(type(name) is str and pattern.fullmatch(name) is not None)
    item = _map(record)
    identifier = item.get("Id")
    _require(type(identifier) is str and _ID.fullmatch(identifier) is not None)
    if container_id is not None:
        _require(type(container_id) is str and identifier == container_id)
    _equal(item, "Name", "/" + name)
    _equal(item, "Image", IMAGE)
    config = _map(item.get("Config"))
    labels = _map(config.get("Labels"))
    _equal(labels, OWNER_LABEL, name)
    if run_token is not None:
        _require(type(run_token) is str and _RUN.fullmatch(run_token) is not None)
        _equal(labels, RUN_LABEL, run_token)
    return identifier  # type: ignore[return-value]


def require_offline_gateway(
    record: object,
    name: str,
    command: tuple[str, ...],
    *,
    container_id: str | None = None,
    run_token: str | None = None,
) -> str:
    """Check the offline allocation before start and after execution.

    command is an immutable TRUSTED supervisor vector, not a record-derived
    expectation. Its bootstrap must separately receive source review. Inherited
    image Config.Env is not persisted here; /usr/bin/env -i scrubs runtime env.
    IPC must be disabled: this DNS-only fixture needs no /dev/shm allocation.
    """
    identifier = require_identity(record, name, container_id=container_id, run_token=run_token)
    item = _require_gateway_limits(record, command, "none")
    network = _map(item.get("NetworkSettings"))
    networks = _map(network.get("Networks"))
    _require(set(networks) == {"none"})
    none = _map(networks["none"])
    _equal(none, "IPAddress", "")
    _equal(none, "GlobalIPv6Address", "")
    _require_no_ports(network)
    return identifier


def _require_no_ports(network: Mapping[str, object]) -> None:
    ports = network.get("Ports")
    _require(ports is None or type(ports) is dict and all(v is None for v in ports.values()))


def _require_gateway_limits(
    record: object, command: tuple[str, ...], network_mode: str
) -> Mapping[str, object]:
    """Every gateway-role Config/HostConfig/mount limit except the network attachment."""
    _require(type(command) is tuple and len(command) > 1 and command[0] == "-i")
    _require(all(type(arg) is str and "\x00" not in arg for arg in command))
    item = _map(record)
    config, host = _map(item.get("Config")), _map(item.get("HostConfig"))
    for key, value in (
        ("User", "appuser"),
        ("Entrypoint", ["/usr/bin/env"]),
        ("Cmd", list(command)),
        ("Tty", False),
        ("OpenStdin", True),
        ("StdinOnce", True),  # Daemon closes stdin when the attach client ends it.
        ("AttachStdin", True),
        ("AttachStdout", True),
        ("AttachStderr", True),
    ):
        _equal(config, key, value)
    _empty(config, "Volumes")
    _equal(_map(config.get("Healthcheck")), "Test", ["NONE"])
    for key, value in (
        ("NetworkMode", network_mode),
        ("ReadonlyRootfs", True),
        ("Privileged", False),
        ("CapDrop", ["ALL"]),
        ("SecurityOpt", ["no-new-privileges"]),
        ("Memory", MEMORY),
        ("MemorySwap", MEMORY),
        ("NanoCpus", 500_000_000),
        ("PidsLimit", 32),
        ("Tmpfs", TMPFS),
        ("IpcMode", "none"),
        ("PidMode", ""),
        ("UTSMode", ""),
        ("UsernsMode", ""),
        ("CgroupnsMode", "private"),
        ("PublishAllPorts", False),
        ("AutoRemove", False),
    ):
        _equal(host, key, value)
    for key in (
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
    ):
        _empty(host, key)
    restart = _map(host.get("RestartPolicy"))
    _equal(restart, "Name", "no")
    _equal(restart, "MaximumRetryCount", 0)
    log = _map(host.get("LogConfig"))
    _equal(log, "Type", "none")
    _equal(log, "Config", {})
    mounts = item.get("Mounts")
    _require(type(mounts) is list)
    for mount in mounts:  # type: ignore[union-attr]
        fields = _map(mount)
        _equal(fields, "Type", "tmpfs")
        _equal(fields, "Destination", "/tmp")
        _equal(fields, "RW", True)
        _equal(fields, "Source", "")
        _equal(fields, "Mode", "")
        _equal(fields, "Propagation", "")
    _require(len(mounts) <= 1)  # type: ignore[arg-type]
    return item


def browser_seccomp_option(profile: bytes) -> str:
    """Pure: the exact ``SecurityOpt`` entry Docker stores for the pinned profile.

    The CLI inlines the profile file as compact JSON (key order preserved), so
    the record must equal ``seccomp=`` plus this compaction of the pinned bytes.
    """
    _require(type(profile) is bytes)
    _require(hashlib.sha256(profile).hexdigest() == BROWSER_SECCOMP_SHA256)
    return "seccomp=" + json.dumps(json.loads(profile), separators=(",", ":"))


def browser_create_arguments(
    name: str, command: tuple[str, ...], profile_path: str, *, run_token: str | None = None
) -> tuple[str, ...]:
    """Pure fixed browser create vector; the caller verifies the profile bytes.

    ``profile_path`` must be the absolute path whose bytes the caller has just
    hash-checked with :func:`browser_seccomp_option`; the CLI reads it at create.
    """
    _require(type(name) is str and BROWSER_NAME.fullmatch(name) is not None)
    _require(type(command) is tuple and len(command) > 1 and command[0] == "-i")
    _require(all(type(arg) is str and "\x00" not in arg for arg in command))
    _require(type(profile_path) is str and os.path.isabs(profile_path))
    _require("\x00" not in profile_path and os.path.normpath(profile_path) == profile_path)
    return (
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
        name,
        "--label",
        OWNER_LABEL + "=" + name,
        *_run_label(run_token),
        "--network",
        "none",
        "--read-only",
        "--user",
        "appuser",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "seccomp=" + profile_path,
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
        "/tmp:" + BROWSER_TMPFS["/tmp"],
        "--restart",
        "no",
        "--log-driver",
        "none",
        "--no-healthcheck",
        "--entrypoint",
        "/usr/bin/env",
        "-i",
        IMAGE,
        *command,
    )


def require_browser_identity(
    record: object,
    name: str,
    *,
    container_id: str | None = None,
    run_token: str | None = None,
) -> str:
    """Browser-name identity check for cleanup, independent of configuration."""
    return _require_identity(record, name, BROWSER_NAME, container_id, run_token)


def require_offline_browser(
    record: object,
    name: str,
    command: tuple[str, ...],
    seccomp_option: str,
    *,
    container_id: str | None = None,
    run_token: str | None = None,
) -> str:
    """Check the offline browser allocation before start and after execution.

    ``seccomp_option`` comes from :func:`browser_seccomp_option` over the pinned
    bytes, never from the record. Matching never proves runtime enforcement.
    """
    identifier = require_browser_identity(
        record, name, container_id=container_id, run_token=run_token
    )
    _require(type(command) is tuple and len(command) > 1 and command[0] == "-i")
    _require(type(seccomp_option) is str and seccomp_option.startswith("seccomp={"))
    item = _map(record)
    config, host = _map(item.get("Config")), _map(item.get("HostConfig"))
    for key, value in (
        ("User", "appuser"),
        ("Entrypoint", ["/usr/bin/env"]),
        ("Cmd", list(command)),
        ("Tty", False),
        ("OpenStdin", True),
        ("StdinOnce", True),
        ("AttachStdin", True),
        ("AttachStdout", True),
        ("AttachStderr", True),
    ):
        _equal(config, key, value)
    _empty(config, "Volumes")
    _equal(_map(config.get("Healthcheck")), "Test", ["NONE"])
    for key, value in (
        ("NetworkMode", "none"),
        ("ReadonlyRootfs", True),
        ("Privileged", False),
        ("CapDrop", ["ALL"]),
        ("SecurityOpt", [seccomp_option, "no-new-privileges"]),
        ("Memory", BROWSER_MEMORY),
        ("MemorySwap", BROWSER_MEMORY),
        ("NanoCpus", 1_000_000_000),
        ("PidsLimit", 128),
        ("ShmSize", BROWSER_SHM),
        ("Tmpfs", BROWSER_TMPFS),
        ("IpcMode", "private"),
        ("PidMode", ""),
        ("UTSMode", ""),
        ("UsernsMode", ""),
        ("CgroupnsMode", "private"),
        ("PublishAllPorts", False),
        ("AutoRemove", False),
    ):
        _equal(host, key, value)
    for key in (
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
    ):
        _empty(host, key)
    restart = _map(host.get("RestartPolicy"))
    _equal(restart, "Name", "no")
    _equal(restart, "MaximumRetryCount", 0)
    log = _map(host.get("LogConfig"))
    _equal(log, "Type", "none")
    _equal(log, "Config", {})
    mounts = item.get("Mounts")
    _require(type(mounts) is list)
    for mount in mounts:  # type: ignore[union-attr]
        fields = _map(mount)
        _equal(fields, "Type", "tmpfs")
        _equal(fields, "Destination", "/tmp")
        _equal(fields, "RW", True)
        _equal(fields, "Source", "")
        _equal(fields, "Mode", "")
        _equal(fields, "Propagation", "")
    _require(len(mounts) <= 1)  # type: ignore[arg-type]
    network = _map(item.get("NetworkSettings"))
    networks = _map(network.get("Networks"))
    _require(set(networks) == {"none"})
    none = _map(networks["none"])
    _equal(none, "IPAddress", "")
    _equal(none, "GlobalIPv6Address", "")
    ports = network.get("Ports")
    _require(ports is None or type(ports) is dict and all(v is None for v in ports.values()))
    return identifier


NETWORK_NAME = re.compile(r"daemon-net-offline-[0-9a-f]{24}\Z")
NETWORK_SUBNET = "1.2.3.0/29"  # Global unicast (APNIC research range), never routed here.
NETWORK_OPTIONS = {
    # No host-side bridge address, so the host gains no route to the subnet.
    "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
    "com.docker.network.bridge.enable_ip_masquerade": "false",
}


EGRESS_NETWORK_NAME = re.compile(r"daemon-net-egress-[0-9a-f]{24}\Z")
EGRESS_SUBNET = "10.251.248.0/29"  # Private; unused by local Docker networks and host routes.
EGRESS_OPTIONS = {
    # The gateway is the only member; no container-to-container traffic at all.
    "com.docker.network.bridge.enable_icc": "false",
    "com.docker.network.bridge.enable_ip_masquerade": "true",
}


@dataclass(frozen=True)
class _NetworkKind:
    pattern: re.Pattern[str]
    subnet: str
    gateway: str
    options: Mapping[str, str]
    internal: bool


_KINDS = (
    _NetworkKind(NETWORK_NAME, NETWORK_SUBNET, "1.2.3.1", NETWORK_OPTIONS, True),
    _NetworkKind(EGRESS_NETWORK_NAME, EGRESS_SUBNET, "10.251.248.1", EGRESS_OPTIONS, False),
)


def _kind_for(name: object) -> _NetworkKind:
    _require(type(name) is str)
    for kind in _KINDS:
        if kind.pattern.fullmatch(name) is not None:  # type: ignore[arg-type]
            return kind
    raise PreflightError("offline gateway preflight refused")


def network_create_arguments(name: str, run_token: str) -> tuple[str, ...]:
    """Pure fixed vector for one disposable IPv4-only bridge network of a known kind.

    ``daemon-net-offline-*`` is internal (no route out); ``daemon-net-egress-*`` has a
    route out and exists only for a separately approved live session.
    """
    kind = _kind_for(name)
    run = _run_label(run_token)
    _require(bool(run))
    options: list[str] = []
    for key, value in kind.options.items():
        options.extend(("--opt", key + "=" + value))
    return (
        "network",
        "create",
        "--driver",
        "bridge",
        *(("--internal",) if kind.internal else ()),
        "--ipv6=false",
        "--subnet",
        kind.subnet,
        *options,
        "--label",
        OWNER_LABEL + "=" + name,
        *run,
        name,
    )


def require_network_identity(
    record: object, name: str, run_token: str, *, network_id: str | None = None
) -> str:
    """Owned-network identity for cleanup, independent of configuration."""
    _kind_for(name)
    _require(type(run_token) is str and _RUN.fullmatch(run_token) is not None)
    item = _map(record)
    identifier = item.get("Id")
    _require(type(identifier) is str and _ID.fullmatch(identifier) is not None)
    if network_id is not None:
        _require(type(network_id) is str and identifier == network_id)
    _equal(item, "Name", name)
    labels = _map(item.get("Labels"))
    _equal(labels, OWNER_LABEL, name)
    _equal(labels, RUN_LABEL, run_token)
    return identifier  # type: ignore[return-value]


def require_offline_network(
    record: object, name: str, run_token: str, *, network_id: str | None = None
) -> str:
    """Internal kind ONLY: isolated-gateway bridge on the fixed subnet, nothing else."""
    _require(_kind_for(name).internal)
    return require_owned_network(record, name, run_token, network_id=network_id)


def require_owned_network(
    record: object, name: str, run_token: str, *, network_id: str | None = None
) -> str:
    """Exact configuration for the kind the name selects; fails closed on any drift.

    Exact option equality fails closed if the daemon adds or drops an option.
    A matching record is configuration evidence, not proof of host routing.
    """
    kind = _kind_for(name)
    identifier = require_network_identity(record, name, run_token, network_id=network_id)
    item = _map(record)
    for key, value in (
        ("Driver", "bridge"),
        ("Scope", "local"),
        ("Internal", kind.internal),
        ("EnableIPv6", False),
        ("Attachable", False),
        ("Ingress", False),
        ("ConfigOnly", False),
    ):
        _equal(item, key, value)
    _equal(item, "Options", dict(kind.options))
    ipam = _map(item.get("IPAM"))
    _equal(ipam, "Driver", "default")
    configs = ipam.get("Config")
    _require(type(configs) is list and len(configs) == 1)  # type: ignore[arg-type]
    config = _map(configs[0])  # type: ignore[index]
    _equal(config, "Subnet", kind.subnet)
    _require(set(config) <= {"Subnet", "Gateway"})
    gateway = config.get("Gateway")
    _require(gateway is None or gateway == kind.gateway)
    return identifier


NETWORKED_NAME = re.compile(r"daemon-(?:gateway|fixture|client)-[0-9a-f]{24}\Z")
_ALIAS = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")


def networked_create_arguments(
    name: str,
    command: tuple[str, ...],
    network: str,
    *,
    run_token: str,
    alias: str | None = None,
) -> tuple[str, ...]:
    """Gateway-limit container attached only to the owned internal network."""
    _require(type(name) is str and NETWORKED_NAME.fullmatch(name) is not None)
    _kind_for(network)
    _require(bool(_run_label(run_token)))
    attachment: tuple[str, ...] = ("--network", network)
    if alias is not None:
        _require(type(alias) is str and _ALIAS.fullmatch(alias) is not None)
        attachment += ("--network-alias", alias)
    return _gateway_vector(name, command, run_token, attachment)


def require_networked_identity(
    record: object,
    name: str,
    *,
    container_id: str | None = None,
    run_token: str | None = None,
) -> str:
    return _require_identity(record, name, NETWORKED_NAME, container_id, run_token)


def require_networked(
    record: object,
    name: str,
    command: tuple[str, ...],
    network: str,
    *,
    run_token: str,
    network_id: str,
    alias: str | None = None,
    container_id: str | None = None,
) -> str:
    """Gateway limits plus exactly one attachment: the owned internal network.

    Before start the endpoint may not yet carry a network ID or address; when
    present they must be the owned network and an IPv4 address in its subnet.
    Exact alias equality fails closed if the daemon adds implicit aliases.
    """
    identifier = require_networked_identity(
        record, name, container_id=container_id, run_token=run_token
    )
    subnet = _kind_for(network).subnet
    _require(type(network_id) is str and _ID.fullmatch(network_id) is not None)
    item = _require_gateway_limits(record, command, network)
    settings = _map(item.get("NetworkSettings"))
    networks = _map(settings.get("Networks"))
    _require(set(networks) == {network})
    endpoint = _map(networks[network])
    _require(endpoint.get("NetworkID") in ("", network_id))
    address = endpoint.get("IPAddress")
    if type(address) is not str:
        raise PreflightError("offline gateway preflight refused")
    if address:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            raise PreflightError("offline gateway preflight refused") from None
        _require(parsed in ipaddress.ip_network(subnet))
    _equal(endpoint, "GlobalIPv6Address", "")
    aliases = endpoint.get("Aliases")
    if alias is None:
        _require(aliases is None or aliases == [])
    else:
        _require(aliases == [alias])
    _require_no_ports(settings)
    return identifier


# ---- Crawl4AI 0.9.4 service role (Phase 1 evaluation, owner-approved) ----
# A locally built, owner-approved derivative of the upstream image below. It
# changes only browser launch arguments and is replaced by upstream once a
# release meets the pilot's requirements.
C4AI_UPSTREAM_IMAGE = "sha256:048848e548fad60c670bd656cbb3eb204fd999709a30365d3697c411ce50796d"
C4AI_IMAGE = "sha256:455b56782cd1840d3659c79d67e59a63a6e6bb7df91f2f971ee72eacccd0f782"
C4AI_NAME = re.compile(r"daemon-c4ai-[0-9a-f]{24}\Z")
C4AI_MEMORY = 2 * 1024 * 1024 * 1024
C4AI_SHM = 256 * 1024 * 1024
C4AI_TMPFS = {
    "/tmp": "rw,nosuid,nodev,size=256m,mode=1777",
    "/var/lib/redis": "rw,nosuid,nodev,size=64m,mode=1777",
    "/home/appuser/.crawl4ai": "rw,nosuid,nodev,size=128m,mode=1777",
}
C4AI_CONFIG_TARGET = "/app/config.yml"
# Shipped /app/config.yml and the reviewed override that removes ONLY its
# ``- "--no-sandbox"`` line (scripts/web_fetch_pilot_c4ai_config_sandboxed.yml).
C4AI_SHIPPED_CONFIG_SHA256 = "e1c63398a3958414204fa1e218360d2e13baadafbb3f971ee4ba6a46cbc3225f"
C4AI_SANDBOXED_CONFIG_SHA256 = "af11aa97fb426170f16aea08b6cd48025b9caacc0510cb7b122e01ed9672a15b"
C4AI_IMAGE_ENV = frozenset(
    {
        "C4AI_VERSION",
        "DEBIAN_FRONTEND",
        "GPG_KEY",
        "LANG",
        "PATH",
        "PIP_DEFAULT_TIMEOUT",
        "PIP_DISABLE_PIP_VERSION_CHECK",
        "PIP_NO_CACHE_DIR",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONFAULTHANDLER",
        "PYTHONHASHSEED",
        "PYTHONUNBUFFERED",
        "PYTHON_ENV",
        "PYTHON_SHA256",
        "PYTHON_VERSION",
        "REDIS_HOST",
        "REDIS_PORT",
    }
)
_TOKEN = re.compile(r"[0-9a-f]{64}\Z")


def c4ai_sandboxed_config(shipped: bytes) -> bytes:
    """Pure: the reviewed override from hash-checked shipped bytes (one line removed)."""
    _require(type(shipped) is bytes)
    _require(hashlib.sha256(shipped).hexdigest() == C4AI_SHIPPED_CONFIG_SHA256)
    line = b'      - "--no-sandbox"\n'
    _require(shipped.count(line) == 1)
    override = shipped.replace(line, b"", 1)
    _require(hashlib.sha256(override).hexdigest() == C4AI_SANDBOXED_CONFIG_SHA256)
    return override


def c4ai_create_arguments(
    name: str,
    network: str,
    alias: str,
    *,
    run_token: str,
    api_token: str,
    sandboxed: bool,
    profile_path: str | None = None,
    config_path: str | None = None,
) -> tuple[str, ...]:
    """Pure fixed vector for one upstream Crawl4AI service container.

    Its own entrypoint and command run unchanged; only an ephemeral per-run API
    token is added. ``sandboxed`` additionally applies the pinned seccomp profile
    and the reviewed config override (read-only bind, the single mount exception).
    """
    _require(type(name) is str and C4AI_NAME.fullmatch(name) is not None)
    _kind_for(network)
    _require(type(alias) is str and _ALIAS.fullmatch(alias) is not None)
    _require(bool(_run_label(run_token)))
    _require(type(api_token) is str and _TOKEN.fullmatch(api_token) is not None)
    _require(type(sandboxed) is bool)
    hardening: tuple[str, ...] = ()
    if sandboxed:
        for path in (profile_path, config_path):
            _require(type(path) is str and os.path.isabs(path) and "," not in path)
            _require(os.path.normpath(path) == path)  # type: ignore[arg-type]
        hardening = (
            "--security-opt",
            "seccomp=" + profile_path,  # type: ignore[operator]
            "--mount",
            f"type=bind,source={config_path},target={C4AI_CONFIG_TARGET},readonly",
        )
    else:
        _require(profile_path is None and config_path is None)
    tmpfs: list[str] = []
    for target, options in C4AI_TMPFS.items():
        tmpfs.extend(("--tmpfs", target + ":" + options))
    return (
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
        name,
        "--label",
        OWNER_LABEL + "=" + name,
        *_run_label(run_token),
        "--network",
        network,
        "--network-alias",
        alias,
        "--read-only",
        "--user",
        "appuser",
        "--cap-drop",
        "ALL",
        *hardening,
        "--security-opt",
        "no-new-privileges",
        "--memory",
        "2g",
        "--memory-swap",
        "2g",
        "--cpus",
        "2",
        "--pids-limit",
        "256",
        "--shm-size",
        "256m",
        "--ipc",
        "private",
        "--cgroupns",
        "private",
        *tmpfs,
        "--env",
        "CRAWL4AI_API_TOKEN=" + api_token,
        "--restart",
        "no",
        "--log-driver",
        "none",
        "--no-healthcheck",
        C4AI_IMAGE,
    )


def require_c4ai_identity(
    record: object,
    name: str,
    *,
    container_id: str | None = None,
    run_token: str | None = None,
) -> str:
    """Identity for cleanup: c4ai name, pinned 0.9.4 image, owner and run labels."""
    _require(type(name) is str and C4AI_NAME.fullmatch(name) is not None)
    item = _map(record)
    identifier = item.get("Id")
    _require(type(identifier) is str and _ID.fullmatch(identifier) is not None)
    if container_id is not None:
        _require(type(container_id) is str and identifier == container_id)
    _equal(item, "Name", "/" + name)
    _equal(item, "Image", C4AI_IMAGE)
    labels = _map(_map(item.get("Config")).get("Labels"))
    _equal(labels, OWNER_LABEL, name)
    if run_token is not None:
        _require(type(run_token) is str and _RUN.fullmatch(run_token) is not None)
        _equal(labels, RUN_LABEL, run_token)
    return identifier  # type: ignore[return-value]


def require_c4ai(
    record: object,
    name: str,
    network: str,
    alias: str,
    *,
    run_token: str,
    network_id: str,
    api_token: str,
    sandboxed: bool,
    seccomp_option: str | None = None,
    config_path: str | None = None,
    container_id: str | None = None,
) -> str:
    """Exact upstream-service configuration before start; fails closed on drift."""
    identifier = require_c4ai_identity(record, name, container_id=container_id, run_token=run_token)
    _require(type(api_token) is str and _TOKEN.fullmatch(api_token) is not None)
    item = _map(record)
    config, host = _map(item.get("Config")), _map(item.get("HostConfig"))
    for key, value in (
        ("User", "appuser"),
        ("Cmd", ["bash", "entrypoint.sh"]),
        ("WorkingDir", "/app"),
        ("Tty", False),
        ("OpenStdin", True),
        ("StdinOnce", True),
        ("AttachStdin", True),
        ("AttachStdout", True),
        ("AttachStderr", True),
    ):
        _equal(config, key, value)
    _require(config.get("Entrypoint") in (None, []))
    _empty(config, "Volumes")
    _equal(_map(config.get("Healthcheck")), "Test", ["NONE"])
    env = config.get("Env")
    _require(type(env) is list and all(type(entry) is str for entry in env))  # type: ignore[union-attr]
    names = [entry.split("=", 1)[0] for entry in env]  # type: ignore[union-attr]
    _require(len(names) == len(set(names)))
    _require(set(names) == C4AI_IMAGE_ENV | {"CRAWL4AI_API_TOKEN"})
    _require("CRAWL4AI_API_TOKEN=" + api_token in env)  # type: ignore[operator]
    security = ["no-new-privileges"]
    if sandboxed:
        _require(type(seccomp_option) is str and seccomp_option.startswith("seccomp={"))
        security = [seccomp_option, "no-new-privileges"]  # type: ignore[list-item]
    for key, value in (
        ("NetworkMode", network),
        ("ReadonlyRootfs", True),
        ("Privileged", False),
        ("CapDrop", ["ALL"]),
        ("SecurityOpt", security),
        ("Memory", C4AI_MEMORY),
        ("MemorySwap", C4AI_MEMORY),
        ("NanoCpus", 2_000_000_000),
        ("PidsLimit", 256),
        ("ShmSize", C4AI_SHM),
        ("Tmpfs", C4AI_TMPFS),
        ("IpcMode", "private"),
        ("PidMode", ""),
        ("UTSMode", ""),
        ("UsernsMode", ""),
        ("CgroupnsMode", "private"),
        ("PublishAllPorts", False),
        ("AutoRemove", False),
    ):
        _equal(host, key, value)
    for key in (
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
    ):
        _empty(host, key)
    restart = _map(host.get("RestartPolicy"))
    _equal(restart, "Name", "no")
    _equal(restart, "MaximumRetryCount", 0)
    log = _map(host.get("LogConfig"))
    _equal(log, "Type", "none")
    mounts = item.get("Mounts")
    _require(type(mounts) is list)
    binds = []
    for mount in mounts:  # type: ignore[union-attr]
        fields = _map(mount)
        if fields.get("Type") == "tmpfs":
            _require(fields.get("Destination") in C4AI_TMPFS)
            continue
        _equal(fields, "Type", "bind")
        _equal(fields, "Destination", C4AI_CONFIG_TARGET)
        _equal(fields, "RW", False)
        _equal(fields, "Source", config_path)
        binds.append(fields)
    _require(len(binds) == (1 if sandboxed else 0))
    settings = _map(item.get("NetworkSettings"))
    networks = _map(settings.get("Networks"))
    _require(set(networks) == {network})
    endpoint = _map(networks[network])
    _require(endpoint.get("NetworkID") in ("", network_id))
    _equal(endpoint, "GlobalIPv6Address", "")
    _require(endpoint.get("Aliases") == [alias])
    _require_no_ports(settings)
    return identifier
