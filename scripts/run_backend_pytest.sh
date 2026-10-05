#!/usr/bin/env bash
# Backend pytest diagnostics runner — CI hardening for issue #454.
#
# Runs the full, unfiltered pytest suite through the locked uv environment
# under a GNU `timeout` process-group deadline. This is diagnostic hardening
# only: no retries, no assertion changes, no dependency resolution.
#
#   GNU timeout runs: env PYTHONPATH=<repo> PYTHONFAULTHANDLER=1
#   PYTHONUNBUFFERED=1 uv run --no-sync python -m pytest -vv --durations=30
#   -o faulthandler_timeout=<N> --junitxml=<artifact>/pytest-results.xml
#   [user pytest args]
#
# `timeout --signal=ABRT --kill-after=<N>` signals the whole process group, so
# PYTHONFAULTHANDLER prints Python stacks even when the wait happens during
# collection; the kill-after SIGKILL stops signal-resistant survivors. Core
# dumps are disabled inside the timed process. Combined stdout+stderr is
# continuously appended to <artifact>/pytest.log through tee; a failing log
# writer keeps the pipeline exit status nonzero. The pytest exit status and
# GNU timeout's 137 (deadline plus mandatory group kill) stay nonzero. Usage/status
# lines printed here name no variable values beyond runner options and
# artifact paths; no environments, secrets, locals or private data are
# dumped. Linux/GNU coreutils runner, not a portable supervisor.
#
# Exit codes: pytest/pipeline statuses pass through; suite deadlines yield 137;
# 2 CLI usage error; 3 missing tool; 4 artifact/log write failure;
# 6 JUnit XML not written on an otherwise-successful run; 7 internal.

set -uo pipefail

TAG="pytest-diagnostics"

DEFAULT_SUITE_TIMEOUT=1200   # 20m deadline for the whole timed pytest process.
DEFAULT_KILL_AFTER=15        # SIGKILL grace after the deadline signal.
DEFAULT_FAULTHANDLER_TO=120  # pytest per-test faulthandler watchdog (dump only).
DEFAULT_ARTIFACT_DIR="pytest-diagnostics"

usage() {
	cat <<'USAGE'
usage: run_backend_pytest.sh [options] [-- [pytest args...]]

Runs the backend pytest suite under GNU timeout with diagnostics logging.

Options (defaults in parentheses):
  --suite-timeout SECONDS   wall-time deadline for the pytest process group
                            (1200; CI's 20m)
  --kill-after SECONDS      SIGKILL grace after the deadline signal (15)
  --faulthandler-timeout S  pytest faulthandler per-test watchdog in seconds;
                            dump-only, it never kills the suite (120)
  --artifact-dir DIR        directory for pytest.log and pytest-results.xml
                            (pytest-diagnostics)
  --help                    show this help and exit

Everything after the first `--` is passed unchanged to pytest, e.g. one test
node ID for targeted reproduction. With no pytest args the full, unfiltered
suite is collected from the repository root, matching the ordinary CI run.
Option values accept positive integers and decimals (no suffixes, no zero).

Statuses: pytest's own exit status passes through when logging succeeds;
the suite deadline and group kill return 137. A failing log writer or missing
artifact also fails nonzero. Errors print usage-safe messages only.
USAGE
}

# Print a tagged line to the console and append it to the diagnostic log.
# Only usable after artifact setup; pre-flight errors go to stderr instead.
log_line() {
	printf '%s: %s\n' "$TAG" "$*" | tee -a "$log_path"
}

fail_stderr() { # fail_stderr <exit-code> <message...>
	local code="$1"
	shift
	printf '%s: error: %s\n' "$TAG" "$*" >&2
	exit "$code"
}

usage_error() {
	printf '%s: error: %s\n' "$TAG" "$*" >&2
	usage >&2
	exit 2
}

# is_positive <value>: true for plain decimal numbers > 0 (no suffix, no
# exponent, no sign). Fractional seconds are valid for GNU timeout and for
# pytest's faulthandler ini value.
is_positive() {
	[[ $1 =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1
	! [[ $1 =~ ^0+([.]0+)?$ ]] # 0 / 00 / 0.0 / 00.00 are not positive
}

value_error() { # value_error <flag> <value>
	usage_error "$1: '$2' is not a positive number of seconds"
}

# ---- argument parsing ------------------------------------------------------

suite_timeout=$DEFAULT_SUITE_TIMEOUT
kill_after=$DEFAULT_KILL_AFTER
faulthandler_timeout=$DEFAULT_FAULTHANDLER_TO
artifact_dir=$DEFAULT_ARTIFACT_DIR
pytest_args=()
after_separator=false

while (($# > 0)); do
	if $after_separator; then
		pytest_args+=("$1")
		shift
		continue
	fi
	case $1 in
	--)
		after_separator=true
		shift
		;;
	--help | -h)
		usage
		exit 0
		;;
	--suite-timeout | --kill-after | --faulthandler-timeout | --artifact-dir)
		if (($# < 2)); then
			usage_error "option $1 requires a value"
		fi
		case $1 in
			--suite-timeout) suite_timeout=$2 ;;
			--kill-after) kill_after=$2 ;;
			--faulthandler-timeout) faulthandler_timeout=$2 ;;
			--artifact-dir) artifact_dir=$2 ;;
		esac
		shift 2
		;;
	--suite-timeout=* | --kill-after=* | --faulthandler-timeout=* | --artifact-dir=*)
		opt=${1%%=*}
		val=${1#*=}
		case $opt in
			--suite-timeout) suite_timeout=$val ;;
			--kill-after) kill_after=$val ;;
			--faulthandler-timeout) faulthandler_timeout=$val ;;
			--artifact-dir) artifact_dir=$val ;;
		esac
		shift
		;;
	*)
		usage_error "unknown argument: $1"
		;;
	esac
