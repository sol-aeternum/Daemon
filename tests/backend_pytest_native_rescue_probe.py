"""Explicitly selected ONE native rescue negative; not default pytest discovery.

Expected: this pytest assertion passes, but the unchanged manager/managed scope
exits1 because accounted rescued failure remains retained. Never call that scope0.
No accepted module is imported/recollected and no production boundary is patched.
The only observer forwards the real client send unchanged, recording its original
deadline before dispatch. No fake IPC, clock, FD, kill, reaper or completion fact.
"""

import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest

from tests import backend_pytest_supervisor as supervisor

HARNESS_SECONDS = 5.0
WORKLOAD_SECONDS = 60
GNU_TIMEOUT_SECONDS = "60"
MARKER_VARIABLE = "SYNTH_PID_MARKER"  # existing synthetic marker, never numeric control

STALLED_SUITE = f'''\
import os
from pathlib import Path
import time


def test_positive_startup_then_outlive_original_harness():
    marker = Path(os.environ["{MARKER_VARIABLE}"])
    with marker.open("xb") as stream:
        stream.write(b"started:" + marker.name.encode("ascii") + bytes([10]))
    time.sleep({WORKLOAD_SECONDS})
'''


def test_native_harness_overrun_remains_accounted_failure(tmp_path, monkeypatch):
    """Real runner/leaf rescue; expected failed result is NOT normal completion."""
    root = Path(__file__).resolve().parents[1]
    case = tmp_path / "controlled-case"
    case.mkdir()
    (case / "pytest.ini").write_text("[pytest]\naddopts = -s\n", encoding="utf-8")
    (case / "test_controlled.py").write_text(STALLED_SUITE, encoding="utf-8")
    marker = supervisor.StartupMarker.fresh(tmp_path, "native-rescue")
    artifacts = tmp_path / "runner-artifacts"
    uv = shutil.which("uv")
    assert uv is not None, "current local qualified uv must be visible; no skip/fallback"
    project_env = os.environ["UV_PROJECT_ENVIRONMENT"]
    assert Path(project_env) == Path(sys.executable).parent.parent
    env = {
        "PATH": str(Path(uv).parent) + ":/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "LANG": "C.UTF-8",
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "UV_PROJECT_ENVIRONMENT": project_env,
        MARKER_VARIABLE: str(marker.path),
    }
    command = [
        "/usr/bin/bash",
        str(root / "scripts/run_backend_pytest.sh"),
        "--suite-timeout",
        GNU_TIMEOUT_SECONDS,
        "--kill-after",
        "2",
        "--faulthandler-timeout",
        "30",
        "--artifact-dir",
        str(artifacts),
        "--",
        str(case),
        "-p",
        "no:cacheprovider",
    ]
    submitted = []
    original_send = supervisor.send

    def observe_original_send(channel, nonce, seq, kind, data, deadline):
        if kind == "run":
            assert not submitted and nonce == marker.nonce and seq == 0
            assert data["deadline"] == deadline
            frame = {
                "nonce": nonce,
                "seq": seq,
                "kind": kind,
                "data": data,
                "frame_deadline": deadline,
            }
            # Observation only: preserve the actual source-created original
            # request before its unchanged real send; no deadline renewal/slack.
            with (tmp_path / "submitted-request.json").open("x", encoding="utf-8") as stream:
                json.dump(frame, stream, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            submitted.append(frame)
        return original_send(channel, nonce, seq, kind, data, deadline)

    monkeypatch.setattr(supervisor, "send", observe_original_send)
    with pytest.raises(
        supervisor.SupervisionFailure,
        match="runner failed/rescued/UNKNOWN; retain invocation: original phase deadline exhausted",
    ) as failure:
        supervisor.run_runner(
            command, cwd=root, env=env, bound_seconds=HARNESS_SECONDS, startup=marker
        )
    received_at = time.monotonic()
    assert len(submitted) == 1
    request = submitted[0]["data"]
    assert request["argv"] == command and request["cwd"] == str(root) and request["env"] == env
    ceiling = request["deadline"] + supervisor.RESCUE_SECONDS
    assert request["deadline"] <= received_at < ceiling  # exact original budget, no tolerance

    context = supervisor.client_context(ceiling)  # original validated cached hello
    evidence = Path(context.evidence)
    intent_path = evidence / ("backend-pytest-" + marker.nonce + "-intent.json")
    result_path = evidence / ("backend-pytest-" + marker.nonce + "-result.json")
    registration = json.loads(intent_path.read_bytes())
    receipt = json.loads(result_path.read_bytes())
    own_scope = supervisor.self_group()  # own membership only; no scan/late PID lookup
    assert registration["owner"] == context.owner
    assert registration["task_nonce"] == context.nonce
    assert registration["nonce"] == receipt["nonce"] == marker.nonce
    assert registration["scope"] == receipt["scope"] == own_scope
    leaf = "/sys/fs/cgroup" + own_scope + "/backend-pytest-" + marker.nonce
    assert registration["leaf"] == receipt["leaf"] == leaf
    assert len(registration["parent_identity"]) == 5
    assert registration["parent_identity"][0] > 0 and registration["parent_identity"][1] > 0
    assert marker.path.read_bytes() == marker.payload.encode("ascii")

    final = receipt["result"]
    assert set(final) == {"ok", "rescue_used", "error", "accounting", "end"}
    assert final["ok"] is False and final["rescue_used"] is True
    assert final["error"] == "original phase deadline exhausted"
    assert final["end"] == ceiling  # original request.deadline+10, never detection+10.1
    assert final["accounting"] == {
        "accounted": True,
        "facts": {
            key: True
            for key in ["direct_reaped", "no_children", "domain_empty", "stdout_eof", "stderr_eof"]
        },
    }
    assert all(0 <= size <= supervisor.MAX_STREAM for size in receipt["stream_bytes"].values())
    # The live marker->sleep60 workload cannot normally finish within the
    # original5+10 budget; GNU timeout60 is deliberately not the terminating event.
    # Marker proves exec was released, so unchanged rescue uses held own-leaf
    # cgroup.kill (not its pre-ACK held-pidfd branch). No syscall tracing/new hook.
    observation = {
        "criterion": "ONE native original-harness-overrun/held-own-leaf accounted failed rescue",
        "assertions_passed": True,
        "nonce": marker.nonce,
        "original_request": submitted[0],
        "response_received_at": received_at,
        "original_rescue_ceiling": ceiling,
        "exception": str(failure.value),
        "marker": str(marker.path),
        "marker_payload": marker.payload,
        "intent_path": str(intent_path),
        "result_path": str(result_path),
        "registration": registration,
        "receipt": receipt,
        "expected_managed_exit": 1,
        "status_limit": "Failed IPC schema exports no numeric wrapper wait status; none invented.",
        "proof_limit": "Reviewed live held-FD kill/reap path and accounted receipt, not a new syscall trace or hostile migration guarantee.",
    }
    with (tmp_path / "rescue-observation.json").open("x", encoding="utf-8") as stream:
        json.dump(observation, stream, indent=2, allow_nan=False)
        stream.write("\n")
