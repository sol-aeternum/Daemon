#!/usr/bin/env bash
# local_ci.sh — Run Daemon's quality gates locally before PR submission.
#
# Mirrors the AGENTS.md gate inventory and the GitHub Actions CI workflow
# at .github/workflows/ci.yml. Blocking gates fail this script; inventory
# gates (those marked `continue-on-error: true` in CI) are reported but do
# not block. Pre-existing debt must be tracked in a GitHub issue, not silenced
# here.
#
# Usage:
#   scripts/local_ci.sh                  # run every gate
#   scripts/local_ci.sh backend          # backend gates only
#   scripts/local_ci.sh frontend         # frontend gates only
#   scripts/local_ci.sh aggregate        # feature matrix + pre-commit
#   scripts/local_ci.sh --list           # list gates, then exit
#   scripts/local_ci.sh -h | --help      # show this help and exit
#
# Exit codes:
#   0  all blocking gates passed (inventory gates may have reported issues)
#   1  at least one blocking gate failed
#   2  environment / setup error (missing tool, wrong cwd, etc.)
#
# Notes:
#   - The script changes to the repository root before doing anything.
#   - For frontend gates, `npm ci` is run once before the gate family and
#     counts as inventory (CI does the same; existing node_modules are
#     reused when present).
#   - Frontend `npm run build` may regenerate `frontend/next-env.d.ts`
#     and PWA service-worker artifacts; this regeneration is expected, not a gate failure.

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || { echo "local_ci: cannot cd to $REPO_ROOT" >&2; exit 2; }

# colors only when stdout is a tty, so logs piped to files stay plain
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_BLUE=$'\033[34m'
    C_DIM=$'\033[2m'
else
    C_RESET="" C_BOLD="" C_RED="" C_GREEN="" C_YELLOW="" C_BLUE="" C_DIM=""
fi

log()   { printf '%s\n' "$*"; }
info()  { printf '%s==>%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ok()    { printf '%s  PASS%s  %s\n' "$C_GREEN" "$C_RESET" "$*"; }
fail()  { printf '%s  FAIL%s  %s\n' "$C_RED"   "$C_RESET" "$*"; }
warn()  { printf '%s  WARN%s  %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
dim()   { printf '%s%s%s\n' "$C_DIM" "$*" "$C_RESET"; }

read -r -d '' GATE_TABLE <<'EOF' || true
backend|ruff-check|blocking|uv run ruff check .
backend|ruff-format|blocking|uv run ruff format --check .
backend|basedpyright|blocking|uv run basedpyright --level error
backend|pytest-collect|blocking|PYTHONPATH=. uv run pytest --collect-only -q
backend|bandit|inventory|uv run bandit -r orchestrator providers scripts tests
backend|pip-audit|inventory|uv run pip-audit
backend|pytest|blocking|PYTHONPATH=. uv run pytest -q
frontend|npm-ci|inventory|npm ci --prefix frontend --no-audit --no-fund --prefer-offline
frontend|type-check|blocking|npm --prefix frontend run type-check
frontend|lint|blocking|npm --prefix frontend run lint
frontend|format-check|blocking|npm --prefix frontend run format:check
frontend|audit-ci|inventory|npm --prefix frontend run audit:ci
frontend|test-run|blocking|npm --prefix frontend run test:run
frontend|build|blocking|npm --prefix frontend run build
aggregate|feature-matrix|blocking|python scripts/lint_feature_matrix.py
aggregate|pre-commit|blocking|uv run pre-commit run --all-files
EOF

print_help() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
}

print_list() {
    log "Gates that scripts/local_ci.sh would run:"
    log ""
    printf '  %-10s %-15s %-10s  %s\n' "FAMILY" "GATE" "KIND" "COMMAND"
    printf '  %-10s %-15s %-10s  %s\n' "-------" "----" "----" "-------"
    while IFS='|' read -r family gate kind cmd; do
        [[ -z "$family" || "$family" == \#* ]] && continue
        printf '  %-10s %-15s %-10s  %s\n' "$family" "$gate" "$kind" "$cmd"
    done <<< "$GATE_TABLE"
    log ""
    log "blocking  = exits non-zero on failure"
    log "inventory = reported; failure does NOT block (mirrors CI continue-on-error)"
}

FAMILIES=()
LIST_ONLY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        --list)
            LIST_ONLY=1
            shift
            ;;
        backend|frontend|aggregate)
            FAMILIES+=("$1")
            shift
            ;;
        --)
            shift
            FAMILIES+=("backend" "frontend" "aggregate")
            break
            ;;
        *)
            echo "local_ci: unknown argument: $1" >&2
            echo "  try: scripts/local_ci.sh --help" >&2
            exit 2
            ;;
    esac