done

is_positive "$suite_timeout" || value_error --suite-timeout "$suite_timeout"
is_positive "$kill_after" || value_error --kill-after "$kill_after"
is_positive "$faulthandler_timeout" || value_error --faulthandler-timeout "$faulthandler_timeout"
[[ -n $artifact_dir ]] || usage_error "--artifact-dir: directory must not be empty"

# ---- locate the repository and toolchain -----------------------------------

# Resolve this script's repository root (works from any caller cwd).
self_dir=$(dirname -- "${BASH_SOURCE[0]}") || fail_stderr 7 "cannot resolve script directory"
repo_root=$(cd -- "$self_dir/.." && pwd -P) ||
	fail_stderr 7 "cannot resolve repository root"

# Artifact paths resolve against the CALLER's cwd, before we cd into the repo.
mkdir -p -- "$artifact_dir" ||
	fail_stderr 4 "cannot create artifact directory: $artifact_dir"
[[ -d $artifact_dir ]] || fail_stderr 4 "artifact path is not a directory: $artifact_dir"
artifact_dir_abs=$(cd -- "$artifact_dir" && pwd -P) ||
	fail_stderr 4 "cannot resolve artifact directory: $artifact_dir"
log_path=$artifact_dir_abs/pytest.log
xml_path=$artifact_dir_abs/pytest-results.xml

for tool in timeout tee uv; do
	command -v "$tool" >/dev/null 2>&1 ||
		fail_stderr 3 "required command not found in PATH: $tool"
done
timeout --version 2>/dev/null | head -n 1 | grep -qi coreutils ||
	fail_stderr 3 "GNU coreutils timeout is required (this runner is Linux/GNU-only)"

# Never mix an earlier run's evidence into the current run.
: > "$log_path" || fail_stderr 4 "cannot initialize diagnostic log"
rm -f -- "$xml_path" || fail_stderr 4 "cannot clear previous JUnit report"
log_line "starting pytest (suite-timeout=${suite_timeout}s kill-after=${kill_after}s faulthandler-timeout=${faulthandler_timeout}s artifact-dir=${artifact_dir_abs})" || exit 4

# GNU timeout needs a duration, which we pass after its own options.
( cd -- "$repo_root" || exit 7
 ulimit -c 0 || exit 7
 # Supervise the whole pipeline, not uv alone. If uv/Python aborts while a
 # descendant holds the pipe open, tee is still inside the deadline. Retain
 # the shell after ABRT so timeout's kill-after cannot be disarmed by uv exit.
 exec timeout --signal=ABRT --kill-after="$kill_after" -- "$suite_timeout" \
     bash -c '
         set -o pipefail
         trap "while :; do sleep 1; done" ABRT
         root=$1; watchdog=$2; xml=$3; log=$4; shift 4
         env PYTHONPATH="$root" PYTHONFAULTHANDLER=1 PYTHONUNBUFFERED=1 \
             uv run --no-sync python -m pytest -vv --durations=30 \
             -o "faulthandler_timeout=$watchdog" --junitxml="$xml" "$@" 2>&1 |
             (trap "" ABRT; exec tee -a "$log")
     ' pytest-pipeline "$repo_root" "$faulthandler_timeout" "$xml_path" \
       "$log_path" "${pytest_args[@]}"
) || pipeline_status=$?
pipeline_status=${pipeline_status:-0}

log_line "pytest process finished (combined exit status $pipeline_status)" || exit 4

if [[ ! -s $log_path ]]; then
	printf '%s: error: diagnostic log was not written or is empty: %s\n' \
		"$TAG" "$log_path" >&2
	exit 4
fi

case $pipeline_status in
	124 | 137 | 130 | 13[4-7])
		if [[ ! -f $xml_path ]]; then
			log_line \
                "JUnit XML was not written; the process was terminated before session finalize" || exit 4
		fi
		;;
	0)
		if [[ ! -f $xml_path ]]; then
			printf '%s: error: pytest considered the run successful but JUnit XML is missing: %s\n' \
				"$TAG" "$xml_path" >&2
			exit 6
		fi
		;;
esac

exit "$pipeline_status"
