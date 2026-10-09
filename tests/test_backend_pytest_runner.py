"""Offline synthetic coverage for scripts/run_backend_pytest.sh (issue #454).

Integration tests drive the real production chain (bash wrapper -> GNU timeout ->
``uv run --no-sync`` -> locked venv Python -> pytest) against fictional suites
written into pytest temporary directories. There is no network access, no
production probing, no credential use, and the synthetic children never import
the repository conftest or .env: each case dir carries its own minimal
``pytest.ini``, is passed to pytest explicitly, and runs with a stripped
environment. Subprocess bounds make sure a defective runner can never hang
the outer suite, and timed-out process groups are SIGKILLed before asserting.
Most contract tests substitute an offline uv shim while retaining real pytest;
fractional deadlines use a controlled sleeper to avoid pytest startup timing.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from xml.etree import ElementTree

import pytest

# The runner and its tests are Linux/GNU-coreutils only by design.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="the runner is a Linux/GNU-coreutils shell script"
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_SCRIPT = REPO_ROOT / "scripts" / "run_backend_pytest.sh"

LOG_NAME = "pytest.log"
XML_NAME = "pytest-results.xml"
STATUS_TAG = "pytest-diagnostics:"
DEFAULTS_LINE = "starting pytest (suite-timeout=1200s kill-after=15s faulthandler-timeout=120s"
STDERR_MARKER = "SYNTH_STDERR_REACHED"

FAKEUV_ARGV_VAR = "RUNNER_FAKE_UV_ARGV"
FAKEUV_ENV_VAR = "RUNNER_FAKE_UV_ENV"
PID_MARKER_VAR = "SYNTH_PID_MARKER"

_CACHE_OFF = ("-p", "no:cacheprovider")

_SYSTEM_PATH_DIRS = (
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
)

# ---- fictional synthetic suites ---------------------------------------------

_PASSING_SUITE = f'''\
"""Synthetic passing suite; fictional data only."""
import sys
from pathlib import Path

sys.stderr.write("{STDERR_MARKER}\\n")
sys.stderr.flush()


def test_synthetic_pass(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("ok", encoding="utf-8")
'''

_FAILING_SUITE = '''\
"""Synthetic failing suite used to pin nonzero status propagation."""


def test_synthetic_expected_failure() -> None:
    assert 1 == 2, "synthetic assertion for wrapper status propagation"
'''

_TWO_PASS_SUITE = '''\
"""Two synthetic tests; one is selected with -k by the harness."""


def test_tagged_pass() -> None:
    assert True


def test_other_pass() -> None:
    assert True
'''

_CALL_STALL_SUITE = '''\
"""One test stalls in its call phase but recovers within a bounded time."""
import time


def test_synthetic_self_recovering_call_stall() -> None:
    time.sleep(14)
'''

_TEARDOWN_STALL_SUITE = '''\
"""A teardown fixture stalls after the yield so the watchdog dumps stacks."""
import time

import pytest


@pytest.fixture
def synthetic_teardown_staller() -> None:
    yield
    time.sleep(10)


def test_synthetic_teardown_stall(synthetic_teardown_staller: None) -> None:
    return
'''

_COLLECTION_STALL_SUITE = '''\
"""Module import stalls during pytest collection, before any test protocol."""
import time

time.sleep(60)


def test_never_reached() -> None:
    assert True
'''

_SIGNAL_RESISTANT_SUITE = f'''\
"""Registers an ABRT-resistant child process, then stalls until the deadline.

