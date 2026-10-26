"""Offline synthetic coverage for scripts/run_backend_pytest.sh (issue #454).

Integration tests drive the real production chain (bash wrapper -> GNU timeout ->
``uv run --no-sync`` -> locked venv Python -> pytest) against fictional suites
written into pytest temporary directories. There is no network access, no
production probing, no credential use, and the synthetic children never import
the repository conftest or .env: each case dir carries its own minimal
``pytest.ini``, is passed to pytest explicitly, and runs with a stripped
environment. The managed entry uses an original bootstrap/private-channel
handoff and one dedicated subreaper per invocation. Trusted same-UID programme
coordination is assumed; deliberate/buggy external cgroup migration is NOT excluded.
Missing managed handoff fails before launch. Rescue never produces a normal pass.
Local repair; managed qualification and final independent review are tracked separately.
Most contract tests substitute an offline uv shim while retaining real pytest;
fractional deadlines use a controlled sleeper to avoid pytest startup timing.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tests import backend_pytest_supervisor as supervisor

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
        "p=os.environ['{PID_MARKER_VAR}'];"
        "open(p, 'x').write('started:'+os.path.basename(p)+'\\\\n');"
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
    substitute ignores ABRT and records positive same-run startup before execing a sleeper,
    so the test also verifies that kill-after removes the timed process.
    Python abort/collection stacks remain covered by the real-uv tests below.
    """

    bin_dir = tmp / "fakebin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        "trap '' ABRT\n"
        "set -o noclobber\n"
        f'printf "started:%s\\n" "${{{PID_MARKER_VAR}##*/}}" > "${PID_MARKER_VAR}"\n'
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
    startup: supervisor.StartupMarker | None = None,
) -> supervisor.Result:
    """Exact original command; no implicit ordinary-process fallback or skip."""

    cmd = ["/usr/bin/bash", str(RUNNER_SCRIPT), *args]
    return supervisor.run_runner(
        cmd, cwd=cwd, env=env, bound_seconds=bound_seconds, startup=startup
    )


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


def _assert_started_and_tree_closed(
    marker: supervisor.StartupMarker, result: supervisor.Result
) -> None:
    supervisor.assert_startup_and_normal_closure(marker, result, PID_MARKER_VAR)


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
    startup = supervisor.StartupMarker.fresh(tmp_path, "sleeper")
    pid_marker = startup.path
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
        startup=startup,
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
    assert pid_marker.exists(), "timed sleeper never recorded same-run startup"
    _assert_started_and_tree_closed(startup, proc)
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
    startup = supervisor.StartupMarker.fresh(tmp_path, "resistant-child")
    pid_marker = startup.path
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
        startup=startup,
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
    assert pid_marker.exists(), "signal-resistant child never recorded same-run startup"
    _assert_started_and_tree_closed(startup, proc)


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


# Source-only regressions. Concrete-port mocks below NEVER grant host authority.
# Trusted same-UID/non-migrating scope is explicit; these are not kernel-exclusion tests.


def test_missing_lifetime_provider_fails_before_any_launch(tmp_path, monkeypatch):
    # Historical regression name retained; the concrete seam is now the original
    # managed private handoff, not a fictional lifetime-exclusive provider.
    launches = []

    def forbidden_launch(*args, **kwargs):
        launches.append((args, kwargs))
        raise AssertionError("unexpected child launch")

    monkeypatch.setattr(subprocess, "Popen", forbidden_launch)
    monkeypatch.setattr(supervisor.os, "fork", forbidden_launch)
    monkeypatch.setattr(supervisor, "_client", None)
    monkeypatch.delenv(supervisor.CHANNEL, raising=False)
    with pytest.raises(
        supervisor.SupervisionFailure, match="missing original.*runner NOT launched"
    ):
        _run_runner(["--help"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}, bound_seconds=1)
    assert launches == []


def _model_request(tmp_path, *, nonce="a" * 32, deadline=40):
    return supervisor.Request(
        ("/usr/bin/bash", "runner", "--help"),
        str(tmp_path),
        (("PATH", "/usr/bin:/bin"),),
        nonce,
        deadline,
    )


