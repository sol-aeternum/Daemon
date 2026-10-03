"""Pure E3c tests: gateway entrypoint helpers, gateway bundle and bootstrap.

No process, socket, DNS or container runs here. Bootstrap functions are taken
from the derived literal's AST (definitions only) and run over a fake fd 0.
"""

from __future__ import annotations

import ast
import json
import struct
import types
from pathlib import Path

import pytest

from scripts.web_fetch_pilot_browser_payload import (
    BOOTSTRAP_SOURCE,
    GATEWAY_BOOTSTRAP_SOURCE,
    GATEWAY_ENVIRONMENT,
    GATEWAY_MODULE_NAMES,
    MODULE_NAMES,
    PYTHON,
    _derive_gateway_bootstrap,
    gateway_command,
    make_gateway_bundle,
)
from scripts.web_fetch_pilot_container_policy import networked_create_arguments
from scripts.web_fetch_pilot_gateway_entrypoint import parse_run, routes_ok

ROOT = Path(__file__).resolve().parents[1]
RUN = {"allowed_hosts": ["openai.com"], "inventory": [], "deadline_seconds": 40.0}
HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
SUBNET_ROW = "eth0\t00030201\t00000000\t0001\t0\t0\t0\tF8FFFFFF\t0\t0\t0\n"


def modules() -> dict[str, bytes]:
    return {name: (ROOT / "scripts" / name).read_bytes() for name in GATEWAY_MODULE_NAMES}


def test_gateway_entrypoint_import_is_host_safe() -> None:
    source = (ROOT / "scripts/web_fetch_pilot_gateway_entrypoint.py").read_text()
    top = ast.parse(source).body
    imported = {
        alias.name
        for node in top
        if isinstance(node, ast.ImportFrom | ast.Import)
        for alias in node.names
    }
    assert not {"Gateway", "DNSResolver", "NumericConnector"} & imported


def test_gateway_parse_run_strict() -> None:
    assert parse_run(dict(RUN)).inventory == ()
    assert parse_run({**RUN, "inventory": ["192.0.2.10", "203.0.113.0/24"]}).inventory
    for key, value in (
        ("allowed_hosts", []),
        ("allowed_hosts", ["OpenAI.com"]),
        ("inventory", None),
        ("inventory", [""]),
        ("inventory", "1.2.3.4"),
        ("deadline_seconds", 40),
        ("deadline_seconds", 45.0),
    ):
        with pytest.raises(ValueError):
            parse_run({**RUN, key: value})
    with pytest.raises(ValueError):
        parse_run({**RUN, "extra": 1})


def test_routes_require_eth0_only_and_no_default_route() -> None:
    assert routes_ok(HEADER + SUBNET_ROW)
    assert not routes_ok(HEADER)  # No subnet route at all.
    default = "eth0\t00000000\t01030201\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
    assert not routes_ok(HEADER + SUBNET_ROW + default)
    assert not routes_ok(HEADER + SUBNET_ROW.replace("eth0", "eth1"))
    assert not routes_ok(HEADER + "eth0\t00030201\n")


def test_gateway_bundle_exact_and_refusals() -> None:
    bundle = make_gateway_bundle(modules(), dict(RUN))
    (length,) = struct.unpack(">I", bundle[:4])
    value = json.loads(bundle[4:])
    assert length == len(bundle) - 4 and value["run"] == RUN
    assert {k: v.encode() for k, v in value["modules"].items()} == modules()
    with pytest.raises(ValueError):
        make_gateway_bundle({**modules(), "web_fetch_pilot_relay.py": b"x"}, dict(RUN))
    with pytest.raises(ValueError):
        make_gateway_bundle(modules(), {**RUN, "deadline_seconds": 99.0})


def test_gateway_command_isolated_no_site_and_fits_networked_policy() -> None:
    command = gateway_command()
    assert command == (
        "-i",
        *GATEWAY_ENVIRONMENT,
        PYTHON,
        "-I",
        "-S",
        "-u",
        "-c",
        GATEWAY_BOOTSTRAP_SOURCE,
    )
    assert GATEWAY_ENVIRONMENT == ("PATH=/usr/local/bin:/usr/bin:/bin", "LANG=C.UTF-8")
    vector = networked_create_arguments(
        "daemon-gateway-" + "4" * 24, command, "daemon-net-offline-" + "5" * 24, run_token="6" * 32
    )
    assert vector[-len(command) :] == command


def test_gateway_bootstrap_is_an_exact_derivation() -> None:
    tree = ast.parse(GATEWAY_BOOTSTRAP_SOURCE)
    names = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "NAMES"
    )
    assert ast.literal_eval(names) == GATEWAY_MODULE_NAMES
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "scripts.web_fetch_pilot_gateway_entrypoint" in imports
    assert "scripts.web_fetch_pilot_browser_entrypoint" not in imports
    assert '"wfp-gateway-"' in GATEWAY_BOOTSTRAP_SOURCE and "print(" not in GATEWAY_BOOTSTRAP_SOURCE
    gateway_names = "".join(f'    "{name}",\n' for name in GATEWAY_MODULE_NAMES)
    browser_names = "".join(f'    "{name}",\n' for name in MODULE_NAMES)
    restored = (
        GATEWAY_BOOTSTRAP_SOURCE.replace('"""Gateway container', '"""Browser container')
        .replace(gateway_names, browser_names)
        .replace('prefix="wfp-gateway-"', 'prefix="wfp-browser-"')
        .replace("pilot_gateway_entrypoint as", "pilot_browser_entrypoint as")
    )
    assert restored == BOOTSTRAP_SOURCE  # Nothing else differs.
    with pytest.raises(RuntimeError):
        _derive_gateway_bootstrap(BOOTSTRAP_SOURCE.replace('prefix="wfp-browser-"', "x"))


def test_gateway_bootstrap_reads_exactly_one_bundle() -> None:
    tree = ast.parse(GATEWAY_BOOTSTRAP_SOURCE)
    keep: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"read_exact", "unique", "reject", "load"}
        or isinstance(node, ast.Assign)
        and ast.unparse(node.targets[0]) in {"NAMES", "BUNDLE_CAP", "FILE_CAP"}
    ]
    frames = b"\x01\x00 frames"
    buffer = bytearray(make_gateway_bundle(modules(), dict(RUN)) + frames)

    def fake_read(fd: int, size: int) -> bytes:
        assert fd == 0
        chunk = bytes(buffer[: min(size, 5)])
        del buffer[: len(chunk)]
        return chunk

    namespace: dict[str, object] = {
        "os": types.SimpleNamespace(read=fake_read),
        "json": json,
        "struct": struct,
    }
    exec(compile(ast.Module(body=keep, type_ignores=[]), "<gateway-bootstrap>", "exec"), namespace)
    loaded, run = namespace["load"]()  # type: ignore[operator]
    assert run == RUN and set(loaded) == set(GATEWAY_MODULE_NAMES)
    assert bytes(buffer) == frames
