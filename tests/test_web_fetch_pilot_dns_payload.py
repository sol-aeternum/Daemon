"""Payload tests: no subprocess, socket, DNS or full fixture execution.

Never imports subprocess, never dials, never stages, never executes the
BOOTSTRAP_SOURCE/FIXTURE_SOURCE literals (static AST inspection only), and
never touches a container. One reviewed pure predicate is AST-extracted and
executed with inert doubles. Validates the pure builder/validator surface plus
static source-literal constraints for the future offline gateway container.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.web_fetch_pilot_dns_payload import (
    ALLOWED_ENV_NAMES,
    BOOTSTRAP_SOURCE,
    BOOTSTRAP_PYTHON_FLAGS,
    CTX_KEYS,
    CORE_STAGE_NAME,
    DNS_STAGE_NAME,
    FIXED_CPU_MAX,
    FIXED_LANG_VALUE,
    FIXTURE_STAGE_NAME,
    FIXTURE_SOURCE,
    MANIFEST_KEYS,
    PAYLOAD_FIXTURE_KEY,
    PAYLOAD_KEYS,
    PAYLOAD_MAX,
    RECORD_ALL_FIELDS,
    STATUS_FAILURE,
    STATUS_OK,
    STATUS_UNSUPPORTED,
    TRUSTED_HOSTNAME,
    build_exec_argv,
    expected_hashes,
    make_payload,
    validate_record,
)

FAKE_HOSTNAME = "fixture.invalid"  # fake-only; never resolved by any test here
FAKE_CORE = b"""# fake trusted core.py bytes for hash-fixture tests only
"""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError("duplicate key in payload")
        result[key] = value
    return result


def _fake_dns() -> bytes:
    return b"COLOR = 'teal'\n"  # fake trusted dns.py bytes; never executed


# ---------------------------------------------------------------------------
# make_payload: exact fixed map, strict UTF-8, fixed cap, no extras
# ---------------------------------------------------------------------------


def test_payload_is_exact_fixed_map() -> None:
    bundle = json.loads(
        make_payload(FAKE_CORE, _fake_dns()).decode("utf-8"), object_pairs_hook=_pairs
    )
    assert type(bundle) is dict
    assert set(bundle) == PAYLOAD_KEYS
    assert bundle["core.py"] == FAKE_CORE.decode("utf-8")
    assert bundle["dns.py"] == _fake_dns().decode("utf-8")
    assert bundle[PAYLOAD_FIXTURE_KEY] == FIXTURE_SOURCE


def test_payload_is_deterministic_and_bounded() -> None:
    first = make_payload(FAKE_CORE, _fake_dns())
    assert first == make_payload(FAKE_CORE, _fake_dns())
    assert len(first) <= PAYLOAD_MAX


def test_payload_rejects_non_bytes_and_empty_inputs() -> None:
    for core, dns in [
        ("x", _fake_dns()),
        (None, _fake_dns()),
        (b"", _fake_dns()),
        (FAKE_CORE, b""),
    ]:
        with pytest.raises(ValueError):
            make_payload(core, dns)  # type: ignore[arg-type]


def test_payload_rejects_invalid_utf8_trusted_bytes() -> None:
    with pytest.raises(ValueError):
        make_payload(b"\xff\xfe", _fake_dns())
    with pytest.raises(ValueError):
        make_payload(FAKE_CORE, b"\xc3\x28")


# ---------------------------------------------------------------------------
# expected_hashes: payload/modules/helper/fixture provenance manifest
# ---------------------------------------------------------------------------


def test_expected_hashes_keys_and_values() -> None:
    dns = _fake_dns()
    manifest = expected_hashes(FAKE_CORE, dns)
    assert set(manifest) == MANIFEST_KEYS
    for key in ("payload_sha256", "core_sha256", "dns_sha256", "helper_sha256", "fixture_sha256"):
        assert type(manifest[key]) is str and len(manifest[key]) == 64
    assert manifest["core_sha256"] == hashlib.sha256(FAKE_CORE).hexdigest()
    assert manifest["dns_sha256"] == hashlib.sha256(dns).hexdigest()
    payload = make_payload(FAKE_CORE, dns)
    assert manifest["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest["fixture_sha256"] == (
        hashlib.sha256(FIXTURE_SOURCE.encode("utf-8")).hexdigest()
    )


def test_expected_hashes_is_readonly() -> None:
    manifest = expected_hashes(FAKE_CORE, _fake_dns())
    with pytest.raises(TypeError):
        manifest["core_sha256"] = "x"  # type: ignore[index]


# ---------------------------------------------------------------------------
# build_exec_argv: pure argv vector, qualified interpreter flags
# ---------------------------------------------------------------------------


def test_exec_argv_shape() -> None:
    argv = build_exec_argv("/usr/local/bin/python")
    assert type(argv) is tuple
    assert argv[0] == "/usr/local/bin/python"
    assert list(argv[1:5]) == list(BOOTSTRAP_PYTHON_FLAGS)
    assert argv[5] == BOOTSTRAP_SOURCE


def test_exec_argv_rejects_bad_interpreter() -> None:
    for name in ("python", "", "rel/path/python", "/usr/local/bin/python\x00x"):
        with pytest.raises(ValueError):
            build_exec_argv(name)
    with pytest.raises(ValueError):
        build_exec_argv(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Static source-literal inspection: AST parse only, never executed
# ---------------------------------------------------------------------------


def _tree(source: str) -> ast.Module:
    return ast.parse(source)  # AST-level inspection only, never executed


def _names(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _record_key_sets(tree: ast.AST) -> list[set[str]]:
    found: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = {
                item.value
                for item in node.value.keys
                if isinstance(item, ast.Constant) and type(item.value) is str
            }
            if keys:
                found.append(keys)
    return found


ASSIGNED_FORBIDDEN_TOKENS = (
    "subprocess",
    "Popen",
    "socket",
    "getaddrinfo",
    "urlopen",
    "requests",
    "http.client",
    "ssl",
    "exec(",
    "eval(",
    "compile(",
    "__import__",
    "importlib",
    "mount",
    "chmod",
    "chown",
    "tarfile",
    "zipfile",
)


@pytest.mark.parametrize("literal", [FIXTURE_SOURCE, BOOTSTRAP_SOURCE])
def test_source_literals_parse_and_have_no_forbidden_tokens(literal: str) -> None:
    assert _tree(literal) is not None
    for token in ASSIGNED_FORBIDDEN_TOKENS:
        assert token not in literal, token


def test_fixture_imports_only_trusted_and_stdlib() -> None:
    tree = _tree(FIXTURE_SOURCE)
    modules = _names(tree)
    assert modules == {
        "asyncio",
        "hashlib",
        "io",
        "os",
        "sys",
        "scripts.web_fetch_pilot_dns",
    }
    assert "scripts.web_fetch_pilot_core" not in modules  # single trusted import source


def test_fixture_record_has_exact_schema_keys() -> None:
    key_sets = [
        keys
        for keys in _record_key_sets(_tree(FIXTURE_SOURCE))
        if len(keys) == len(RECORD_ALL_FIELDS)
    ]
    assert len(key_sets) == 2  # unsupported path + final path
    expected = RECORD_ALL_FIELDS | {"status"}
    for keys in key_sets:
        assert keys == expected  # fixed flat schema in the fixture


def test_bootstrap_imports_only_fixed_modules_and_static_fixture() -> None:
    assert _names(_tree(BOOTSTRAP_SOURCE)) == {
        "asyncio",
        "hashlib",
        "json",
        "os",
        "sys",
        "tempfile",
        "scripts.fixture",
    }
    assert "importlib" not in BOOTSTRAP_SOURCE


def test_bootstrap_stages_only_owned_tmp_paths() -> None:
    assert 'TemporaryDirectory(prefix="wfp-dns-qual-", dir="/tmp")' in BOOTSTRAP_SOURCE
    assert "root.startswith(ALLOWED_ROOT_PREFIX)" in BOOTSTRAP_SOURCE
    assert 'ALLOWED_ROOT_PREFIX = "/tmp/"' in BOOTSTRAP_SOURCE
    assert f'"{CORE_STAGE_NAME}"' in BOOTSTRAP_SOURCE
    assert f'"{DNS_STAGE_NAME}"' in BOOTSTRAP_SOURCE
    assert f'"{FIXTURE_STAGE_NAME}"' in BOOTSTRAP_SOURCE
    assert "shutil" not in BOOTSTRAP_SOURCE


def test_fixture_reads_only_fixed_proc_sys_paths() -> None:
    for path in (
        "/proc/self/fd",
        "/proc/net/route",
        "/proc/net/if_inet6",
        "/proc/net/ipv6_route",
        "/proc/self/status",
        "/proc/self/cgroup",
        "/sys/class/net",
        "/sys/fs/cgroup",
    ):
        assert f'"{path}"' in FIXTURE_SOURCE


def test_fixture_has_explicit_prehelper_route_gates() -> None:
    # Route evidence is checked BEFORE the helper can exist; the counts are
    # not merely recorded quantities. '!= 0' pre-helper refusals per file.
    for field in ("ipv4_rows != 0", "ipv6_address_rows != 0", "ipv6_route_rows != 0"):
        assert field in FIXTURE_SOURCE, field


def test_fixture_wraps_single_actual_spawner_call() -> None:
    # Exactly one real constructor/await call site on the trusted spawner.
    call_sites = [
        node
        for node in ast.walk(_tree(FIXTURE_SOURCE))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "StdlibSpawner"
    ]
    assert len(call_sites) == 1
    assert FIXTURE_SOURCE.count("spawner(host)") == 1
    # The wrapper publishes the real handle immediately; no intervening await.
    await_line = FIXTURE_SOURCE.find("await spawner(host)")
    append_line = FIXTURE_SOURCE.find("published.append(process)")
    assert 0 < await_line < append_line
    between = FIXTURE_SOURCE[await_line:append_line]
    assert "await" not in between[len("await spawner(host)") :]
    assert "process = await spawner(host)" in FIXTURE_SOURCE
    # HELPER_SOURCE is imported and hashed only, never reassigned/compiled.
    assert FIXTURE_SOURCE.count("HELPER_SOURCE") == 2
    assert "HELPER_SOURCE =" not in FIXTURE_SOURCE
    assert "spawner =" in FIXTURE_SOURCE and "resolver = DNSResolver(factory)" in FIXTURE_SOURCE


def test_fixture_flags_use_public_stdout_evidence() -> None:
    assert "sys.flags.unbuffered" not in FIXTURE_SOURCE
    assert "io.BufferedWriter" not in FIXTURE_SOURCE
    assert "io.FileIO" in FIXTURE_SOURCE
    assert "write_through" in FIXTURE_SOURCE
    assert "write_through is True" in FIXTURE_SOURCE
    # all three flag gates land before the helper invocation
    gate = FIXTURE_SOURCE.find("if not isolated or not no_site or not unbuffered")
    helper = FIXTURE_SOURCE.find("resolver = DNSResolver(factory)")
    assert 0 < gate < helper


def test_fixture_env_contract_is_exact_names_and_lang_value() -> None:
    assert 'ENV_KEYS_EXPECTED = ("LANG", "PATH")' in FIXTURE_SOURCE
    assert 'LANG_VALUE_EXPECTED = "C.UTF-8"' in FIXTURE_SOURCE
    assert 'os.environ.get("LANG", "") != LANG_VALUE_EXPECTED' in FIXTURE_SOURCE
    assert FIXED_LANG_VALUE == "C.UTF-8"
    assert sorted(ALLOWED_ENV_NAMES) == ["LANG", "PATH"]


def test_fixture_hostname_is_the_single_fixed_refusal_target() -> None:
    assert FIXTURE_SOURCE.count('"' + FAKE_HOSTNAME + '"') == 1
    assert TRUSTED_HOSTNAME == FAKE_HOSTNAME


def test_fixture_checks_are_explicit_not_assert() -> None:
    assert " assert " not in FIXTURE_SOURCE


def test_bootstrap_env_is_minimal_names_only() -> None:
    assert tuple(sorted(ALLOWED_ENV_NAMES)) == ("LANG", "PATH")
    assert FIXED_LANG_VALUE == "C.UTF-8"
    assert CTX_KEYS == {"payload_sha256"}
    assert "HTTP_PROXY" not in BOOTSTRAP_SOURCE
    assert "HTTPS_PROXY" not in BOOTSTRAP_SOURCE


def test_expected_cgroup_values_are_fixed_literals() -> None:
    # The fixture's own expected constants must mirror the parent manifest.
    assert FIXED_CPU_MAX == "50000 100000"
    assert "60000 100000" not in FIXTURE_SOURCE


def test_static_no_runtime_execution_of_fixture_sources() -> None:
    # Static harness constraints: the bootstrap's only dynamic action would be
    # the guarded fixture invocation, and the fixture itself never self-runs.
    assert "sys.stdin.buffer.read" in BOOTSTRAP_SOURCE
    assert "asyncio.wait_for" in BOOTSTRAP_SOURCE
    assert "fixture_mod.main(ctx)" in BOOTSTRAP_SOURCE
    assert "asyncio.run" not in FIXTURE_SOURCE
    assert "__name__" not in FIXTURE_SOURCE  # no self-executing entrypoint


def test_test_module_never_imports_native_execution() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert "subprocess" not in imported
    assert "socket" not in imported
    assert "docker" not in imported
    assert not any(
        name.startswith("scripts.web_fetch") and name != "scripts.web_fetch_pilot_dns_payload"
        for name in imported
    )


# ---------------------------------------------------------------------------
# validate_record: strict single bounded record only
# ---------------------------------------------------------------------------


MANIFEST = expected_hashes(FAKE_CORE, _fake_dns())


def _record(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "status": STATUS_OK,
        "dns_failure": True,
        "cleanup_failed": False,
        "flags_isolated": True,
        "flags_no_site": True,
        "flags_unbuffered": True,
        "no_new_privs": True,
        "cap_eff": 0,
        "seccomp": 2,
        "uid": 999,
        "ipv4_route_rows": 0,
        "ipv6_address_rows": 0,
        "ipv6_route_rows": 0,
        "memory_max": 134217728,
        "memory_swap_max": 0,
        "pids_max": 32,
        "cpu_max": "50000 100000",
        "env_keys": sorted(ALLOWED_ENV_NAMES),
        "interfaces": ["lo"],
        "fds_before": 6,
        "fds_after": 6,
        "fds_equal": True,
        "tasks_before": 1,
        "tasks_after": 1,
        "tasks_equal": True,
        "actor_jobs": 0,
        "actor_tasks": 0,
        "actor_processes": 0,
        "helper_started": True,
        "helper_exit_code": 1,
        "interpreter": "3.12.1",
        "python_exe": "/usr/local/bin/python",
        "stdlib_location": "/usr/local/lib/python3.12",
    }
    for key, value in MANIFEST.items():
        base[key] = value
    base.update(over)
    if over.get("__exclude__"):
        for key in over["__exclude__"]:  # type: ignore[union-attr]
            base.pop(key)
    return {key: value for key, value in base.items() if key != "__exclude__"}


def _checks(record: dict[str, object]) -> bytes:
    return json.dumps(record, sort_keys=True).encode("utf-8")


@pytest.mark.parametrize("status", [STATUS_OK, STATUS_FAILURE, STATUS_UNSUPPORTED])
def test_validate_record_accepts_schema_valid_records(status: str) -> None:
    over: dict[str, object] = {"status": status}
    if status == STATUS_OK:
        pass  # fully-verified ok shape
    elif status == STATUS_FAILURE:
        over["dns_failure"] = False
    else:
        # The actual unsupported shape: helper never started, no exit code,
        # zero/unverified limits, empty cpu string, no dns claim.
        over.update(
            dns_failure=False,
            helper_started=False,
            helper_exit_code=None,
            memory_max=0,
            memory_swap_max=0,
            pids_max=0,
            cpu_max="",
        )
    parsed = validate_record(_checks(_record(**over)), MANIFEST)
    assert parsed["status"] == status


def test_validate_record_accepts_minimal_abort_record() -> None:
    parsed = validate_record(b'{"status":"failure"}', MANIFEST)
    assert parsed["status"] == STATUS_FAILURE


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"{}" * 4096,
        b"\xff\xfe",
        b"not json",
        b'{"status":"x"}',
        b'[{"status":"failure"}]',
        b"null",
        b'"ok"',
    ],
)
def test_validate_record_rejects_malformed_bounded_input(raw: bytes) -> None:
    with pytest.raises(ValueError):
        validate_record(raw, MANIFEST)


def test_validate_record_rejects_oversized_record() -> None:
    record = _record(status=STATUS_FAILURE)
    record["interpreter"] = "3.12.1" * 2200  # forces output past 8 KiB
    with pytest.raises(ValueError):
        validate_record(_checks(record), MANIFEST)


def test_validate_record_rejects_duplicate_keys() -> None:
    body = json.dumps(_record(status=STATUS_FAILURE), sort_keys=True)
    injected = body[:-1] + "," + '"status":"failure"}'
    assert injected.count('"status"') == 2
    with pytest.raises(ValueError):
        validate_record(injected.encode("utf-8"), MANIFEST)


def test_validate_record_rejects_extra_and_missing_fields() -> None:
    extra = _record(status=STATUS_FAILURE, diagnostic="leaked")
    with pytest.raises(ValueError):
        validate_record(_checks(extra), MANIFEST)
    missing = _record(status=STATUS_FAILURE, __exclude__=["cpu_max"])
    with pytest.raises(ValueError):
        validate_record(_checks(missing), MANIFEST)


def test_validate_record_rejects_bad_status() -> None:
    for status in ("qual", "qualified", 3, None, True):
        with pytest.raises(ValueError):
            validate_record(_checks(_record(status=status)), MANIFEST)  # type: ignore[arg-type]


def test_validate_record_rejects_helper_hash_mismatch() -> None:
    with pytest.raises(ValueError):
        validate_record(_checks(_record(helper_sha256="a" * 64)), MANIFEST)


def test_validate_record_rejects_bad_hash_shapes() -> None:
    for override in ("A" * 64, "g" * 64, "a" * 16, 5, None):
        with pytest.raises(ValueError):
            validate_record(_checks(_record(payload_sha256=override)), MANIFEST)  # type: ignore[arg-type]


def test_validate_record_rejects_bad_manifest() -> None:
    for manifest in (
        {},
        {"role": "x"},
        {**MANIFEST, "extra": "x"},
        {**MANIFEST, "core_sha256": "z" * 64},
        {**MANIFEST, "dns_sha256": 12},
        {**MANIFEST, "role_sha256": "a" * 64},
    ):
        with pytest.raises(ValueError):
            validate_record(_checks(_record(status=STATUS_FAILURE)), manifest)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field", ["dns_failure", "cleanup_failed", "flags_isolated", "no_new_privs"]
)
def test_validate_record_rejects_bad_bools(field: str) -> None:
    for value in (1, 0, "true", "False", None, 1.0):
        with pytest.raises(ValueError):
            validate_record(_checks(_record(status=STATUS_FAILURE, **{field: value})), MANIFEST)  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["fds_before", "tasks_after", "actor_jobs"])
def test_validate_record_rejects_bad_counters(field: str) -> None:
    for value in (-1, "2", 2147483648, 3.5, True, None):
        with pytest.raises(ValueError):
            validate_record(_checks(_record(status=STATUS_FAILURE, **{field: value})), MANIFEST)  # type: ignore[arg-type]


def test_validate_record_rejects_wrong_env_keys_or_interfaces() -> None:
    for over in (
        {"env_keys": ["PATH", "HTTP_PROXY"]},
        {"env_keys": []},
        {"env_keys": "PATH"},
        {"env_keys": ["path"]},
        {"interfaces": ["lo", "eth0"]},
        {"interfaces": []},
        {"interfaces": ["eth0"]},
    ):
        with pytest.raises(ValueError):
            validate_record(_checks(_record(status=STATUS_FAILURE, **over)), MANIFEST)  # type: ignore[arg-type]


def test_validate_record_rejects_bad_identity_paths() -> None:
    for over in (
        {"python_exe": "python3"},
        {"python_exe": ""},
        {"stdlib_location": ""},
        {"interpreter": "3.12.1-unicode-བྷ"},
        {"interpreter": 3},
    ):  # bad types and paths
        with pytest.raises(ValueError):
            validate_record(_checks(_record(status=STATUS_FAILURE, **over)), MANIFEST)  # type: ignore[arg-type]


def test_validate_record_rejects_ok_gate_violations() -> None:
    for over in (
        {"dns_failure": False},
        {"cleanup_failed": True},
        {"fds_equal": False},
        {"tasks_equal": False},
        {"actor_jobs": 1},
        {"actor_tasks": 2},
        {"actor_processes": 1},
        {"fds_before": 6, "fds_after": 5},
        {"tasks_before": 1, "tasks_after": 2},
        {"flags_isolated": False},
        {"flags_no_site": False},
        {"flags_unbuffered": False},
        {"no_new_privs": False},
        {"helper_started": False},
        {"helper_exit_code": None},
        {"helper_exit_code": 0},
        {"helper_exit_code": -9},
    ):
        with pytest.raises(ValueError):
            validate_record(_checks(_record(status=STATUS_OK, **over)), MANIFEST)  # type: ignore[arg-type]


def test_validate_record_accepts_failure_with_unstarted_helper_evidence() -> None:
    # A failed run may honestly report no started helper / no reaped code;
    # those shapes are only ever non-qualifying statuses.
    record = _record(
        status=STATUS_FAILURE, dns_failure=False, helper_started=False, helper_exit_code=None
    )
    parsed = validate_record(_checks(record), MANIFEST)
    assert parsed["status"] == STATUS_FAILURE
    for code in (0, -9, 1, None):
        record = _record(status=STATUS_FAILURE, helper_exit_code=code)
        validate_record(_checks(record), MANIFEST)


def test_validate_record_rejects_unsupported_claiming_helper_or_dns() -> None:
    for over in (
        {"helper_started": True},
        {"helper_exit_code": 1},
        {"helper_exit_code": 0},
        {"helper_exit_code": -9},
        {"dns_failure": True},
        {"cleanup_failed": True},
        {"actor_jobs": 1},
        {"fds_equal": False},
        {"memory_max": 134217728},
        {"cpu_max": "50000 100000"},
    ):
        with pytest.raises(ValueError):
            validate_record(_checks(_record(status=STATUS_UNSUPPORTED, **over)), MANIFEST)  # type: ignore[arg-type]


def test_validate_record_accepts_actual_unsupported_zero_shape() -> None:
    # The real unsupported record: fixed limits remain unverified zeros /
    # empty cpu string, helper never started; the shape must still validate.
    record = dict(
        _record(
            status=STATUS_UNSUPPORTED,
            memory_max=0,
            memory_swap_max=0,
            pids_max=0,
            cpu_max="",
            helper_started=False,
            helper_exit_code=None,
            dns_failure=False,
            cleanup_failed=False,
            fds_before=6,
            fds_after=6,
            fds_equal=True,
            tasks_before=1,
            tasks_after=1,
            tasks_equal=True,
        )
    )
    parsed = validate_record(_checks(record), MANIFEST)
    assert parsed["status"] == STATUS_UNSUPPORTED
    assert parsed["helper_started"] is False
    assert parsed["helper_exit_code"] is None
    assert parsed["memory_max"] == 0
    assert parsed["cpu_max"] == ""


def test_builder_rejects_oversized_raw_inputs_before_decode() -> None:
    oversized = b"a" * (PAYLOAD_MAX + 1)
    with pytest.raises(ValueError):
        make_payload(oversized, _fake_dns())
    with pytest.raises(ValueError):
        make_payload(FAKE_CORE, oversized)
    # Even a raw input that fits the per-file cap overflows the fixed 128 KiB
    # bundle cap once the bundle's other fields are included; refusal either
    # way (never silently truncate/decode-and-ship).
    for oversized_dns in (b"a" * PAYLOAD_MAX, b"b" * PAYLOAD_MAX):
        with pytest.raises(ValueError):
            make_payload(FAKE_CORE, oversized_dns)


def test_validate_record_refusal_is_fixed_text() -> None:
    with pytest.raises(ValueError) as caught:
        validate_record(b"", MANIFEST)
    assert str(caught.value) == "record refused"  # fixed message, never raw


def _unsupported() -> dict[str, object]:
    return _record(
        status=STATUS_UNSUPPORTED,
        memory_max=0,
        memory_swap_max=0,
        pids_max=0,
        cpu_max="",
        helper_started=False,
        helper_exit_code=None,
        dns_failure=False,
    )


@pytest.mark.parametrize(
    "field",
    [
        "uid",
        "cap_eff",
        "seccomp",
        "ipv4_route_rows",
        "ipv6_address_rows",
        "ipv6_route_rows",
        "memory_max",
        "memory_swap_max",
        "pids_max",
    ],
)
@pytest.mark.parametrize("value", [None, True, "0", [], -1])
def test_unsupported_exact_integer_types(field: str, value: object) -> None:
    from scripts.web_fetch_pilot_dns_payload import RecordRefused

    record = _unsupported()
    record[field] = value
    with pytest.raises(RecordRefused, match="^record refused$"):
        validate_record(_checks(record), MANIFEST)


@pytest.mark.parametrize(
    "field,value",
    [
        ("uid", 0),
        ("cap_eff", 1),
        ("seccomp", 0),
        ("ipv4_route_rows", 1),
        ("ipv6_address_rows", 1),
        ("ipv6_route_rows", 1),
        ("memory_max", 134217728),
        ("memory_swap_max", 1),
        ("pids_max", 32),
        ("cpu_max", "50000 100000"),
        ("flags_isolated", False),
        ("flags_no_site", False),
        ("flags_unbuffered", False),
        ("no_new_privs", False),
    ],
)
def test_actual_unsupported_baseline_rejects_contradictions(field: str, value: object) -> None:
    record = _unsupported()
    record[field] = value
    with pytest.raises(ValueError):
        validate_record(_checks(record), MANIFEST)


@pytest.mark.parametrize(
    "case", ["unbuffered", "buffered", "missing", "text_buffered", "not_isolated", "site_enabled"]
)
def test_actual_flags_predicate_with_inert_objects(case: str) -> None:
    # Execute ONLY this reviewed pure function, extracted from the literal AST.
    # Its globals are inert doubles: no imports, fds, staging or helper execution.
    node = next(
        n
        for n in ast.parse(FIXTURE_SOURCE).body
        if isinstance(n, ast.FunctionDef) and n.name == "_flags"
    )

    class FileIO:
        pass

    binary = FileIO() if case not in ("buffered", "missing") else object()
    stdout = SimpleNamespace(buffer=binary, write_through=case != "text_buffered")
    if case == "missing":
        stdout = SimpleNamespace()
    namespace = {
        "io": SimpleNamespace(FileIO=FileIO),
        "sys": SimpleNamespace(
            stdout=stdout,
            flags=SimpleNamespace(
                isolated=int(case != "not_isolated"), no_site=int(case != "site_enabled")
            ),
        ),
        "_Failure": ValueError,
    }
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), "<pure-flags-predicate>", "exec"),
        namespace,
    )
    if case == "unbuffered":
        assert namespace["_flags"]() == {
            "flags_isolated": True,
            "flags_no_site": True,
            "flags_unbuffered": True,
        }
    else:
        with pytest.raises(ValueError):
            namespace["_flags"]()


def test_bootstrap_calls_main_only_under_entrypoint_guard() -> None:
    last = ast.parse(BOOTSTRAP_SOURCE).body[-1]
    expected = ast.parse('if __name__ == "__main__":\n    sys.exit(main())').body[0]
    assert ast.dump(last) == ast.dump(expected)