def _model_domain():
    return supervisor.Domain(
        "/sys/fs/cgroup/fictional-own.scope/backend-pytest-" + "a" * 32,
        (-101, -102, -103, -104),
        [[0, 0, 0, 0, 0]] * 4,
    )


def _all_facts():
    return {
        name: True
        for name in ["direct_reaped", "no_children", "domain_empty", "stdout_eof", "stderr_eof"]
    }


def _model_context(source):
    # Pure model value: no registration/FS lookup and no host authority.
    base = source.parent.parent
    home = base.parent.parent.parent
    return supervisor.InvocationContext(
        source.name,
        "ses_Model",
        "c" * 32,
        str(source),
        str(base / "resources" / source.name),
        str(base / "evidence" / source.name),
        str(home / ".local/state/opencode-work/owned-manifests" / (source.name + ".json")),
        str(source / ".venv/bin/python"),
    )


def test_waitpid_zero_and_echild_cannot_discover_or_prove_live_tree(tmp_path, monkeypatch):
    _model_request(tmp_path)
    collector = supervisor.Collector(supervisor.CapturedChild(123, -124, -125, -126))
    monkeypatch.setattr(supervisor.os, "waitpid", lambda pid, flags: (0, 0))
    collector.reap()
    assert not collector.no_children
    assert collector.direct_status is None
    assert not supervisor.complete(collector.facts(True))

    def echild(pid, flags):
        raise ChildProcessError("synthetic empty wait set")

    monkeypatch.setattr(supervisor.os, "waitpid", echild)
    with pytest.raises(supervisor.SupervisionFailure, match="ECHILD without original direct reap"):
        collector.reap()
    assert collector.no_children
    assert not supervisor.complete(collector.facts(True))  # no direct reap or EOF


@pytest.mark.parametrize("missing", ["direct", "children", "domain", "stdout", "stderr"])
def test_normal_closure_needs_each_independent_fact(tmp_path, monkeypatch, missing):
    _model_request(tmp_path)
    collector = supervisor.Collector(supervisor.CapturedChild(123, -124, -125, -126))
    collector.direct_status = None if missing == "direct" else 0
    collector.no_children = missing != "children"
    collector.eofs = {"stdout", "stderr"} - (
        {missing} if missing in {"stdout", "stderr"} else set()
    )
    assert not supervisor.complete(collector.facts(missing != "domain"))


def test_registration_failure_preserves_failure_without_unbound_signal(tmp_path, monkeypatch):
    def no_registration(*args):
        raise OSError("synthetic pre-exec registration failure")

    effects = []
    monkeypatch.setattr(supervisor, "create_domain", no_registration)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: effects.append(args))
    monkeypatch.setattr(supervisor.signal, "pidfd_send_signal", lambda *args: effects.append(args))
    retained = []
    with pytest.raises(OSError, match="registration failure"):
        supervisor.manager_one(
            None,
            -101,
            "/fictional-own.scope",
            _model_request(tmp_path),
            retained,
            _model_context(tmp_path / ".local/share/opencode-work/worktrees/model-task"),
        )
    assert effects == []
    assert retained == []