done

if [[ ${#FAMILIES[@]} -eq 0 ]]; then
    FAMILIES=(backend frontend aggregate)
fi

if [[ $LIST_ONLY -eq 1 ]]; then
    print_list
    exit 0
fi

need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "required tool not found: $1"
        echo "  install it, then re-run. see AGENTS.md for the tool inventory." >&2
        exit 2
    fi
}
need bash
need uv
need python

declare -a FAILED_BLOCKING=()
declare -a REPORTED_INVENTORY=()
declare -a PASSED=()
START_TS=$(date +%s)

run_gate() {
    local family="$1" gate="$2" kind="$3" cmd="$4"
    info "[$family] $gate ($kind)"
    dim "  $ $cmd"
    (
        set +e
        eval "$cmd"
    )
    local rc=$?
    if [[ $rc -eq 0 ]]; then
        ok "$gate"
        PASSED+=("$family/$gate")
    elif [[ "$kind" == "inventory" ]]; then
        warn "$gate  (inventory — does not block, exit=$rc)"
        REPORTED_INVENTORY+=("$family/$gate (exit=$rc)")
    else
        fail "$gate  (blocking, exit=$rc)"
        FAILED_BLOCKING+=("$family/$gate (exit=$rc)")
    fi
}

want_family() {
    local needle="$1"
    local f
    for f in "${FAMILIES[@]}"; do
        [[ "$f" == "$needle" ]] && return 0
    done
    return 1
}

log ""
printf '%slocal_ci.sh — Daemon quality-gate runner%s\n' "$C_BOLD" "$C_RESET"
log "  repo:    $REPO_ROOT"
log "  families: ${FAMILIES[*]}"
log ""

if want_family backend; then
    info "Backend gates"
    log ""
    while IFS='|' read -r family gate kind cmd; do
        [[ "$family" == "backend" ]] || continue
        [[ -n "$family" ]] || continue
        run_gate "$family" "$gate" "$kind" "$cmd"
    done <<< "$GATE_TABLE"
    log ""
fi

if want_family frontend; then
    info "Frontend gates"
    log ""
    while IFS='|' read -r family gate kind cmd; do
        [[ "$family" == "frontend" ]] || continue
        [[ -n "$family" ]] || continue
        run_gate "$family" "$gate" "$kind" "$cmd"
    done <<< "$GATE_TABLE"
    log ""
fi

if want_family aggregate; then
    info "Aggregate gates"
    log ""
    while IFS='|' read -r family gate kind cmd; do
        [[ "$family" == "aggregate" ]] || continue
        [[ -n "$family" ]] || continue
        run_gate "$family" "$gate" "$kind" "$cmd"
    done <<< "$GATE_TABLE"
    log ""
fi

ELAPSED=$(( $(date +%s) - START_TS ))

log ""
printf '%sSummary%s (in %ss)\n' "$C_BOLD" "$C_RESET" "$ELAPSED"
log "  passed (any kind):    ${#PASSED[@]}"
log "  blocking failures:    ${#FAILED_BLOCKING[@]}"
log "  inventory reports:    ${#REPORTED_INVENTORY[@]}"

if [[ ${#FAILED_BLOCKING[@]} -gt 0 ]]; then
    log ""
    printf '%sBlocking failures:%s\n' "$C_RED" "$C_RESET"
    for g in "${FAILED_BLOCKING[@]}"; do
        log "  - $g"
    done
    log ""
    log "Fix the blocking failures above, then re-run scripts/local_ci.sh."
    exit 1
fi

if [[ ${#REPORTED_INVENTORY[@]} -gt 0 ]]; then
    log ""
    printf '%sInventory reports (non-blocking):%s\n' "$C_YELLOW" "$C_RESET"
    for g in "${REPORTED_INVENTORY[@]}"; do
        log "  - $g"
    done
    log ""
    log "These gates are marked continue-on-error in CI for legacy reasons."
    log "Pre-existing debt should be tracked in a GitHub issue, not silenced here."
fi

log ""
ok "All blocking gates passed."
exit 0
