"""Pure trusted-bundle builder for the future offline gateway DNS qualification.

Builders and validators for DATA ONLY: a strict UTF-8 JSON payload of exact
trusted bytes, fixed source literals, and a strict parent-side record
validator. This module performs no process, network, filesystem, container or
interpreter execution and has no import-time side effects. Trusted inputs are
the exact bytes of the unchanged host modules (staged under fixed names);
nothing is patched, aliased or copied from a caller path or archive.

The future reviewed gateway driver owns every execution gate (image inspect,
HostConfig, entrypoint/environment scrub — see
``scripts/web_fetch_pilot_container_policy.py``) and must run BEFORE any
execution. A record's own ``ok`` status is never the sole containment
authority: the fixture's verified DNS refusal qualifies native helper
start/refusal/cleanup only, not public DNS success or SSRF containment.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from types import MappingProxyType

from scripts.web_fetch_pilot_dns import HELPER_SOURCE

PAYLOAD_CORE_KEY = "core.py"
PAYLOAD_DNS_KEY = "dns.py"
PAYLOAD_FIXTURE_KEY = "FIXTURE_SOURCE"
PAYLOAD_KEYS = frozenset({PAYLOAD_CORE_KEY, PAYLOAD_DNS_KEY, PAYLOAD_FIXTURE_KEY})
PAYLOAD_MAX = 128 * 1024  # total trusted bundle; matches the read cap
OUTPUT_RECORD_MAX = 8192  # single child record, bounded UTF-8
STAGE_DIR_NAME = "scripts"  # owned /tmp tmpfs package root
CORE_STAGE_NAME = "web_fetch_pilot_core.py"  # staged name equals trusted module name
DNS_STAGE_NAME = "web_fetch_pilot_dns.py"  # staged name equals trusted module name
FIXTURE_STAGE_NAME = "fixture.py"  # the fixture is the on-disk helper module
STAGE_NAME_MAP = MappingProxyType(
    {
        PAYLOAD_CORE_KEY: CORE_STAGE_NAME,
        PAYLOAD_DNS_KEY: DNS_STAGE_NAME,
        PAYLOAD_FIXTURE_KEY: FIXTURE_STAGE_NAME,
    }
)
TRUSTED_HOSTNAME = "fixture.invalid"  # fixed DNS refusal target
BOOTSTRAP_PYTHON_FLAGS = ("-I", "-S", "-u", "-c")  # qualified interpreter flags
STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_UNSUPPORTED = "unsupported"
STATUS_SET = frozenset({STATUS_OK, STATUS_FAILURE, STATUS_UNSUPPORTED})
CTX_KEYS = frozenset({"payload_sha256"})
ALLOWED_ENV_NAMES = ("LANG", "PATH")  # names only, no values recorded except LANG
FIXED_LANG_VALUE = "C.UTF-8"  # planned env-i contract; disables locale coercion
FIXED_CPU_MAX = "50000 100000"  # cgroup v2 quota/period == ratio 0.5
FIXED_MEMORY_MAX = 134217728  # cgroup v2 memory.max, 128 MiB
FIXED_MEMORY_SWAP_MAX = 0  # cgroup v2 swap.max, none
FIXED_PIDS_MAX = 32  # cgroup v2 pids.max

RECORD_BOOL_FIELDS = frozenset(
    {
        "dns_failure",
        "flags_isolated",
        "flags_no_site",
        "flags_unbuffered",
        "no_new_privs",
        "fds_equal",
        "tasks_equal",
        "cleanup_failed",
        "helper_started",
    }
)
RECORD_STR_FIELDS = frozenset(
    {
        "interpreter",
        "python_exe",
        "stdlib_location",
        "cpu_max",
        "core_sha256",
        "dns_sha256",
        "helper_sha256",
        "payload_sha256",
        "fixture_sha256",
    }
)
RECORD_LIST_FIELDS = frozenset({"env_keys", "interfaces"})
RECORD_FIXED_LISTS: dict[str, list[str]] = {
    "env_keys": list(ALLOWED_ENV_NAMES),
    "interfaces": ["lo"],
}
RECORD_INT_FIELDS = frozenset(
    {
        "uid",
        "ipv4_route_rows",
        "ipv6_address_rows",
        "ipv6_route_rows",
        "cap_eff",
        "seccomp",
        "memory_max",
        "memory_swap_max",
        "pids_max",
        "fds_before",
        "fds_after",
        "tasks_before",
        "tasks_after",
        "actor_jobs",
        "actor_tasks",
        "actor_processes",
    }
)
RECORD_FIXED_INTS: dict[str, int] = {
    "uid": 999,
    "ipv4_route_rows": 0,
    "ipv6_address_rows": 0,
    "ipv6_route_rows": 0,
    "cap_eff": 0,
    "seccomp": 2,
    "memory_max": FIXED_MEMORY_MAX,
    "memory_swap_max": FIXED_MEMORY_SWAP_MAX,
    "pids_max": FIXED_PIDS_MAX,
}
RECORD_FIXED_TEXT: dict[str, str] = {"cpu_max": FIXED_CPU_MAX}
# helper_exit_code is int (a reaped child exit status, e.g. 1 for the literal
# refusal publish path) or None (never started / not reaped); never bool/float.
RECORD_CODE_FIELD = "helper_exit_code"
CODE_LOW, CODE_HIGH = -128, 255
RECORD_ALL_FIELDS = frozenset(
    RECORD_BOOL_FIELDS
    | RECORD_STR_FIELDS
    | RECORD_LIST_FIELDS
    | RECORD_INT_FIELDS
    | {RECORD_CODE_FIELD, "status"}
)
HASH_FIELD_RE = re.compile(r"[0-9a-f]{64}\Z")
MANIFEST_KEYS = frozenset(
    {
        "payload_sha256",
        "core_sha256",
        "dns_sha256",
        "helper_sha256",
        "fixture_sha256",
    }
)


class RecordRefused(ValueError):
    """The parent-side record validator refuses schema-invalid child output."""


def _no_float(value: str) -> object:
    raise RecordRefused("record refused")


def _no_constant(value: str) -> object:
    raise RecordRefused("record refused")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RecordRefused("record refused")
        result[key] = value
    return result


def _trusted_text(data: bytes, label: str) -> str:
    if type(data) is not bytes:
        raise ValueError(f"trusted {label} input must be raw bytes")
    if not data:
        raise ValueError(f"trusted {label} input must not be empty")
    return data.decode("utf-8")  # strict UTF-8; UnicodeDecodeError bubbles


def make_payload(core: bytes, dns: bytes) -> bytes:
    """Strict fixed JSON bundle of trusted bytes; total <= 128 KiB.

    Returns one strict, immutable JSON bundle with exactly three string fields
    (the trusted core.py/dns.py bytes and the fixed FIXTURE_SOURCE literal), no
    caller paths, no archive semantics and no changeable extras. Raises
    ValueError for any non-UTF-8 trusted input or for cap overflow.
    """
    if type(core) is not bytes or type(dns) is not bytes:
        raise ValueError("trusted inputs must be raw bytes")
    # Bound the RAW input lengths BEFORE any decode/allocation work.
    if len(core) > PAYLOAD_MAX or len(dns) > PAYLOAD_MAX:
        raise ValueError("trusted input exceeds the 128 KiB payload cap")
    bundle = {
        PAYLOAD_CORE_KEY: _trusted_text(core, "core"),
        PAYLOAD_DNS_KEY: _trusted_text(dns, "dns"),
        PAYLOAD_FIXTURE_KEY: FIXTURE_SOURCE,
    }
    if type(bundle) is not dict or set(bundle) != PAYLOAD_KEYS:
        raise RecordRefused("record refused")
    raw = json.dumps(bundle, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    encoded = raw.encode("utf-8")
    if len(encoded) <= PAYLOAD_MAX:
        return encoded
    raise ValueError("trusted bundle exceeds the 128 KiB payload cap")


def expected_hashes(core: bytes, dns: bytes) -> Mapping[str, str]:
    """Exact hashes of payload/modules/helper — the parent-side manifest.

    helper is the DNS helper subprocess source text (HELPER_SOURCE, inside the
    unchanged trusted dns.py). Returns a readonly mapping. A hash match is
    byte-provenance only and never claims the trusted source bytes are
    exec-qualified; containment qualification remains the parent's separate
    image/HostConfig/entrypoint inspection plus the record's own gates.
    """
    return MappingProxyType(
        {
            "payload_sha256": hashlib.sha256(make_payload(core, dns)).hexdigest(),
            "core_sha256": hashlib.sha256(core).hexdigest(),
            "dns_sha256": hashlib.sha256(dns).hexdigest(),
            "helper_sha256": hashlib.sha256(HELPER_SOURCE.encode("utf-8")).hexdigest(),
            "fixture_sha256": hashlib.sha256(FIXTURE_SOURCE.encode("utf-8")).hexdigest(),
        }
    )


def build_exec_argv(python_executable: str) -> tuple[str, ...]:
    """Pure qualified argv: no PATH search, no shell, no site, unbuffered.

    argv[0] is a qualified absolute interpreter path; the fixed flag vector is
    ``-I -S -u -c`` with BOOTSTRAP_SOURCE as the single program argument. The
    future driver must terminate stdin after writing make_payload() bytes and
    attach the payload on stdin. Pure: no execution of any kind.
    """
    if type(python_executable) is not str:
        raise ValueError("qualified absolute Python executable required")
    if python_executable == "" or not os.path.isabs(python_executable):
        raise ValueError("qualified absolute Python executable required")
    if "\x00" in python_executable or len(python_executable) > 4096:
        raise ValueError("qualified absolute Python executable required")
    return (python_executable, "-I", "-S", "-u", "-c", BOOTSTRAP_SOURCE)


def validate_record(raw: bytes, manifest: Mapping[str, str]) -> Mapping[str, object]:
    """Parent-side strict single-record validation; returns a readonly mapping.

    Accepts EXACTLY one bounded (<= 8 KiB) strict-UTF-8 flat record: duplicate
    keys, extra/missing fields, floats, non-JSON constants, bad bools/strings/
    counters and any hash-provenance mismatch against the trusted manifest are
    refused with one fixed message. For status ``ok`` the schema additionally
    requires dns_failure=True, cleanup_failed=False and a fully zeroed,
    equality-confirmed actor state, so a child ``ok`` claim is schema-valid
    only when the fixture itself observed the ordinary DNS refusal plus a
    verified cleanup.

    LIMITATION — an ``ok`` record never qualifies containment on its own: this
    validator is a shape/claim parser, not an enforcement engine. The parent
    must still independently inspect image, HostConfig, entrypoint scrub and
    environment before any execution; the fixture's verified DNS refusal
    qualifies only native helper start/refusal/cleanup, not public DNS success
    or SSRF containment.
    """
    if type(raw) is not bytes or not 0 < len(raw) <= OUTPUT_RECORD_MAX:
        raise RecordRefused("record refused")
    try:
        text = raw.decode("utf-8")  # strict UTF-8
        record = json.loads(
            text, object_pairs_hook=_pairs, parse_float=_no_float, parse_constant=_no_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise RecordRefused("record refused") from None
    except RecordRefused:
        raise
    if type(record) is not dict:
        raise RecordRefused("record refused")
    if set(record) == {"status"}:  # minimal fixed abort record
        if record["status"] != STATUS_FAILURE:
            raise RecordRefused("record refused")
        return MappingProxyType({"status": STATUS_FAILURE})
    if set(record) != RECORD_ALL_FIELDS:
        raise RecordRefused("record refused")
    status = record.get("status")
    if type(status) is not str or status not in STATUS_SET:
        raise RecordRefused("record refused")
    if set(manifest) != MANIFEST_KEYS:
        raise RecordRefused("record refused")
    for key, value in manifest.items():
        if not _hex256(value):
            raise RecordRefused("record refused")
        if record[key] != value or not _hex256(record[key]):
            raise RecordRefused("record refused")
    for key in RECORD_BOOL_FIELDS:
        if type(record[key]) is not bool:
            raise RecordRefused("record refused")
    for key, expected in RECORD_FIXED_LISTS.items():
        value = record[key]
        if type(value) is not list or value != expected:
            raise RecordRefused("record refused")
    if not _bounded_ascii(record["interpreter"], 16):
        raise RecordRefused("record refused")
    for key in ("python_exe", "stdlib_location"):
        value = record[key]
        if not _bounded_ascii(value, 4096) or not value.startswith("/"):
            raise RecordRefused("record refused")
    for key in RECORD_INT_FIELDS:
        value = record[key]
        if type(value) is not int or not 0 <= value <= 0x7FFFFFFF:
            raise RecordRefused("record refused")
    for key, expected in RECORD_FIXED_INTS.items():
        if key not in {"memory_max", "memory_swap_max", "pids_max"} and record[key] != expected:
            raise RecordRefused("record refused")
    code = record[RECORD_CODE_FIELD]
    if code is None:
        pass
    elif type(code) is not int or not CODE_LOW <= code <= CODE_HIGH:
        raise RecordRefused("record refused")
    unsupported = record["status"] == STATUS_UNSUPPORTED
    if unsupported:
        # cgroup v2 was unverifiable: the record may not claim a started
        # helper, a waited/exit-code child or finishes; fail closed.
        if (
            type(record["cpu_max"]) is not str
            or record["cpu_max"] != ""
            or any(record[key] != 0 for key in ("memory_max", "memory_swap_max", "pids_max"))
            or record["helper_started"] is not False
            or record[RECORD_CODE_FIELD] is not None
            or record["dns_failure"] is not False
            or record["cleanup_failed"] is not False
            or any(
                record[key] is not True
                for key in ("flags_isolated", "flags_no_site", "flags_unbuffered", "no_new_privs")
            )
            or record["actor_jobs"] != 0
            or record["actor_tasks"] != 0
            or record["actor_processes"] != 0
            or record["fds_equal"] is not True
            or record["tasks_equal"] is not True
            or record["fds_after"] != record["fds_before"]
            or record["tasks_after"] != record["tasks_before"]
        ):
            raise RecordRefused("record refused")
        return MappingProxyType(dict(record))
    # ok/failure records must carry the verified allocation evidence.
    for key, expected in RECORD_FIXED_INTS.items():
        if type(record[key]) is not int or record[key] != expected:
            raise RecordRefused("record refused")
    if type(record["cpu_max"]) is not str or record["cpu_max"] != FIXED_CPU_MAX:
        raise RecordRefused("record refused")
    if record["status"] == STATUS_OK:
        if (
            record["dns_failure"] is not True
            or record["cleanup_failed"] is not False
            or record["helper_started"] is not True
            or record[RECORD_CODE_FIELD] != 1
            or record["flags_isolated"] is not True
            or record["flags_no_site"] is not True
            or record["flags_unbuffered"] is not True
            or record["no_new_privs"] is not True
            or record["fds_equal"] is not True
            or record["tasks_equal"] is not True
            or record["fds_after"] != record["fds_before"]
            or record["tasks_after"] != record["tasks_before"]
            or record["actor_jobs"] != 0
            or record["actor_tasks"] != 0
            or record["actor_processes"] != 0
        ):
            raise RecordRefused("record refused")
    return MappingProxyType(dict(record))


def _hex256(value: object) -> bool:
    return type(value) is str and HASH_FIELD_RE.fullmatch(value) is not None


def _bounded_ascii(value: object, cap: int) -> bool:
    return type(value) is str and 0 < len(value) <= cap and value.isascii()


FIXTURE_SOURCE = r'''
"""Fixture module for the future offline gateway DNS qualification run.