The child is left in the wrapper's process group and ignores SIGABRT, so GNU
timeout's kill-after SIGKILL is the only step that can remove it.
"""
import os
import signal
import subprocess
import sys
import time


def test_synthetic_signal_resistant_child() -> None:
    ignore_abrt = (
        "import os,signal,time;"
        "signal.signal(signal.SIGABRT, signal.SIG_IGN);"
        "open(os.environ['{PID_MARKER_VAR}'], 'w').write(str(os.getpid()));"
        "time.sleep(120)"
    )
    child = subprocess.Popen([sys.executable, "-c", ignore_abrt])
    assert child.pid > 0
    time.sleep(60)
'''

# ---- helpers ------------------------------------------------------------------


def _real_uv_ready() -> bool:
    return shutil.which("uv") is not None


def _write_case(root: Path, name: str, body: str) -> Path:
    """Create a synthetic case dir with its own minimal root config.

    The inner ``pytest.ini`` keeps rootdir inside the case directory, so the
    timed pytest never discovers repository configuration or conftest.py
    files above it.
    """

    case_dir = root / name
    case_dir.mkdir(parents=True, exist_ok=True)
    # `-s` disables pytest's capture for the synthetic suites, so module-level
    # stderr markers reach the real fd 2 and prove combined logging end to end.
    (case_dir / "pytest.ini").write_text("[pytest]\naddopts = -s\n", encoding="utf-8")
    (case_dir / "test_synthetic.py").write_text(body, encoding="utf-8")
    return case_dir


def _synthetic_env(
    tmp: Path,
    *,
    fake_uv_dir: Path | None = None,
    plugin_autoload: bool = True,
    forward_uv_project_environment: bool = False,
) -> dict[str, str]:
    """Build a deliberately sparse child environment.

    No inherited credentials, git state, repo env or pytest/conftest knobs
    reach the timed subprocess. ``PYTEST_DISABLE_PLUGIN_AUTOLOAD`` is a
    harness-only isolation knob; the runner itself never sets it (pinned by
    the contract test).
    """

    path_dirs: list[str] = list(_SYSTEM_PATH_DIRS)
    if fake_uv_dir is not None:
        path_dirs.insert(0, str(fake_uv_dir))
    else:
        real_uv = shutil.which("uv")
        if real_uv is not None:
            path_dirs.insert(0, str(Path(real_uv).parent))
    env: dict[str, str] = {
        "PATH": ":".join(path_dirs),
        "HOME": str(tmp),
        "TMPDIR": str(tmp),
        "LANG": "C.UTF-8",
        "PYTEST_ADDOPTS": "",
    }
    if plugin_autoload:
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    if fake_uv_dir is not None:
        inspect_dir = tmp / "inspect"
        inspect_dir.mkdir(parents=True, exist_ok=True)
        env[FAKEUV_ARGV_VAR] = str(inspect_dir / "fake_uv_argv.txt")
        env[FAKEUV_ENV_VAR] = str(inspect_dir / "fake_uv_env.txt")
    if forward_uv_project_environment:
        outer_value = os.environ.get("UV_PROJECT_ENVIRONMENT")
        if outer_value:
            env["UV_PROJECT_ENVIRONMENT"] = outer_value
    return env


_FAKE_UV_SCRIPT = """\
#!/usr/bin/env bash
# Synthetic `uv` used only by tests/test_backend_pytest_runner.py. It strips
# the runner's known uv options, records the exact invocation and the timed
# environment into $RUNNER_FAKE_UV_ARGV/$RUNNER_FAKE_UV_ENV, then execs the
# selected interpreter like real uv resolves `python`, without syncing.
if [[ $1 == run && $2 == --no-sync ]]; then shift 2; fi
declare -a args=("$@")
if [[ -n ${FAKEUV_DROP_JUNIT:-} ]]; then
  filtered=()
  for single in "${args[@]}"; do
    [[ $single == --junitxml=* ]] && continue
    filtered+=("$single")
  done
  args=("${filtered[@]}")
fi
printf '%s\\n' "${args[@]}" > "$RUNNER_FAKE_UV_ARGV"
env | LC_ALL=C sort > "$RUNNER_FAKE_UV_ENV"
case "${args[0]}" in
  python) exec __TARGET_PYTHON__ "${args[@]:1}" ;;
  *) exec "${args[@]}" ;;
