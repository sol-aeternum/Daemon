"""One production-only #490 experiment; public output is reconstructed, not copied.

Raw test/build output, screenshots and traces stay in separate private VM paths.
This is reviewed same-repository code, not a sandbox for hostile PR programs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
FRONT = ROOT / "frontend"
CONTROL = FRONT / "test-results/issue490-control"
PUBLIC = FRONT / "test-results/issue490-public"
MANIFEST = FRONT / "e2e/issue490-cases.json"
BRANCH = "investigate/490-ci-fps-20261115"
COMMAND = [
    "npm",
    "run",
    "test:browser",
    "--",
    "--config=playwright.production.config.ts",
    "--workers=4",
    "--reporter=./e2e/issue490-reporter.ts",
    "--output=test-results/issue490-browser",
    "--update-snapshots=none",
]
STATUSES = {"passed", "failed", "timedOut", "skipped", "interrupted"}
WINDOW_KEYS = {
    "start_epoch_ms",
    "end_epoch_ms",
    "start_monotonic_ms",
    "end_monotonic_ms",
    "fps",
}


def child_environment() -> dict[str, str]:
    # Outside-in allowlist, never enumerate or print environment/credential files.
    result = {
        key: os.environ[key]
        for key in (
            "PATH",
            "HOME",
            "TMPDIR",
            "TMP",
            "TEMP",
            "PYTHONPYCACHEPREFIX",
        )
        if key in os.environ
    }
    result.update(CI="1", NEXT_PUBLIC_API_URL="http://127.0.0.1:3181")
    return result


def eligible(event: str, action: str, branch: str, same_repo: bool, attempt: int) -> bool:
    return (event, action, branch, same_repo, attempt) == (
        "pull_request",
        "opened",
        BRANCH,
        True,
        1,
    )


def number(value: object, minimum: float = 0, maximum: float = 2**63) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return math.isfinite(value) and minimum <= value <= maximum


def integer(value: object, minimum: int = 0, maximum: int = 2**63) -> bool:
    return type(value) is int and minimum <= value <= maximum


def fields(value: object, names: set[str]) -> dict:
    if type(value) is not dict or set(value) != names:
        raise ValueError("schema")
    return value


def read_json(filename: Path, root: Path, cap: int = 131_072):
    if filename.resolve() != filename.absolute() or not filename.is_relative_to(root):
        raise ValueError("path")
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        import stat

        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            raise ValueError("size")
        data = stream.read(cap + 1)
    if len(data) > cap:
        raise ValueError("size")
    return json.loads(data)


def validate_window(value):
    data = fields(value, WINDOW_KEYS)
    if not all(number(v) for v in data.values()):
        raise ValueError("window")
    if not (0 < data["fps"] < 10_000):
        raise ValueError("window")
    for prefix in ("epoch", "monotonic"):
        delta = data[f"end_{prefix}_ms"] - data[f"start_{prefix}_ms"]
        if not 0 < delta < 30_000:
            raise ValueError("window")
    return {key: data[key] for key in sorted(WINDOW_KEYS)}


def finalize_results(candidate, manifest: list, browser_exit: int | None) -> dict:
    """No caller strings survive: identities are mapped back to frozen source."""
    data = fields(
        candidate,
        {
            "schema",
            "workers",
            "retries",
            "indices",
            "attempts",
            "global_errors",
            "reporter_errors",
            "status",
        },
    )
    if data["schema"] != 1 or type(data["schema"]) is not int:
        raise ValueError("schema")
    if type(data["indices"]) is not list or not all(
        integer(i, 0, len(manifest) - 1) for i in data["indices"]
    ):
        raise ValueError("inventory")
    if sorted(data["indices"]) != list(range(len(manifest))):
        raise ValueError("inventory")
    if type(data["workers"]) is not int or data["workers"] != 4:
        raise ValueError("workers")
    if (
        type(data["retries"]) is not list
        or not data["retries"]
        or any(type(r) is not int or r != 1 for r in data["retries"])
    ):
        raise ValueError("retries")
    if not all(integer(data[name], 0, 1000) for name in ("global_errors", "reporter_errors")):
        raise ValueError("errors")
    if data["status"] not in {"passed", "failed", "timedout", "interrupted"}:
        raise ValueError("status")
    if type(data["attempts"]) is not list or len(data["attempts"]) > len(manifest) * 2:
        raise ValueError("attempts")
    attempts = [[] for _ in manifest]
    for item in data["attempts"]:
        item = fields(
            item, {"index", "retry", "status", "duration_ms", "worker", "start_epoch_ms", "window"}
        )
        if not integer(item["index"], 0, len(manifest) - 1) or not integer(item["retry"], 0, 1):
            raise ValueError("attempt")
        if item["status"] not in STATUSES or not integer(item["worker"], 0, 512):
            raise ValueError("attempt")
        if not number(item["duration_ms"], 0, 900_000) or not number(item["start_epoch_ms"]):
            raise ValueError("attempt")
        window = validate_window(item["window"]) if item["window"] is not None else None
        if item["index"] != 5 and window is not None:
            raise ValueError("window_case")
        attempts[item["index"]].append(
            {
                "retry": item["retry"],
                "status": item["status"],
                "duration_ms": item["duration_ms"],
                "worker": item["worker"],
                "start_epoch_ms": item["start_epoch_ms"],
                "window": window,
            }
        )
    codes = []
    cases = []
    for identity, records in zip(manifest, attempts, strict=True):
        records.sort(key=lambda item: item["retry"])
        if not records or [r["retry"] for r in records] != list(range(len(records))):
            codes.append("attempt_coverage")
        if len(records) > 1 and records[0]["status"] not in {"failed", "timedOut"}:
            codes.append("unexpected_retry")
        if any(r["status"] in {"skipped", "interrupted"} for r in records):
            codes.append("incomplete_case")
        cases.append({"identity": identity, "attempts": records})
    for record in attempts[5]:
        if record["window"] is None:
            codes.append("missing_scroll_window")
        elif (record["status"] == "passed") != (record["window"]["fps"] > 30):
            # A later assertion/fixture error may fail despite high FPS: retain
            # failure, never mislabel it an FPS-only failure.
            if record["status"] == "passed":
                codes.append("fps_threshold_mismatch")
    if data["global_errors"] or data["reporter_errors"]:
        codes.append("reporter_or_global_error")
    if browser_exit not in (0, 1):
        codes.append("abnormal_browser_exit")
    all_passed = all(r and r[-1]["status"] == "passed" for r in attempts)
    if (browser_exit == 0) != (data["status"] == "passed" and all_passed):
        codes.append("exit_status_mismatch")
    return {
        "schema": 1,
        "workers": 4,
        "configured_retries": 1,
        "integrity": not codes,
        "codes": sorted(set(codes)),
        "browser_exit": browser_exit,
        "status": data["status"],
        "inventory_passed": not codes and browser_exit == 0 and all_passed,
        "global_errors": data["global_errors"],
        "reporter_errors": data["reporter_errors"],
        "cases": cases,
    }


def cpu_ticks(text: str) -> list[int]:
    line = next(line for line in text.splitlines() if line.startswith("cpu "))
    result = [int(value) for value in line.split()[1:9]]
    if len(result) != 8 or any(value < 0 for value in result):
        raise ValueError("cpu")
    return result


def psi_total(text: str) -> int:
    line = next(line for line in text.splitlines() if line.startswith("some "))
    total = next(field[6:] for field in line.split() if field.startswith("total="))
    value = int(total)
    if value < 0:
        raise ValueError("pressure")
    return value


def current_cgroup() -> Path:
    base = Path("/sys/fs/cgroup").resolve()
    membership = next(
        line[3:]
        for line in Path("/proc/self/cgroup").read_text().splitlines()
        if line.startswith("0::")
    )
    current = (base / membership.lstrip("/")).resolve()
    if not current.is_relative_to(base) or not current.is_dir():
        raise ValueError("cgroup")
    return current


def sample_host() -> dict:
    row = {
        "epoch_ns": time.time_ns(),
        "monotonic_ns": time.monotonic_ns(),
        "cpu_ticks": cpu_ticks(Path("/proc/stat").read_text()),
    }
    for kind in ("cpu", "memory", "io"):
        row[f"{kind}_some_total_us"] = psi_total(Path(f"/proc/pressure/{kind}").read_text())
    memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    for name in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
        row[name] = int(memory[name].split()[0]) * 1024
    row["runnable"] = int(Path("/proc/loadavg").read_text().split()[3].split("/")[0])
    # Current controller group is not asserted exclusive renderer accounting.
    row["cgroup_cpu"] = None
    row["cgroup_cpu_some_total_us"] = None
    try:
        group = current_cgroup()
        values = dict(line.split() for line in (group / "cpu.stat").read_text().splitlines())
        row["cgroup_cpu"] = {
            key: int(values[key]) if key in values else None
            for key in (
                "usage_usec",
                "user_usec",
                "system_usec",
                "nr_periods",
                "nr_throttled",
                "throttled_usec",
            )
        }
        row["cgroup_cpu_some_total_us"] = psi_total((group / "cpu.pressure").read_text())
    except (OSError, ValueError, StopIteration):
        pass
    return validate_host(row)


def validate_host(value) -> dict:
    data = fields(
        value,
        {
            "epoch_ns",
            "monotonic_ns",
            "cpu_ticks",
            "cpu_some_total_us",
            "memory_some_total_us",
            "io_some_total_us",
            "MemTotal",
            "MemAvailable",
            "SwapTotal",
            "SwapFree",
            "runnable",
            "cgroup_cpu",
            "cgroup_cpu_some_total_us",
        },
    )
    if type(data["cpu_ticks"]) is not list or len(data["cpu_ticks"]) != 8:
        raise ValueError("cpu")
    if not all(integer(v) for v in data["cpu_ticks"]):
        raise ValueError("cpu")
    if not all(
        integer(v)
        for k, v in data.items()
        if k not in {"cpu_ticks", "cgroup_cpu", "cgroup_cpu_some_total_us"}
    ):
        raise ValueError("host")
    if data["cgroup_cpu"] is not None:
        cpu = fields(
            data["cgroup_cpu"],
            {
                "usage_usec",
                "user_usec",
                "system_usec",
                "nr_periods",
                "nr_throttled",
                "throttled_usec",
            },
        )
        if not all(value is None or integer(value) for value in cpu.values()):
            raise ValueError("cgroup_cpu")
    if data["cgroup_cpu_some_total_us"] is not None and not integer(
        data["cgroup_cpu_some_total_us"]
    ):
        raise ValueError("cgroup_pressure")
    return {key: data[key] for key in sorted(data)}


def parse_limit(name: str, text: str) -> list[int | None]:
    values = text.split()
    if len(values) != (2 if name == "cpu.max" else 1):
        raise ValueError("limit")
    parsed = [None if token == "max" else int(token) for token in values]
    if any(value is not None and value <= 0 for value in parsed):
        raise ValueError("limit")
    if name == "cpu.max" and parsed[1] is None:
        raise ValueError("limit")
    return parsed


def runner_hardware() -> dict:
    affinity = sorted(os.sched_getaffinity(0))
    quotas = []
    base = Path("/sys/fs/cgroup").resolve()
    current = current_cgroup()
    limits = []
    for depth in range(32):
        item: dict[str, int | list[int | None]] = {"depth": depth}
        for name in ("cpu.max", "memory.max"):
            source = current / name
            if not source.exists():
                continue
            parsed = parse_limit(name, source.read_text())
            item[name] = parsed
            if name == "cpu.max":
                quota, period = parsed
                if quota is not None and period is not None:
                    quotas.append(quota / period)
        limits.append(item)
        if current == base:
            break
        current = current.parent
    else:
        raise ValueError("cgroup_depth")
    cpuinfo = Path("/proc/cpuinfo").read_text()
    model = next(
        (
            line.split(":", 1)[1].strip()
            for line in cpuinfo.splitlines()
            if line.startswith("model name")
        ),
        "unavailable",
    )
    if not re.fullmatch(r"[A-Za-z0-9()@., +_-]{1,128}", model):
        model = "unavailable"
    return {
        "logical_cpus": os.cpu_count(),
        "affinity": affinity,
        "cpu_model": model,
        "ancestor_limits": limits,
        "effective_cpu_ceiling": min([len(affinity), *quotas]),
        "memory_total_bytes": sample_host()["MemTotal"],
    }


def runtime_versions() -> dict:
    versions = {}
    for name, command in (("node", ["node", "--version"]), ("npm", ["npm", "--version"])):
        value = subprocess.run(
            command, env=child_environment(), capture_output=True, text=True, check=True, timeout=15
        ).stdout.strip()
        if not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
            raise ValueError("runtime_version")
        versions[name] = value
    value = json.loads((FRONT / "node_modules/@playwright/test/package.json").read_text())[
        "version"
    ]
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("runtime_version")
    versions["playwright"] = value
    return versions


class HostSampler:
    def __init__(self):
        self.rows = []
        self.errors = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.collect, daemon=True)

    def collect(self):
        try:
            for _ in range(2000):
                self.rows.append(sample_host())
                if self.stop_event.wait(0.5):
                    return
            self.errors += 1
        except (OSError, ValueError, StopIteration):
            self.errors += 1

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive() or len(self.rows) < 2:
            self.errors += 1


def wait_bounded(child, seconds: int) -> dict:
    try:
        return {"exit": child.wait(timeout=seconds), "timeout": False, "unresolved": False}
    except subprocess.TimeoutExpired:
        pass
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            child.send_signal(sig)  # Only our unreaped Popen child, never a group.
        except OSError:
            pass
        try:
            return {"exit": child.wait(timeout=5), "timeout": True, "unresolved": False}
        except subprocess.TimeoutExpired:
            continue
    return {"exit": None, "timeout": True, "unresolved": True}


def run_child(command: list[str], label: str, seconds: int) -> dict:
    with (CONTROL / f"{label}.log").open("x") as output:
        child = subprocess.Popen(
            command, cwd=FRONT, env=child_environment(), stdout=output, stderr=subprocess.STDOUT
        )
        return wait_bounded(child, seconds)


def build_fingerprint() -> dict:
    identifier = (FRONT / ".next/BUILD_ID").read_text().strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
        raise ValueError("build_id")
    hashes = {}
    total = 0
    for directory in (FRONT / ".next/server", FRONT / ".next/static"):
        for count, filename in enumerate(directory.rglob("*")):
            if count > 10_000 or filename.is_symlink():
                raise ValueError("build_paths")
            if not filename.is_file():
                continue
            name = filename.relative_to(FRONT).as_posix()
            if (
                not re.fullmatch(r"[A-Za-z0-9_.@/()+%\[\]~-]+", name)
                or ".." in filename.relative_to(FRONT).parts
            ):
                raise ValueError("build_paths")
            total += filename.stat().st_size
            if total > 2 * 1024**3:
                raise ValueError("build_size")
            hashes[name] = hashlib.sha256(filename.read_bytes()).hexdigest()
    if not hashes:
        raise ValueError("build_empty")
    return {"build_id": identifier, "hashes": hashes}


def export(runner: dict, host: list, results: dict):
    """Only controller-constructed dictionaries and validated scalar data enter."""
    validated = [validate_host(row) for row in host]
    PUBLIC.mkdir(exist_ok=False)
    with (PUBLIC / "runner.json").open("x") as stream:
        json.dump(runner, stream, allow_nan=False, indent=2)
    with (PUBLIC / "results.json").open("x") as stream:
        json.dump(results, stream, allow_nan=False, indent=2)
    with (PUBLIC / "host.jsonl").open("x") as stream:
        for row in validated:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    # Complete only after every final write succeeded; no raw fallback upload.
    (PUBLIC / "complete").write_text("schema-validated-controller-export-v1\n")


def probe(expected_sha: str, run_id: int, attempt: int) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha) or attempt != 1 or run_id < 1:
        raise ValueError("identity")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        env=child_environment(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != expected_sha:
        raise ValueError("head")
    runner = {
        "schema": 1,
        "head": expected_sha,
        "run_id": run_id,
        "run_attempt": attempt,
        "command": COMMAND,
        "production_only": True,
        "browser_invocations": 0,
        "hardware": None,
        "versions": runtime_versions(),
        "suitable": False,
        "codes": [],
    }
    results = {
        "schema": 1,
        "integrity": False,
        "inventory_passed": False,
        "codes": ["not_executed"],
    }
    try:
        runner["hardware"] = runner_hardware()
        sample_host()
    except (OSError, ValueError, StopIteration):
        runner["codes"].append("hardware_or_pressure_unavailable")
        export(runner, [], results)
        return 2
    if runner["hardware"]["effective_cpu_ceiling"] < 4:
        runner["codes"].append("insufficient_cpu")
        export(runner, [], results)
        return 2
    runner["suitable"] = True
    runner["build"] = run_child(["npm", "run", "build"], "build", 1500)
    if runner["build"] != {"exit": 0, "timeout": False, "unresolved": False}:
        runner["codes"].append("build_failed")
        export(runner, [], results)
        return 2
    runner["fingerprint"] = build_fingerprint()
    sampler = HostSampler()
    sampler.thread.start()
    runner["browser_invocations"] = 1
    try:
        runner["browser"] = run_child(COMMAND, "browser", 900)
    finally:
        sampler.stop()
    try:
        manifest = json.loads(MANIFEST.read_text())
        candidate = read_json(CONTROL / "candidate.json", CONTROL)
        results = finalize_results(candidate, manifest, runner["browser"]["exit"])
    except (OSError, ValueError, TypeError, KeyError):
        results = {
            "schema": 1,
            "integrity": False,
            "inventory_passed": False,
            "codes": ["invalid_or_missing_report"],
        }
    runner["sampler_errors"] = sampler.errors
    if sampler.errors:
        runner["codes"].append("host_capture_invalid")
    if runner["browser"]["timeout"] or runner["browser"]["unresolved"]:
        runner["codes"].append("browser_lifetime_invalid")
    if build_fingerprint() != runner["fingerprint"]:
        runner["codes"].append("build_changed")
    export(runner, sampler.rows, results)
    return 0 if results["inventory_passed"] and not runner["codes"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("install", "probe"))
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--run-id", type=int, default=0)
    parser.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.stage == "install":
            CONTROL.mkdir(parents=True, exist_ok=False)
            for command, label in (
                (["npm", "ci", "--userconfig=/dev/null"], "npm-ci"),
                (
                    ["node_modules/.bin/playwright", "install", "--with-deps", "chromium"],
                    "chromium",
                ),
            ):
                if run_child(command, label, 900) != {
                    "exit": 0,
                    "timeout": False,
                    "unresolved": False,
                }:
                    print("issue490: install_failed")
                    return 2
            return 0
        return probe(args.expected_sha, args.run_id, args.attempt)
    except (OSError, ValueError, TypeError, KeyError, StopIteration, subprocess.SubprocessError):
        print("issue490: probe_blocked_or_export_invalid")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