Runs ONLY inside the reviewed offline gateway image (128 MiB memory, no swap,
32 PIDs, half CPU, 16 MiB tmpfs on /tmp, non-root uid 999, network none) with
the trusted module files staged under scripts/ by the BOOTSTRAP program.
Performs the fixed ordered checks before the single trusted DNS helper
attempt through DNSResolver with StdlibSpawner(sys.executable) for the fixed
hostname only, and returns one flat bounded record. Identity/environment/
interface/kernel/proc-status checks are explicit comparisons; cgroup v2
unsupported collapses to the fixed status value BEFORE any helper exists and
refused helpers raise so the parent emits a minimal abort record. aclose()
runs on every run ending and owned cleanup must complete with zero
jobs/tasks/processes, cleanup_failed False, and equal before/after fd and
event-loop task sets. The record carries no raw exception or secret text and
every helper is an explicit comparison, never assert. Unbuffered evidence is
the public stdout layered-IO contract (binary io.FileIO and text-wrapper
write_through True); -u remains a pinned bootstrap argv value.
"""

import asyncio
import hashlib
import io
import os
import sys

from scripts.web_fetch_pilot_dns import (
    DNSFailure,
    DNSResolver,
    HELPER_SOURCE,
    StdlibSpawner,
)

HOST = "fixture.invalid"
CTX_KEYS = ("payload_sha256",)
UID_EXPECTED = 999
PYTHON_EXE_PLANNED = "/usr/local/bin/python"
PROC_FD = "/proc/self/fd"
PROC_ROUTE = "/proc/net/route"
PROC_IF_INET6 = "/proc/net/if_inet6"
PROC_IPV6_ROUTE = "/proc/net/ipv6_route"
PROC_STATUS = "/proc/self/status"
PROC_CGROUP = "/proc/self/cgroup"
SYS_NET = "/sys/class/net"
CGROUP_BASE = "/sys/fs/cgroup"
CGROUP_V2_ROOT = "0::/"
CGROUP_FILES = ("memory.max", "memory.swap.max", "pids.max", "cpu.max")
CPU_MAX_EXPECTED = "50000 100000"
MEMORY_MAX_EXPECTED = 134217728
MEMORY_SWAP_MAX_EXPECTED = 0
PIDS_MAX_EXPECTED = 32
INTERFACES_EXPECTED = ("lo",)
ENV_KEYS_EXPECTED = ("LANG", "PATH")
LANG_VALUE_EXPECTED = "C.UTF-8"
STAGE_CORE_NAME = "web_fetch_pilot_core.py"
STAGE_DNS_NAME = "web_fetch_pilot_dns.py"
FIXTURE_NAME = "fixture.py"
READ_CAP = 65536
FILE_CAP = 131072
PATH_CAP = 4096
VERSION_CAP = 16
STATUS_OK = "ok"
STATUS_FAILURE = "failure"
STATUS_UNSUPPORTED = "unsupported"


class _Failure(RuntimeError):
    """Fixed internal refusal; never rendered into the record."""


class _Unsupported(RuntimeError):
    """The cgroup interface is not the fixed v2 form; fail closed."""


def _hex64(value: object) -> bool:
    if type(value) is not str or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value)


def _decimal(value: object) -> int | None:
    if type(value) is not str or not 1 <= len(value) <= 10:
        return None
    if not all(c in "0123456789" for c in value):
        return None
    return int(value)


def _hex_int(value: object) -> int | None:
    if type(value) is not str or not 1 <= len(value) <= 16:
        return None
    if not all(c in "0123456789abcdefABCDEF" for c in value):
        return None
    return int(value, 16)


def _read_bytes(path: str, cap: int) -> bytes:
    with open(path, "rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise _Failure()
    return data


def _read_ascii_rows(path: str, cap: int) -> list[str]:
    return _read_bytes(path, cap).decode("ascii", "strict").split("\n")


def _sha_file(path: str, cap: int) -> str:
    return hashlib.sha256(_read_bytes(path, cap)).hexdigest()


def _fds() -> frozenset[int]:
    handle = os.open(PROC_FD, os.O_RDONLY | os.O_DIRECTORY)
    try:
        entries = frozenset(int(name) for name in os.listdir(handle))
    finally:
        os.close(handle)
    return entries - {handle}


def _tasks() -> frozenset[asyncio.Task[object]]:
    current = asyncio.current_task()
    if current is None:
        raise _Failure()
    return frozenset(t for t in asyncio.all_tasks() if t is not current)


def _identity() -> dict[str, object]:
    uid = os.getuid()
    executable = sys.executable
    version = ".".join(str(part) for part in sys.version_info[:3])
    stdlib_dir = os.path.dirname(os.__file__)
    if type(executable) is not str or not os.path.isabs(executable):
        raise _Failure()
    if type(stdlib_dir) is not str or not os.path.isabs(stdlib_dir):
        raise _Failure()
    if len(executable) > PATH_CAP or len(stdlib_dir) > PATH_CAP:
        raise _Failure()
    if len(version) > VERSION_CAP or not version.isascii():
        raise _Failure()
    if uid != UID_EXPECTED or executable != PYTHON_EXE_PLANNED:
        raise _Failure()
    return {"uid": uid, "interpreter": version, "python_exe": executable,
            "stdlib_location": stdlib_dir}


def _env_names() -> list[str]:
    names = sorted(os.environ)
    if tuple(names) != ENV_KEYS_EXPECTED:
        raise _Failure()
    # Exact fixed names plus the LANG value contract; PATH stays unrecorded
    # (names only in the record, no ambient proxy/credential values).
    if os.environ.get("LANG", "") != LANG_VALUE_EXPECTED:
        raise _Failure()
    return names


def _flags() -> dict[str, bool]:
    # -u removes binary buffering; write_through belongs to the text wrapper.
    # Unexpected wrappers fail closed. The launcher separately pins -u.
    binary_stdout = getattr(sys.stdout, "buffer", None)
    write_through = getattr(sys.stdout, "write_through", None)
    isolated = sys.flags.isolated == 1
    no_site = sys.flags.no_site == 1
    unbuffered = (
        type(binary_stdout) is io.FileIO
        and write_through is True
    )
    if not isolated or not no_site or not unbuffered:
        raise _Failure()
    return {"flags_isolated": isolated, "flags_no_site": no_site,
            "flags_unbuffered": unbuffered}


def _interfaces() -> list[str]:
    names = sorted(os.listdir(SYS_NET))
    if tuple(names) != INTERFACES_EXPECTED:
        raise _Failure()
    return names


def _staged_hashes() -> dict[str, str]:
    here = os.path.dirname(os.path.abspath(__file__))
    return {
        "core_sha256": _sha_file(os.path.join(here, STAGE_CORE_NAME), FILE_CAP),
        "dns_sha256": _sha_file(os.path.join(here, STAGE_DNS_NAME), FILE_CAP),
        "helper_sha256": hashlib.sha256(HELPER_SOURCE.encode("utf-8")).hexdigest(),
        "fixture_sha256": _sha_file(os.path.join(here, FIXTURE_NAME), FILE_CAP),
    }


def _route_counts() -> tuple[int, int, int]:
    rows = _read_ascii_rows(PROC_ROUTE, READ_CAP)
    if not rows or not rows[0].startswith("Iface"):
        raise _Failure()
    ipv4_rows = sum(1 for row in rows[1:] if row)
    ipv6_address_rows = 0
    # /proc/net/if_inet6 is the ADDRESS inventory, never a route table.
    for row in _read_ascii_rows(PROC_IF_INET6, READ_CAP):
        fields = row.split()
        if fields and fields[-1] != "lo":
            ipv6_address_rows += 1
    # Explicit bounded IPv6 ROUTE evidence from the kernel route file.
    ipv6_route_rows = 0
    for row in _read_ascii_rows(PROC_IPV6_ROUTE, READ_CAP):
        fields = row.split()
        if fields and fields[-1] != "lo":
            ipv6_route_rows += 1
    return ipv4_rows, ipv6_address_rows, ipv6_route_rows


def _proc_status() -> dict[str, object]:
    cap_eff = None
    no_new_privs = None
    seccomp = None
    for row in _read_ascii_rows(PROC_STATUS, READ_CAP):
        parts = row.split(":", 1)
        if len(parts) != 2:
            continue
        key, value = parts[0], parts[1].strip()
        if key == "CapEff":
            cap_eff = _hex_int(value)
        elif key == "NoNewPrivs":
            no_new_privs = value == "1"
        elif key == "Seccomp":
            seccomp = _decimal(value)
    if cap_eff is None or no_new_privs is None or seccomp is None:
        raise _Failure()
    if cap_eff != 0 or no_new_privs is not True or seccomp != 2:
        raise _Failure()
    return {"cap_eff": cap_eff, "no_new_privs": no_new_privs, "seccomp": seccomp}


def _cgroup() -> dict[str, object]:
    rows = [row for row in _read_ascii_rows(PROC_CGROUP, READ_CAP) if row]
    if len(rows) != 1 or rows[0] != CGROUP_V2_ROOT:
        raise _Unsupported()
    values: dict[str, object] = {}
    for cgroup_name in CGROUP_FILES:
        entries = [row for row in
                   _read_ascii_rows(os.path.join(CGROUP_BASE, cgroup_name), READ_CAP)
                   if row]
        if len(entries) != 1:
            raise _Unsupported()
        if cgroup_name == "cpu.max":
            if entries[0] != CPU_MAX_EXPECTED:
                raise _Failure()
            values["cpu_max"] = entries[0]
            continue
        parsed = _decimal(entries[0])
        if parsed is None:
            raise _Unsupported()
        values[cgroup_name.replace(".", "_")] = parsed
    if (values["memory_max"] != MEMORY_MAX_EXPECTED
            or values["memory_swap_max"] != MEMORY_SWAP_MAX_EXPECTED
            or values["pids_max"] != PIDS_MAX_EXPECTED):
        raise _Failure()
    return values


def _ctx_sha(ctx: object) -> str:
    if type(ctx) is not dict or set(ctx) != set(CTX_KEYS):
        raise _Failure()
    payload_sha256 = ctx["payload_sha256"]
    if not _hex64(payload_sha256):
        raise _Failure()
    return payload_sha256


async def _dns() -> dict[str, object]:
    # One wrapped factory over the actual StdlibSpawner; the wrapper adds no
    # unowned spawn/cancel handle and publishes the real process reference
    # immediately after the single real await (no intervening await).
    spawner = StdlibSpawner(sys.executable)
    published: list[object] = []

    async def factory(host: str) -> object:
        process = await spawner(host)
        published.append(process)
        return process

    resolver = DNSResolver(factory)
    dns_failure = False
    cleanup_failed = False
    try:
        try:
            await resolver(HOST)
        except DNSFailure:
            dns_failure = True
        else:
            raise _Failure()
    finally:
        try:
            await resolver.aclose()
        except Exception:
            cleanup_failed = True
        if (resolver.active_jobs != 0 or resolver.pending_tasks != ()
                or resolver.owned_processes != ()):
            cleanup_failed = True
    if resolver.cleanup_failed:
        cleanup_failed = True
    helper_started = len(published) == 1
    helper_exit_code = published[0].returncode if helper_started else None
    if helper_exit_code is not None and type(helper_exit_code) is not int:
        cleanup_failed = True
    return {"dns_failure": dns_failure, "cleanup_failed": cleanup_failed,
            "helper_started": helper_started,
            "helper_exit_code": helper_exit_code,
            "actor_jobs": resolver.active_jobs,
            "actor_tasks": len(resolver.pending_tasks),
            "actor_processes": len(resolver.owned_processes)}


async def main(ctx: dict[str, object]) -> dict[str, object]:
    payload_sha256 = _ctx_sha(ctx)
    identity = _identity()
    flags = _flags()
    env_keys = _env_names()
    interfaces = _interfaces()
    ipv4_rows, ipv6_address_rows, ipv6_route_rows = _route_counts()
    # spelled-out pre-helper containment preconditions: nonzero IPv4 route
    # rows or non-loopback IPv6 ADDRESS/route rows refuse before any child
    # helper can exist (record validation alone cannot preflight this).
    if ipv4_rows != 0 or ipv6_address_rows != 0 or ipv6_route_rows != 0:
        raise _Failure()
    proc_fields = _proc_status()
    staged = _staged_hashes()
    before_fds = _fds()
    before_tasks = _tasks()
    try:
        limits = _cgroup()
    except _Unsupported:
        limits = {}
        return {
            "status": STATUS_UNSUPPORTED,
            "payload_sha256": payload_sha256,
            "core_sha256": staged["core_sha256"],
            "dns_sha256": staged["dns_sha256"],
            "helper_sha256": staged["helper_sha256"],
            "fixture_sha256": staged["fixture_sha256"],
            "uid": identity["uid"],
            "interpreter": identity["interpreter"],
            "python_exe": identity["python_exe"],
            "stdlib_location": identity["stdlib_location"],
            "env_keys": env_keys,
            "interfaces": interfaces,
            "ipv4_route_rows": ipv4_rows,
            "ipv6_address_rows": ipv6_address_rows,
            "ipv6_route_rows": ipv6_route_rows,
            "cap_eff": proc_fields["cap_eff"],
            "no_new_privs": proc_fields["no_new_privs"],
            "seccomp": proc_fields["seccomp"],
            "memory_max": limits.get("memory_max", 0),
            "memory_swap_max": limits.get("memory_swap_max", 0),
            "pids_max": limits.get("pids_max", 0),
            "cpu_max": limits.get("cpu_max", ""),
            "fds_before": len(before_fds),
            "fds_after": len(before_fds),
            "fds_equal": True,
            "tasks_before": len(before_tasks),
            "tasks_after": len(before_tasks),
            "tasks_equal": True,
            "actor_jobs": 0,
            "actor_tasks": 0,
            "actor_processes": 0,
            "dns_failure": False,
            "cleanup_failed": False,
            "helper_started": False,
            "helper_exit_code": None,
            "flags_isolated": flags["flags_isolated"],
            "flags_no_site": flags["flags_no_site"],
            "flags_unbuffered": flags["flags_unbuffered"],
        }
    dns_values = await _dns()
    after_fds = _fds()
    after_tasks = _tasks()
    fds_equal = before_fds == after_fds
    tasks_equal = before_tasks == after_tasks
    complete = (
        dns_values["dns_failure"] is True
        and dns_values["cleanup_failed"] is False
        and dns_values["helper_started"] is True
        and dns_values["helper_exit_code"] == 1
        and dns_values["actor_jobs"] == 0
        and dns_values["actor_tasks"] == 0
        and dns_values["actor_processes"] == 0
        and fds_equal is True and tasks_equal is True
    )
    return {
        "status": STATUS_OK if complete else STATUS_FAILURE,
        "payload_sha256": payload_sha256,
        "core_sha256": staged["core_sha256"],
        "dns_sha256": staged["dns_sha256"],
        "helper_sha256": staged["helper_sha256"],
        "fixture_sha256": staged["fixture_sha256"],
        "uid": identity["uid"],
        "interpreter": identity["interpreter"],
        "python_exe": identity["python_exe"],
        "stdlib_location": identity["stdlib_location"],
        "env_keys": env_keys,
        "interfaces": interfaces,
        "ipv4_route_rows": ipv4_rows,
        "ipv6_address_rows": ipv6_address_rows,
        "ipv6_route_rows": ipv6_route_rows,
        "cap_eff": proc_fields["cap_eff"],
        "no_new_privs": proc_fields["no_new_privs"],
        "seccomp": proc_fields["seccomp"],
        "memory_max": limits["memory_max"],
        "memory_swap_max": limits["memory_swap_max"],
        "pids_max": limits["pids_max"],
        "cpu_max": limits["cpu_max"],
        "fds_before": len(before_fds),
        "fds_after": len(after_fds),
        "fds_equal": fds_equal,
        "tasks_before": len(before_tasks),
        "tasks_after": len(after_tasks),
        "tasks_equal": tasks_equal,
        "actor_jobs": dns_values["actor_jobs"],
        "actor_tasks": dns_values["actor_tasks"],
        "actor_processes": dns_values["actor_processes"],
        "dns_failure": dns_values["dns_failure"],
        "cleanup_failed": dns_values["cleanup_failed"],
        "helper_started": dns_values["helper_started"],
        "helper_exit_code": dns_values["helper_exit_code"],
        "flags_isolated": flags["flags_isolated"],
        "flags_no_site": flags["flags_no_site"],
        "flags_unbuffered": flags["flags_unbuffered"],
    }
'''

BOOTSTRAP_SOURCE = r'''
"""Future container bootstrap; never executed on this host by this module.