esac
"""


def _write_fake_uv(tmp: Path) -> Path:
    bin_dir = tmp / "fakebin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script_path = bin_dir / "uv"
    script_path.write_text(
        _FAKE_UV_SCRIPT.replace("__TARGET_PYTHON__", shlex.quote(str(sys.executable))),
        encoding="utf-8",
    )
    script_path.chmod(0o755)
    return bin_dir


def _write_fractional_deadline_tools(tmp: Path) -> Path:
    """Record decimal forwarding and stall without paying pytest startup cost.

    The real GNU timeout still supervises the production pipeline. Its uv
    substitute ignores ABRT and records its own pid before execing a sleeper,
    so the test also verifies that kill-after removes the timed process.
    Python abort/collection stacks remain covered by the real-uv tests below.
    """

    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        "trap '' ABRT\n"
        f'printf "%s\\n" "$$" > "${PID_MARKER_VAR}"\n'
        "exec sleep 60\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    real_timeout = shutil.which("timeout")
    assert real_timeout is not None, "GNU timeout is required"
    timeout = bin_dir / "timeout"
    timeout.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 != --version ]]; then\n"
        '  printf "%s\\n" "$@" > "$RUNNER_TIMEOUT_ARGV"\n'
        "fi\n"
        f'exec {shlex.quote(real_timeout)} "$@"\n',
        encoding="utf-8",
    )
    timeout.chmod(0o755)
    return bin_dir


def _run_runner(
    args: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    bound_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Run the wrapper inside its own session, bounded in wall time.

    On a harness-bound overrun the whole process group (wrapper, GNU timeout,
    pytest, everything spawned inside) is SIGKILLed so the outer suite can
    never be left with strays.
    """

    cmd = ["/usr/bin/bash", str(RUNNER_SCRIPT), *args]
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=bound_seconds)
    except subprocess.TimeoutExpired:
        # GNU timeout creates a separate process group within our session.
        # Kill every group in this synthetic session, not just the wrapper's.
        groups = set()
        for entry in Path("/proc").iterdir():
            if entry.name.isdecimal():
                try:
                    if os.getsid(int(entry.name)) == proc.pid:
                        groups.add(os.getpgid(int(entry.name)))
                except ProcessLookupError:
                    pass
        for group in groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.communicate(timeout=10)
        raise AssertionError(
            f"runner exceeded the harness bound ({bound_seconds}s); its process "
            f"group was SIGKILLed. cwd={cwd} args={args}"
        )
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _assert_exact_artifacts(artifact_dir: Path, expected: set[str]) -> None:
    assert artifact_dir.is_dir(), f"missing artifact dir {artifact_dir}"
    assert {entry.name for entry in artifact_dir.iterdir()} == expected, (
        f"artifact dir {artifact_dir} should contain exactly {expected}, "
        f"found {sorted(entry.name for entry in artifact_dir.iterdir())}"
    )


def _assert_log_contains(log_path: Path, *needles: str) -> None:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    missing = [needle for needle in needles if needle not in text]
    assert not missing, f"log {log_path} is missing {missing}; tail:\n{text[-1500:]}"


def _assert_no_core_files(*dirs: Path) -> None:
    for directory in dirs:
        leftovers = [
            entry.name
            for entry in directory.rglob("core*")
            if entry.is_file() and entry.name.startswith("core")
        ]
        assert not leftovers, f"unexpected core files in {directory}: {leftovers}"


def _log_has_no_env_dump(log_path: Path) -> None:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    forbidden_keys = (
        "PYTHONFAULTHANDLER=1",
        "PYTHONUNBUFFERED=1",
        f"PYTHONPATH={REPO_ROOT}",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1",
    )
    for needle in forbidden_keys:
        assert needle not in text, f"log leaked environment content: {needle}"


def _junit_case_names(xml_path: Path) -> list[str]:
    root = ElementTree.fromstring(xml_path.read_bytes())
    suite = root
    if root.tag == "testsuites":
        suites = list(root.iter("testsuite"))
        assert suites, f"no testsuite element inside {xml_path}"
        suite = suites[0]
    return [element.attrib.get("name", "") for element in suite.iter("testcase")]


def _junit_failures(xml_path: Path) -> int:
    root = ElementTree.fromstring(xml_path.read_bytes())
    if root.tag == "testsuites":
        return sum(int(suite.attrib.get("failures", "0")) for suite in root.iter("testsuite"))
    return int(root.attrib.get("failures", "0"))


def _await_pid_death(pid: int, deadline_seconds: float) -> None:
    """Poll until ``pid`` is gone; fail the test early if it still lives."""

    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                return  # dead, awaiting reaping by the host's init
        except FileNotFoundError:
            return
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return  # process exists but is not ours; treat as gone for us
        time.sleep(0.1)
    pytest.fail(f"child pid {pid} was still alive {deadline_seconds}s after the run")