def _worker_ports(tmp_path, monkeypatch, *, bad_ack=False, overrun=False, detection_time=40.0):
    """Exercise concrete worker/rescue/emit callsites, with NO usable native FDs."""
    source = tmp_path / ".local/share/opencode-work/worktrees/model-task"
    monkeypatch.setattr(supervisor, "__file__", str(source / "tests/backend_pytest_supervisor.py"))
    request = _model_request(tmp_path)
    domain = _model_domain()
    clock = [1.0]
    sends, phases, kills, emitted = [], [], [], []

    class Channel:
        def getsockopt(self, *args):
            return supervisor.struct.pack("3i", 4242, supervisor.os.getuid(), 1000)

        def close(self):
            pass

    control, startup = Channel(), Channel()
    setup = {
        "request": request.wire(),
        "manager_pid": 4242,
        "group": "/fictional-own.scope",
        "path": domain.path,
        "fds": list(domain.fds),
        "identities": domain.identities,
        **supervisor.context_frame(_model_context(source)),
    }

    def receive(channel, nonce, seq, deadline):
        if channel is control:
            return {"kind": "setup", "data": setup} if seq == 0 else {"kind": "launch", "data": {}}
        return {"kind": "wrong" if bad_ack else "placed", "data": {"path": domain.path}}

    def reaped(collector):
        collector.direct_status = 137
        collector.no_children = True
        collector.eofs = {"stdout", "stderr"}

    def collect(domain, request, collector, control, deadline, *, rescue):
        phases.append((deadline, rescue))
        if overrun and not rescue:
            clock[0] = detection_time
            raise TimeoutError("synthetic original harness overrun")
        reaped(collector)
        return _all_facts()

    monkeypatch.setattr(supervisor.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(supervisor.os, "getppid", lambda: 4242)
    monkeypatch.setattr(supervisor, "self_group", lambda: "/fictional-own.scope")
    monkeypatch.setattr(supervisor, "subreaper", lambda: None)
    monkeypatch.setattr(supervisor, "request_from", lambda data, nonce: request)
    monkeypatch.setattr(supervisor.Domain, "check", lambda self: None)
    monkeypatch.setattr(supervisor.Domain, "populated", lambda self: False)
    monkeypatch.setattr(
        supervisor.Domain, "kill_owned", lambda self, deadline: kills.append(deadline)
    )
    monkeypatch.setattr(supervisor, "receive", receive)
    monkeypatch.setattr(supervisor, "send", lambda *args: sends.append(args[2:]))
    monkeypatch.setattr(
        supervisor,
        "fork_parked",
        lambda *args: (supervisor.CapturedChild(123, -124, -125, -126), startup),
    )
    monkeypatch.setattr(supervisor.Collector, "reap", reaped)
    monkeypatch.setattr(supervisor, "collect", collect)
    monkeypatch.setattr(supervisor, "emit", lambda *args: emitted.append(args))
    code = supervisor.worker(control, request.nonce, request.deadline)
    return code, sends, phases, kills, emitted


def test_overrun_uses_one_rescue_deadline_and_never_returns_pass(tmp_path, monkeypatch):
    code, sends, phases, kills, emitted = _worker_ports(tmp_path, monkeypatch, overrun=True)
    assert code == 1
    assert phases == [(40, False), (50.0, True)]
    final, deadline, seq = emitted[0][3:]
    assert not final["ok"] and final["rescue_used"]
    assert final["accounting"]["accounted"]
    assert deadline == final["end"] == 50.0 and seq == 2
    assert any(item[1] == "rescue-start" and item[-1] == 50.0 for item in sends)
    assert kills == []  # this model supplied an empty domain, not authority to kill


def test_observed_preexec_exit_never_signals_and_missing_eof_still_fails(tmp_path, monkeypatch):
    domain = _model_domain()
    collector = supervisor.Collector(supervisor.CapturedChild(123, -124, -125, -126))
    signals = []

    def reaped(collector):
        collector.direct_status = 0
        collector.no_children = True

    def missing_eof(*args, **kwargs):
        raise supervisor.SupervisionFailure("synthetic missing original stderr EOF")

    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 1)
    monkeypatch.setattr(supervisor.Collector, "reap", reaped)
    monkeypatch.setattr(supervisor.Domain, "check", lambda self: None)
    monkeypatch.setattr(supervisor.Domain, "populated", lambda self: False)
    monkeypatch.setattr(supervisor, "collect", missing_eof)
    monkeypatch.setattr(supervisor.signal, "pidfd_send_signal", lambda *args: signals.append(args))
    with pytest.raises(supervisor.SupervisionFailure, match="missing original stderr EOF"):
        supervisor.rescue(domain, _model_request(tmp_path), collector, None, False, 11)
    assert signals == []


def test_strictly_late_detection_keeps_original_rescue_ceiling(tmp_path, monkeypatch):
    code, sends, phases, kills, emitted = _worker_ports(
        tmp_path, monkeypatch, overrun=True, detection_time=40.1
    )
    assert code == 1
    assert phases == [(40, False), (50.0, True)]
    final, deadline, seq = emitted[0][3:]
    assert deadline == final["end"] == 50.0  # not50.1
    assert not final["ok"] and final["rescue_used"]
    assert final["accounting"]["accounted"]
    assert any(item[1] == "rescue-start" and item[-1] == 50.0 for item in sends)
    assert seq == 2 and kills == []