The future reviewed driver launches this literal as the single -c program of a
qualified absolute interpreter (``-I -S -u -c BOOTSTRAP_SOURCE``) with the
trusted make_payload() bytes on stdin and closes stdin. It reads ONLY those
bounded bytes, validates the fixed three-field bundle schema (no extra fields,
no caller paths, no archive semantics), hashes the exact received bytes and
stages the owned fixed-name module files under the owned /tmp tmpfs root as
package scripts. It imports the fixed fixture module by a static import, runs
its async main() guarded by a fixed 8-second ceiling, and emits the fixture's
own record as one bounded flat JSON line. Failure paths print the fixed
minimal abort record and exit 1; stderr is never written at all. Cleanup via
the owned TemporaryDirectory happens even after refusal.
"""

import asyncio
import hashlib
import json
import os
import sys
import tempfile

PAYLOAD_KEY_CORE = "core.py"
PAYLOAD_KEY_DNS = "dns.py"
PAYLOAD_KEY_FIXTURE = "FIXTURE_SOURCE"
PAYLOAD_KEYS = frozenset({PAYLOAD_KEY_CORE, PAYLOAD_KEY_DNS, PAYLOAD_KEY_FIXTURE})
PAYLOAD_KEY_TO_STAGE_NAME = {
    PAYLOAD_KEY_CORE: "web_fetch_pilot_core.py",
    PAYLOAD_KEY_DNS: "web_fetch_pilot_dns.py",
    PAYLOAD_KEY_FIXTURE: "fixture.py",
}
STAGE_DIR_NAME = "scripts"
PAYLOAD_CAP = 131072
FILE_CAP = 131072
RECORD_CAP = 8192
MAIN_TIMEOUT = 8.0
FIXTURE_ENTRY = "scripts.fixture"
ALLOWED_ROOT_PREFIX = "/tmp/"
CTX_KEYS = ("payload_sha256",)
FIXED_FAILURE_RECORD = {"status": "failure"}
RECORD_STATUS_OK = "ok"
RECORD_STR_KEYS = frozenset({
    "interpreter", "python_exe", "stdlib_location", "cpu_max",
    "core_sha256", "dns_sha256", "helper_sha256", "payload_sha256",
    "fixture_sha256",
})
RECORD_BOOL_KEYS = frozenset(
    {
        "dns_failure",
        "flags_isolated",
        "flags_no_site",
        "flags_unbuffered",
        "no_new_privs",
        "fds_equal",
        "tasks_equal",
        "cleanup_failed",
        "helper_started",
    }
)
RECORD_LIST_KEYS = frozenset({"env_keys", "interfaces"})
RECORD_INT_KEYS = frozenset(
    {
        "uid",
        "ipv4_route_rows",
        "ipv6_address_rows",
        "ipv6_route_rows",
        "cap_eff",
        "seccomp",
        "memory_max",
        "memory_swap_max",
        "pids_max",
        "fds_before",
        "fds_after",
        "tasks_before",
        "tasks_after",
        "actor_jobs",
        "actor_tasks",
        "actor_processes",
    }
)
RECORD_CODE_KEY = "helper_exit_code"
CODE_LOW, CODE_HIGH = -128, 255
RECORD_ALL_KEYS = frozenset(
    RECORD_STR_KEYS
    | RECORD_BOOL_KEYS
    | RECORD_LIST_KEYS
    | RECORD_INT_KEYS
    | {RECORD_CODE_KEY, "status"}
)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("record refused")
        result[key] = value
    return result


def _no_float(value: str) -> object:
    raise ValueError("record refused")


def _no_constant(value: str) -> object:
    raise ValueError("record refused")


def _read_payload() -> tuple[bytes, dict[str, str]]:
    raw = sys.stdin.buffer.read(PAYLOAD_CAP + 1)
    if sys.stdin.buffer.read(1):
        raise ValueError("record refused")
    if len(raw) > PAYLOAD_CAP:
        raise ValueError("record refused")
    text = raw.decode("utf-8")
    value = json.loads(text, object_pairs_hook=_pairs, parse_float=_no_float,
                       parse_constant=_no_constant)
    if type(value) is not dict or set(value) != PAYLOAD_KEYS:
        raise ValueError("record refused")
    for key, text_value in value.items():
        if type(text_value) is not str or not text_value:
            raise ValueError("record refused")
    return raw, value


def _write_file(path: str, data: bytes) -> None:
    if len(data) > FILE_CAP:
        raise ValueError("record refused")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _stage(root: str, payload: dict[str, str]) -> None:
    os.makedirs(os.path.join(root, STAGE_DIR_NAME), 0o755)
    with open(os.path.join(root, STAGE_DIR_NAME, "__init__.py"), "wb") as handle:
        handle.write(b"")
    for key in (PAYLOAD_KEY_CORE, PAYLOAD_KEY_DNS, PAYLOAD_KEY_FIXTURE):
        staged = os.path.join(root, STAGE_DIR_NAME,
                              PAYLOAD_KEY_TO_STAGE_NAME[key])
        _write_file(staged, payload[key].encode("utf-8"))


def _structural(record: object) -> bool:
    if type(record) is not dict:
        return False
    keys = set(record)
    if keys == {"status"}:
        return record["status"] == "failure" and type(record["status"]) is str
    if keys != RECORD_ALL_KEYS:
        return False
    if record["status"] not in ("ok", "failure", "unsupported"):
        return False
    for key in RECORD_STR_KEYS:
        if type(record[key]) is not str:
            return False
    for key in RECORD_BOOL_KEYS:
        if type(record[key]) is not bool:
            return False
    for key in RECORD_LIST_KEYS:
        value = record[key]
        if type(value) is not list or not value:
            return False
        if not all(type(item) is str and 0 < len(item) <= 64 and item.isascii()
                   for item in value):
            return False
    for key in RECORD_INT_KEYS:
        value = record[key]
        if type(value) is not int or not 0 <= value <= 0x7FFFFFFF:
            return False
    code = record[RECORD_CODE_KEY]
    if code is None:
        return True
    return type(code) is int and CODE_LOW <= code <= CODE_HIGH


def _invoke(root: str, ctx: dict[str, str]) -> object:
    sys.path.insert(0, root)
    import scripts.fixture as fixture_mod
    return asyncio.run(asyncio.wait_for(fixture_mod.main(ctx), timeout=MAIN_TIMEOUT))


def main() -> int:
    try:
        raw, payload = _read_payload()
    except Exception:
        sys.stdout.write('{"status":"failure"}\n')
        return 1
    try:
        with tempfile.TemporaryDirectory(prefix="wfp-dns-qual-", dir="/tmp") as root_name:
            root = os.path.realpath(root_name)
            if not root.startswith(ALLOWED_ROOT_PREFIX):
                sys.stdout.write('{"status":"failure"}\n')
                return 1
            try:
                _stage(root, payload)
                record = _invoke(root, {"payload_sha256":
                                        hashlib.sha256(raw).hexdigest()})
            except BaseException:
                sys.stdout.write('{"status":"failure"}\n')
                return 1
            if not _structural(record):
                sys.stdout.write('{"status":"failure"}\n')
                return 1
            out = json.dumps(record, sort_keys=True, ensure_ascii=True,
                             separators=(",", ":")).encode("utf-8")
            if len(out) > RECORD_CAP:
                sys.stdout.write('{"status":"failure"}\n')
                return 1
            sys.stdout.write(out.decode("ascii"))
            sys.stdout.write("\n")
            return 0 if record["status"] == "ok" else 1
    except BaseException:
        sys.stdout.write('{"status":"failure"}\n')
        return 1


if __name__ == "__main__":
    sys.exit(main())
'''
