"""Pure closed offline C4AI inspect contract. No settings applied or Docker I/O.

Extracted resource/identity controls from #431, not its 0.9.4 image or seccomp.
This source acceptance specification is NOT a runtime-qualified launch profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import require

UPSTREAM_COMMIT = "838869bdda93f25e6d50b3f50ae9577d9b19a586"
AMD64_MANIFEST = "sha256:636a6f9e5127f54771f14f709bf1b95693aacc30512426b26d09768b0269f1f4"
OWNER_LABEL = "daemon.crawl4ai-qualification.owner"
RUN_LABEL = "daemon.crawl4ai-qualification.run"
TARGET = "/app/config.yml"
MEMORY = 2 * 1024**3
SHM = 256 * 1024**2
TMPFS = {
    "/tmp": "rw,nosuid,nodev,size=256m,mode=1777",
    "/var/lib/redis": "rw,nosuid,nodev,size=64m,mode=1777",
    "/home/appuser/.crawl4ai": "rw,nosuid,nodev,size=128m,mode=1777",
}
_ID = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"daemon-c4ai-[0-9a-f]{24}\Z")
_RUN = re.compile(r"[0-9a-f]{32}\Z")
# Metadata-only source-known environment names; no credential/security overrides.
IMAGE_ENV_NAMES = frozenset(
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


def mapping(value: object) -> dict:
    require(type(value) is dict)
    assert isinstance(value, dict)
    return value


def equal(record: dict, key: str, expected: object) -> None:
    require(key in record and type(record[key]) is type(expected) and record[key] == expected)


@dataclass(frozen=True)
class Specification:
    """Independent supervisor expectations, NEVER values inferred from inspect.

    image_id is a locally verified image-config ID, not the registry manifest.
    Native image identity/environment verification is not implemented here.
    """

    daemon_id: str
    image_id: str
    image_env: tuple[str, ...]

    def __post_init__(self) -> None:
        require(type(self.daemon_id) is str and 0 < len(self.daemon_id) <= 256)
        require(type(self.image_id) is str and self.image_id.startswith("sha256:"))
        require(_ID.fullmatch(self.image_id[7:]) is not None)
        require(self.image_id != AMD64_MANIFEST)
        require(type(self.image_env) is tuple and len(self.image_env) <= len(IMAGE_ENV_NAMES))
        require(
            all(
                type(v) is str and "=" in v and "\0" not in v and len(v) <= 4096
                for v in self.image_env
            )
        )
        names = [v.split("=", 1)[0] for v in self.image_env]
        require(len(names) == len(set(names)) and set(names) <= IMAGE_ENV_NAMES)


def identity(record: object, spec: Specification, name: str, run: str, identifier: str) -> dict:
    require(type(name) is str and _NAME.fullmatch(name) is not None)
    require(type(run) is str and _RUN.fullmatch(run) is not None)
    require(type(identifier) is str and _ID.fullmatch(identifier) is not None)
    item = mapping(record)
    equal(item, "Id", identifier)
    equal(item, "Name", "/" + name)
    equal(item, "Image", spec.image_id)
    equal(mapping(mapping(item.get("Config")).get("Labels")), OWNER_LABEL, name)
    equal(mapping(mapping(item.get("Config")).get("Labels")), RUN_LABEL, run)
    return item


def created(
    record: object, spec: Specification, name: str, run: str, identifier: str, config_path: str
) -> None:
    item = identity(record, spec, name, run, identifier)
    config, host = mapping(item.get("Config")), mapping(item.get("HostConfig"))
    expected_config = {
        "Labels": {OWNER_LABEL: name, RUN_LABEL: run},
        "User": "appuser",
        "Cmd": ["bash", "entrypoint.sh"],
        "Entrypoint": None,
        "WorkingDir": "/app",
        "Env": list(spec.image_env),
        "Volumes": None,
        "Healthcheck": {"Test": ["NONE"]},
        "Tty": False,
        "OpenStdin": False,
    }
    require(set(config) == set(expected_config))
    for key, value in expected_config.items():
        equal(config, key, value)
    expected_host = {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges"],
        "Memory": MEMORY,
        "MemorySwap": MEMORY,
        "NanoCpus": 2_000_000_000,
        "PidsLimit": 256,
        "ShmSize": SHM,
        "Tmpfs": TMPFS,
        "IpcMode": "private",
        "PidMode": "",
        "UTSMode": "",
        "UsernsMode": "",
        "CgroupnsMode": "private",
        "PublishAllPorts": False,
        "AutoRemove": False,
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
        "LogConfig": {"Type": "none", "Config": {}},
        "Mounts": [{"Type": "bind", "Source": config_path, "Target": TARGET, "ReadOnly": True}],
    }
    for key, value in expected_host.items():
        equal(host, key, value)
    empty_keys = (
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
    require(set(host) == set(expected_host) | set(empty_keys))
    for key in empty_keys:
        require(key in host and host[key] in (None, [], {}, ""))
    equal(
        item,
        "Mounts",
        [
            {
                "Type": "bind",
                "Source": config_path,
                "Destination": TARGET,
                "RW": False,
                "Propagation": "rprivate",
                "Mode": "",
            }
        ],
    )
    settings = mapping(item.get("NetworkSettings"))
    equal(settings, "Networks", {"none": {"IPAddress": "", "GlobalIPv6Address": ""}})
    equal(settings, "Ports", {})
    state = mapping(item.get("State"))
    equal(state, "Status", "created")
    equal(state, "Running", False)