def _restricted_bin(tmp: Path, missing: str) -> dict[str, str]:
    """PATH directory containing every tool the wrapper needs minus one."""

    bin_dir = tmp / f"bin-no-{missing}"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wanted = {"dirname", "mkdir", "timeout", "tee", "uv"} - {missing}
    for tool in sorted(wanted):
        source = shutil.which(tool)
        if source is None:
            continue
        (bin_dir / tool).symlink_to(source)
    return {
        "PATH": str(bin_dir),
        "HOME": str(tmp),
        "LANG": "C.UTF-8",
    }


# ---- pre-flight interface tests ------------------------------------------------


def test_help_exits_zero_with_usage(tmp_path: Path) -> None:
    proc = _run_runner(["--help"], cwd=tmp_path, env=_synthetic_env(tmp_path), bound_seconds=60)
    assert proc.returncode == 0
    assert "usage:" in proc.stdout
    for flag in (
        "--suite-timeout",
        "--kill-after",
        "--faulthandler-timeout",
        "--artifact-dir",
    ):
        assert flag in proc.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--unknown"],
        ["--suite-timeout"],
        ["--suite-timeout", "0"],
        ["--suite-timeout", "-5"],
        ["--suite-timeout", "soon"],
        ["--suite-timeout=0"],
        ["--kill-after", "0.00"],
        ["--faulthandler-timeout", "-1"],
        ["--artifact-dir", ""],
    ],
    ids=str,
)
def test_invalid_arguments_exit_two_without_artifacts(tmp_path: Path, args: list[str]) -> None:
    proc = _run_runner(args, cwd=tmp_path, env=_synthetic_env(tmp_path), bound_seconds=60)
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
    assert not (tmp_path / "pytest-diagnostics").exists()


