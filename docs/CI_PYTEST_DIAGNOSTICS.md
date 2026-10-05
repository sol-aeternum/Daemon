# Backend Pytest diagnostics

This is diagnostic hardening for [#454](https://github.com/sol-aeternum/Daemon/issues/454),
not a fix for an identified intermittent wait. The root cause remains unverified;
SDK capability metadata reproducibility is separately tracked by #451.

## CI execution and artifacts

The backend job in `.github/workflows/ci.yml` runs
`bash scripts/run_backend_pytest.sh` after locked dependency synchronization.
The runner executes the full, unfiltered suite through the installed environment,
without dependency resolution, retries, new plugins or relaxed assertions.

- Verbose test IDs identify the last started test. Completed runs report the
  slowest 30 setup/call/teardown phases separately from other backend gates.
- Pytest's 120-second faulthandler timer prints thread stacks for a slow test;
  it is diagnostic only, not a short per-test failure limit.
- GNU `timeout` provides a 20-minute process-group deadline, including
  collection/import and shutdown. `SIGABRT` requests Python faulthandler stacks;
  a 15-second kill-after bound stops signal-resistant survivors. Core dumps are
  disabled. This is a Linux/GNU-coreutils runner, not a portable supervisor.
  The supervised shell and log reader remain in the timed process group;
  the shell stays alive after `SIGABRT` until the mandatory group kill (exit
  137), even when Python has already exited and a descendant holds output open.
- The Pytest step has a 25-minute fallback limit, inside a 30-minute backend job
  limit. These leave headroom for a separately bounded artifact upload under
  normal failure/timeout conditions; they cannot guarantee upload after runner
  loss or forced job cancellation.
- Combined stdout/stderr is continuously written to
  `pytest-diagnostics/pytest.log`, retaining test failures and watchdog output.
  Pipeline failure stays nonzero, including a failed log writer.
- JUnit output is `pytest-diagnostics/pytest-results.xml` when Pytest completes
  session finalization. Hard termination may prevent XML or the slowest-test
  summary from being written; the log is the primary hang artifact.

An `always()` upload includes **only these two named files**, when available,
with seven-day retention. It never uploads the workspace, environment dumps,
credentials, core files or production data. Test inputs must remain fictional;
diagnostics are not permission to probe production or expose private fixtures.

## Local reproduction

First run `uv sync --locked` in a credential-free checkout. Then run the same
shell command as CI. Command-line options allow shorter synthetic deadlines,
a different artifact directory or selected test IDs; they add no dotenv keys.
Use `bash scripts/run_backend_pytest.sh --help` for the interface.

The ordinary local gate command is unchanged. The diagnostic runner is also
available for bounded reproduction; neither command automatically retries a
failure or changes which assertions are blocking.

## Verification and remaining investigation

`tests/test_backend_pytest_runner.py` exercises the shell runner with isolated,
fictional subprocesses. `tests/test_ci_workflow_gating.py` checks the blocking
gate, nested CI bounds, upload ordering, retention and exact artifact allowlist.
Synthetic failure-path results are not reproduction of the real CI stall.

For the next real stalled run, retain the commit/run ID, last test ID, stack
dump and whether the wait is in collection, setup, call, teardown or shutdown.
Use that evidence to isolate the wait and test order dependence against unchanged
main. #454 remains open until the actual path is fixed, covered by a regression,
and repeated full backend runs on the final commit have recorded timings.
