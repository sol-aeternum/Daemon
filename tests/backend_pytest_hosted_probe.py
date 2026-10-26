"""Explicit native hosted probes; outside the default test-file inventory."""

from pathlib import Path
import shutil
import sys

from tests import backend_pytest_supervisor as supervisor


def test_native_success(tmp_path):
    case = tmp_path / "positive"
    case.mkdir()
    (case / "pytest.ini").write_text("[pytest]\naddopts = -s\n", encoding="utf-8")
    marker = supervisor.StartupMarker.fresh(tmp_path, "hosted-positive")
    (case / "test_positive.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def test_started():\n"
        "    p = Path(os.environ['SYNTH_PID_MARKER'])\n"
        "    p.write_text('started:' + p.name + '\\n')\n"
        "    print('hosted stdout')\n"
        "    os.write(2, b'hosted stderr\\n')\n",
        encoding="utf-8",
    )
    uv = shutil.which("uv")
    assert uv is not None
    environment = {
        "PATH": str(Path(uv).parent) + ":/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "LANG": "C.UTF-8",
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "UV_PROJECT_ENVIRONMENT": str(Path(sys.executable).parent.parent),
        "SYNTH_PID_MARKER": str(marker.path),
    }
    root = Path(__file__).resolve().parents[1]
    result = supervisor.run_runner(
        [
            "/usr/bin/bash",
            str(root / "scripts/run_backend_pytest.sh"),
            "--suite-timeout",
            "20",
            "--kill-after",
            "2",
            "--faulthandler-timeout",
            "10",
            "--artifact-dir",
            str(tmp_path / "artifacts"),
            "--",
            str(case),
            "-p",
            "no:cacheprovider",
        ],
        cwd=root,
        env=environment,
        bound_seconds=30,
        startup=marker,
    )
    assert result.returncode == 0
    supervisor.assert_startup_and_normal_closure(marker, result, "SYNTH_PID_MARKER")
    assert "1 passed" in (tmp_path / "artifacts/pytest.log").read_text()


def test_accounted_rescue(tmp_path, monkeypatch):
    from tests.backend_pytest_native_rescue_probe import (
        test_native_harness_overrun_remains_accounted_failure,
    )

    # The complete unchanged maintained native negative, not a simulated receipt.
    test_native_harness_overrun_remains_accounted_failure(tmp_path, monkeypatch)
