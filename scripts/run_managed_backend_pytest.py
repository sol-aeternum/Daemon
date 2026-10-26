"""Local stdlib caller only; no installed-helper import, task adoption or fallback.

The CI shell waits for a SEPARATE installed lifecycle invocation. Its original
scope_job exec is the selected checkout-local Python/supervisor, never this caller
or a descendant uv/shell manager. Context selects an existing registration only.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys


def load_supervisor(repo):
    path = repo / "tests/backend_pytest_supervisor.py"
    spec = importlib.util.spec_from_file_location("_managed_pytest_context", path)
    if spec is None or spec.loader is None:
        raise ImportError("local supervisor import spec/loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # local stdlib-only definitions; no registration I/O
    return module


def invocation(repo, selection, pytest_args):
    repo = repo.resolve(strict=True)
    s = load_supervisor(repo)
    base, state, lifecycle = s.layout(repo)
    task = repo.name  # bounded candidate key, NOT authority
    manifest_path = state / "owned-manifests" / (task + ".json")
    manifest = s.private_record(manifest_path)  # exactly ONE existing record
    context = s.InvocationContext.parse(
        s.InvocationContext(
            task=task,
            owner=manifest["owner_session"],
            nonce=manifest["nonce"],
            source=str(repo),
            resources=str(base / "resources" / task),
            evidence=str(base / "evidence" / task),
            manifest=str(manifest_path),
            interpreter=s.local_interpreter(repo, selection),
        ).canonical()
    )
    # UV_PROJECT_ENVIRONMENT does not redirect either basedpyright selector.
    # The ordinary backend gate must use their aligned .venv, not silently
    # mix a real but different override environment with .venv type analysis.
    if Path(context.interpreter).parent.parent != repo / ".venv":
        raise s.SupervisionFailure(
            "selected environment conflicts with both basedpyright .venv selectors"
        )
    # Also checks manifest.source.path equality and W/R/E identity, not basename/UID.
    s.registered_context(context)
    if not lifecycle.is_file() or not os.access(lifecycle, os.X_OK):
        raise s.SupervisionFailure("installed opencode-work run is unavailable")
    args = list(pytest_args) or ["-q"]
    return context, [
        str(lifecycle),
        "run",
        task,
        "--",
        context.interpreter,
        "-I",
        "-B",
        str(repo / "tests/backend_pytest_supervisor.py"),
        "--manager",
        "--context",
        context.canonical(),
        "--",
        *args,
    ]


def main(argv):
    repo = Path(__file__).resolve().parents[1]
    try:
        context, command = invocation(repo, os.environ.get("UV_PROJECT_ENVIRONMENT"), argv)
        env = dict(os.environ)
        env["UV_PROJECT_ENVIRONMENT"] = str(Path(context.interpreter).parent.parent)
        # Preserve all ordinary pytest selection/status. No capture/timeout/retry,
        # no task creation, managed-scope reconciliation, shell or export eval.
        result = subprocess.run(command, cwd=context.source, env=env, check=False)
        return result.returncode if result.returncode >= 0 else 128 - result.returncode
    except (OSError, ValueError, KeyError, AssertionError) as error:
        print("managed backend pytest refused: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