def test_foreign_marker_or_rescue_receipt_cannot_claim_no_strays(tmp_path):
    marker = supervisor.StartupMarker(tmp_path / "not-read", "b" * 32)
    request = _model_request(tmp_path)
    proof = supervisor.Closure(request, 137, _all_facts(), False)
    result = supervisor.Result(request.argv, 137, "", "", proof)
    with pytest.raises(supervisor.SupervisionFailure, match="same-run pre-rescue"):
        _assert_started_and_tree_closed(marker, result)  # rejects BEFORE any marker read
    same_run = supervisor.Request(
        request.argv,
        request.cwd,
        ((PID_MARKER_VAR, str(marker.path)),),
        marker.nonce,
        request.deadline,
    )
    rescued = supervisor.Closure(same_run, 137, _all_facts(), True)
    result = supervisor.Result(same_run.argv, 137, "", "", rescued)
    with pytest.raises(supervisor.SupervisionFailure, match="same-run pre-rescue"):
        _assert_started_and_tree_closed(marker, result)


def test_fixture_preserves_exact_inner_argv_env_cwd_and_original_bound(tmp_path, monkeypatch):
    observed = []
    original_env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTEST_ADDOPTS": ""}

    def capture(argv, *, cwd, env, bound_seconds, startup):
        observed.append((argv, cwd, dict(env), bound_seconds, startup))
        return "synthetic-not-a-native-result"

    monkeypatch.setattr(supervisor, "run_runner", capture)
    result = _run_runner(
        ["--suite-timeout", "0.5", "--kill-after", "0.5"],
        cwd=tmp_path,
        env=original_env,
        bound_seconds=30,
    )
    assert observed == [
        (
            ["/usr/bin/bash", str(RUNNER_SCRIPT), "--suite-timeout", "0.5", "--kill-after", "0.5"],
            tmp_path,
            original_env,
            30,
            None,
        )
    ]
    assert original_env == {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PYTEST_ADDOPTS": ""}
    assert result == "synthetic-not-a-native-result"


def test_bad_startup_ack_never_releases_production_exec(tmp_path, monkeypatch):
    code, sends, phases, kills, emitted = _worker_ports(tmp_path, monkeypatch, bad_ack=True)
    assert code == 1
    assert not any(item[1] == "exec" for item in sends)
    assert phases == [(11.0, True)]
    assert not emitted[0][3]["ok"]


@pytest.mark.parametrize(
    "raw,flags",
    [
        (b"", 0),
        (b"x" * (supervisor.MAX_PACKET + 1), 0),
        (b"{}", supervisor.socket.MSG_TRUNC),
        (b'{"v":1,"v":1}', 0),
        (b'{"v":1,"nonce":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","seq":0,"kind":"x","data":NaN}', 0),
    ],
)
def test_bounded_private_frames_reject_eof_truncation_duplicates_and_nonfinite(raw, flags):
    with pytest.raises((supervisor.SupervisionFailure, ValueError)):
        supervisor.decode_packet(raw, flags)


def test_private_sequence_and_nonce_are_bound_before_consumption(monkeypatch):
    class Channel:
        def recvmsg(self, size):
            raw = supervisor.json.dumps(
                {"v": 1, "nonce": "b" * 32, "seq": 7, "kind": "final", "data": {}}
            ).encode()
            return raw, [], 0, None

    channel = Channel()
    monkeypatch.setattr(supervisor.select, "select", lambda *args: ([channel], [], []))
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 1)
    with pytest.raises(supervisor.SupervisionFailure, match="foreign/replayed/out-of-sequence"):
        supervisor.receive(channel, "a" * 32, 0, 2)


def test_trusted_same_uid_limit_is_not_a_kernel_exclusion_claim():
    doc = supervisor.__doc__
    assert doc is not None
    assert "Deliberate/buggy external migration" in doc
    assert "No kernel lifetime exclusion" in doc