def test_artifact_dir_existing_file_fails_four(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    proc = _run_runner(
        ["--artifact-dir", str(blocker)],
        cwd=tmp_path,
        env=_synthetic_env(tmp_path),
        bound_seconds=60,
    )
    assert proc.returncode == 4
    assert "cannot create artifact directory" in proc.stderr


@pytest.mark.parametrize("missing", ["timeout", "tee", "uv"])
def test_missing_required_tool_exits_three(tmp_path: Path, missing: str) -> None:
    env = _restricted_bin(tmp_path, missing)
    artifacts = tmp_path / "artifact"
    artifacts.mkdir()
    proc = _run_runner(
        ["--artifact-dir", str(artifacts)],
        cwd=tmp_path,
        env=env,
        bound_seconds=60,
    )
    assert proc.returncode == 3
    assert f"required command not found in PATH: {missing}" in proc.stderr


# ---- successful and failing runs ------------------------------------------------


@pytest.mark.skipif(not _real_uv_ready(), reason="real uv chain required")
def test_real_uv_success_writes_narrow_artifacts(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "success-case", _PASSING_SUITE)
    artifacts = tmp_path / "artifact"
    started = time.monotonic()
    proc = _run_runner(
        ["--artifact-dir", str(artifacts), "--", str(case_dir), *_CACHE_OFF],
        cwd=REPO_ROOT,
        env=_synthetic_env(tmp_path, forward_uv_project_environment=True),
        bound_seconds=120,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 60
    assert proc.stderr == ""
    _assert_exact_artifacts(artifacts, {LOG_NAME, XML_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        DEFAULTS_LINE,
        "test_synthetic_pass",
        "1 passed",
        STDERR_MARKER,
        f"{STATUS_TAG} pytest process finished (combined exit status 0)",
    )
    _log_has_no_env_dump(artifacts / LOG_NAME)
    _assert_no_core_files(tmp_path)
    assert _junit_failures(artifacts / XML_NAME) == 0
    assert _junit_case_names(artifacts / XML_NAME) == ["test_synthetic_pass"]


@pytest.mark.skipif(not _real_uv_ready(), reason="real uv chain required")
def test_real_uv_failure_status_one_survives_tee(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "failure-case", _FAILING_SUITE)
    artifacts = tmp_path / "artifact"
    proc = _run_runner(
        ["--artifact-dir", str(artifacts), "--", str(case_dir), *_CACHE_OFF],
        cwd=REPO_ROOT,
        env=_synthetic_env(tmp_path, forward_uv_project_environment=True),
        bound_seconds=120,
    )
    assert proc.returncode == 1
    _assert_exact_artifacts(artifacts, {LOG_NAME, XML_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        "FAILED",
        "assert 1 == 2",
        f"{STATUS_TAG} pytest process finished (combined exit status 1)",
    )
    _log_has_no_env_dump(artifacts / LOG_NAME)
    _assert_no_core_files(tmp_path)
    assert _junit_failures(artifacts / XML_NAME) == 1


@pytest.mark.skipif(not _real_uv_ready(), reason="real uv chain required")
def test_extra_pytest_args_select_subset(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "selection-case", _TWO_PASS_SUITE)
    artifacts = tmp_path / "artifact"
    proc = _run_runner(
        [
            "--artifact-dir",
            str(artifacts),
            "--",
            str(case_dir),
            "-k",
            "tagged",
            *_CACHE_OFF,
        ],
        cwd=REPO_ROOT,
        env=_synthetic_env(tmp_path, forward_uv_project_environment=True),
        bound_seconds=120,
    )
    assert proc.returncode == 0
    _assert_exact_artifacts(artifacts, {LOG_NAME, XML_NAME})
    _assert_log_contains(artifacts / LOG_NAME, "collected 2 items", "1 passed")
    assert _junit_case_names(artifacts / XML_NAME) == ["test_tagged_pass"]


# ---- fake-uv contract and synthetic failure paths -------------------------------


def test_contract_pins_flags_and_inline_env(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "contract-case", _PASSING_SUITE)
    bin_dir = _write_fake_uv(tmp_path)
    artifacts = tmp_path / "artifact"
    proc = _run_runner(
        [
            "--suite-timeout",
            "90",
            "--kill-after",
            "0.7",
            "--faulthandler-timeout",
            "0.2",
            "--artifact-dir",
            str(artifacts),
            "--",
            str(case_dir),
            *_CACHE_OFF,
        ],
        cwd=tmp_path,
        env=_synthetic_env(tmp_path, fake_uv_dir=bin_dir, plugin_autoload=False),
        bound_seconds=120,
    )
    assert proc.returncode == 0, proc.stderr
    argv_path = tmp_path / "inspect" / "fake_uv_argv.txt"
    argv = argv_path.read_text(encoding="utf-8").splitlines()
    xml_abs = str((artifacts / XML_NAME).resolve())
    expected_prefix = [
        "python",
        "-m",
        "pytest",
        "-vv",
        "--durations=30",
        "-o",
        "faulthandler_timeout=0.2",
        f"--junitxml={xml_abs}",
    ]
    assert argv[: len(expected_prefix)] == expected_prefix
    assert argv[len(expected_prefix) :] == [str(case_dir), "-p", "no:cacheprovider"]

    env_path = tmp_path / "inspect" / "fake_uv_env.txt"
    timed_env: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            timed_env[key] = value
    assert timed_env["PYTHONPATH"] == str(REPO_ROOT)
    assert timed_env["PYTHONFAULTHANDLER"] == "1"
    assert timed_env["PYTHONUNBUFFERED"] == "1"
    # The wrapper must not invent pytest knobs of its own.
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in timed_env
    assert set(timed_env) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "LANG",
        "PYTEST_ADDOPTS",
        "PYTHONPATH",
        "PYTHONFAULTHANDLER",
        "PYTHONUNBUFFERED",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "_",
        FAKEUV_ARGV_VAR,
        FAKEUV_ENV_VAR,
    }
    forbidden = [
        key
        for key in timed_env
        if any(
            word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY")
        )
    ]
    assert not forbidden, forbidden

    _assert_exact_artifacts(artifacts, {LOG_NAME, XML_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        "starting pytest (suite-timeout=90s kill-after=0.7s faulthandler-timeout=0.2s",
        "1 passed",
        f"{STATUS_TAG} pytest process finished (combined exit status 0)",
    )
    _log_has_no_env_dump(artifacts / LOG_NAME)
    _assert_no_core_files(tmp_path)


def test_fractional_deadlines_enforced_offline(tmp_path: Path) -> None:
    bin_dir = _write_fractional_deadline_tools(tmp_path)
    artifacts = tmp_path / "artifact"
    pid_marker = tmp_path / "sleeper_pid.txt"
    timeout_argv = tmp_path / "timeout_argv.txt"
    env = _synthetic_env(tmp_path, fake_uv_dir=bin_dir)
    env[PID_MARKER_VAR] = str(pid_marker)
    env["RUNNER_TIMEOUT_ARGV"] = str(timeout_argv)
    started = time.monotonic()
    proc = _run_runner(
        [
            "--suite-timeout",
            "0.5",
            "--kill-after",
            "0.5",
            "--faulthandler-timeout",
            "10",
            "--artifact-dir",
            str(artifacts),
        ],
        cwd=tmp_path,
        env=env,
        bound_seconds=30,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 137
    assert elapsed < 15
    # Pin what reaches the real supervisor, not just the wrapper's status text:
    # integer rounding or an omitted kill-after must fail this contract.
    assert timeout_argv.read_text(encoding="utf-8").splitlines()[:6] == [
        "--signal=ABRT",
        "--kill-after=0.5",
        "--",
        "0.5",
        "bash",
        "-c",
    ]
    assert pid_marker.exists(), "timed sleeper never recorded its pid"
    _await_pid_death(int(pid_marker.read_text(encoding="utf-8").strip()), deadline_seconds=5)
    _assert_exact_artifacts(artifacts, {LOG_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        "starting pytest (suite-timeout=0.5s kill-after=0.5s faulthandler-timeout=10s",
        f"{STATUS_TAG} pytest process finished (combined exit status 137)",
        "JUnit XML was not written",
    )
    _log_has_no_env_dump(artifacts / LOG_NAME)
    _assert_no_core_files(tmp_path)


def test_call_stall_dumps_stacks_and_finishes_bounded(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "call-stall-case", _CALL_STALL_SUITE)
    bin_dir = _write_fake_uv(tmp_path)
    artifacts = tmp_path / "artifact"
    started = time.monotonic()
    proc = _run_runner(
        [
            "--suite-timeout",
            "90",
            "--kill-after",
            "2",
            "--faulthandler-timeout",
            "2",
            "--artifact-dir",
            str(artifacts),
            "--",
            str(case_dir),
            *_CACHE_OFF,
        ],
        cwd=tmp_path,
        env=_synthetic_env(tmp_path, fake_uv_dir=bin_dir),
        bound_seconds=120,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 45
    _assert_exact_artifacts(artifacts, {LOG_NAME, XML_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        "test_synthetic.py::test_synthetic_self_recovering_call_stall",
        "in test_synthetic_self_recovering_call_stall",
        "most recent call first",
        "1 passed",
        f"{STATUS_TAG} pytest process finished (combined exit status 0)",
    )
    _log_has_no_env_dump(artifacts / LOG_NAME)
    _assert_no_core_files(tmp_path)
    assert _junit_case_names(artifacts / XML_NAME) == ["test_synthetic_self_recovering_call_stall"]


def test_teardown_stall_dumps_fixture_stack(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "teardown-stall-case", _TEARDOWN_STALL_SUITE)
    bin_dir = _write_fake_uv(tmp_path)
    artifacts = tmp_path / "artifact"
    started = time.monotonic()
    proc = _run_runner(
        [
            "--suite-timeout",
            "90",
            "--kill-after",
            "2",
            "--faulthandler-timeout",
            "2",
            "--artifact-dir",
            str(artifacts),
            "--",
            str(case_dir),
            *_CACHE_OFF,
        ],
        cwd=tmp_path,
        env=_synthetic_env(tmp_path, fake_uv_dir=bin_dir),
        bound_seconds=120,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 45
    _assert_exact_artifacts(artifacts, {LOG_NAME, XML_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        "test_synthetic.py::test_synthetic_teardown_stall",
        "in synthetic_teardown_staller",
        "most recent call first",
        "1 passed",
    )
    _log_has_no_env_dump(artifacts / LOG_NAME)
    assert _junit_case_names(artifacts / XML_NAME) == ["test_synthetic_teardown_stall"]


@pytest.mark.skipif(not _real_uv_ready(), reason="real uv chain required")
def test_collection_stall_deadline_aborts_with_stack(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "collection-stall-case", _COLLECTION_STALL_SUITE)
    artifacts = tmp_path / "artifact"
    started = time.monotonic()
    proc = _run_runner(
        [
            "--suite-timeout",
            "3",
            "--kill-after",
            "1",
            "--faulthandler-timeout",
            "60",
            "--artifact-dir",
            str(artifacts),
            "--",
            str(case_dir),
            *_CACHE_OFF,
        ],
        cwd=REPO_ROOT,
        env=_synthetic_env(tmp_path, forward_uv_project_environment=True),
        bound_seconds=45,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 137
    assert elapsed < 20
    _assert_exact_artifacts(artifacts, {LOG_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        "Fatal Python error: Aborted",
        "most recent call first",
        "test_synthetic.py",
        f"{STATUS_TAG} pytest process finished (combined exit status 137)",
        "JUnit XML was not written",
    )
    _log_has_no_env_dump(artifacts / LOG_NAME)
    _assert_no_core_files(tmp_path)


@pytest.mark.skipif(not _real_uv_ready(), reason="real uv chain required")
@pytest.mark.parametrize("resistant_parent", [False, True])
def test_signal_resistant_child_is_force_killed(tmp_path: Path, resistant_parent: bool) -> None:
    body = _SIGNAL_RESISTANT_SUITE
    if resistant_parent:
        body = body.replace(
            "    child = subprocess.Popen",
            "    signal.signal(signal.SIGABRT, signal.SIG_IGN)\n    child = subprocess.Popen",
        )
    case_dir = _write_case(tmp_path, "force-kill-case", body)
    artifacts = tmp_path / "artifact"
    pid_marker = tmp_path / "child_pid.txt"
    env = _synthetic_env(tmp_path, forward_uv_project_environment=True)
    env[PID_MARKER_VAR] = str(pid_marker)
    started = time.monotonic()
    proc = _run_runner(
        [
            "--suite-timeout",
            "5",
            "--kill-after",
            "2",
            "--faulthandler-timeout",
            "1",
            "--artifact-dir",
            str(artifacts),
            "--",
            str(case_dir),
            *_CACHE_OFF,
        ],
        cwd=REPO_ROOT,
        env=env,
        bound_seconds=45,
    )
    elapsed = time.monotonic() - started
    assert proc.returncode == 137
    assert elapsed < 25
    _assert_exact_artifacts(artifacts, {LOG_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        "most recent call first",
        f"{STATUS_TAG} pytest process finished (combined exit status 137)",
        "JUnit XML was not written",
    )
    _log_has_no_env_dump(artifacts / LOG_NAME)
    _assert_no_core_files(tmp_path)
    assert pid_marker.exists(), "signal-resistant child never recorded its pid"
    child_pid = int(pid_marker.read_text(encoding="utf-8").strip())
    _await_pid_death(child_pid, deadline_seconds=5)


# ---- artifact surface and special paths ------------------------------------------


def test_paths_with_spaces_and_narrow_output(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "case with space", _PASSING_SUITE)
    bin_dir = _write_fake_uv(tmp_path)
    artifacts = tmp_path / "diag outs"
    proc = _run_runner(
        [
            "--suite-timeout",
            "90",
            "--artifact-dir",
            str(artifacts),
            "--",
            str(case_dir),
            *_CACHE_OFF,
        ],
        cwd=tmp_path,
        env=_synthetic_env(tmp_path, fake_uv_dir=bin_dir),
        bound_seconds=120,
    )
    assert proc.returncode == 0, proc.stderr
    _assert_exact_artifacts(artifacts, {LOG_NAME, XML_NAME})
    _assert_log_contains(
        artifacts / LOG_NAME,
        "test_synthetic_pass",
        "1 passed",
    )
    assert _junit_case_names((artifacts / XML_NAME).resolve()) == ["test_synthetic_pass"]


def test_missing_junit_xml_fails_six(tmp_path: Path) -> None:
    case_dir = _write_case(tmp_path, "missing-junit-case", _PASSING_SUITE)
    bin_dir = _write_fake_uv(tmp_path)
    artifacts = tmp_path / "artifact"
    env = _synthetic_env(tmp_path, fake_uv_dir=bin_dir)
    env["FAKEUV_DROP_JUNIT"] = "1"
    proc = _run_runner(
        [
            "--artifact-dir",
            str(artifacts),
            "--",
            str(case_dir),
            *_CACHE_OFF,
        ],
        cwd=tmp_path,
        env=env,
        bound_seconds=120,
    )
    assert proc.returncode == 6
    _assert_exact_artifacts(artifacts, {LOG_NAME})
    assert "JUnit XML is missing" in proc.stderr
    assert (artifacts / LOG_NAME).exists()
