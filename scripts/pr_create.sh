#!/usr/bin/env bash
# pr_create.sh — Wrapper around `gh pr create` that runs local CI first.
#
# Refuses to invoke `gh pr create` unless scripts/local_ci.sh passes
# every blocking gate. Mirrors the AGENTS.md "before PR submission"
# requirement. Use --dry-run to see what would happen without running
# any gate or contacting GitHub.
#
# Usage:
#   scripts/pr_create.sh --dry-run                         # show the plan
#   scripts/pr_create.sh --dry-run -- --title "..." --body "..."
#   scripts/pr_create.sh -- --title "..." --body "..."     # actually create
#
# Anything after `--` is forwarded to `gh pr create` verbatim.
# Run `gh pr create --help` for the forwarded flag reference.
#
# Exit codes:
#   0  PR created successfully (or dry-run plan printed)
#   1  local CI blocking gate failed; PR not created
#   2  environment / setup error (missing tool, wrong cwd, etc.)
#   3  `gh pr create` itself failed
#
# Notes:
#   - This script does NOT push or commit. It only enforces the
#     "run local CI first" rule. The user still has to push the
#     branch before `gh pr create` will succeed.

set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT" || { echo "pr_create: cannot cd to $REPO_ROOT" >&2; exit 2; }

if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
    C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_DIM=$'\033[2m'
else
    C_RESET=""; C_BOLD=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_DIM=""
fi

log()   { printf '%s\n' "$*"; }
info()  { printf '%s==>%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
ok()    { printf '%s  OK%s    %s\n' "$C_GREEN" "$C_RESET" "$*"; }
fail()  { printf '%s  FAIL%s  %s\n' "$C_RED"   "$C_RESET" "$*"; }

LOCAL_CI="$REPO_ROOT/scripts/local_ci.sh"

print_help() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
}

DRY_RUN=0
PASSTHROUGH=()
SEEN_DOUBLE_DASH=0
while [[ $# -gt 0 ]]; do
    if [[ $SEEN_DOUBLE_DASH -eq 1 ]]; then
        PASSTHROUGH+=("$1")
        shift
        continue
    fi
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --)
            SEEN_DOUBLE_DASH=1
            shift
            ;;
        *)
            echo "pr_create: arguments must come after \`--\` (forwarded to gh pr create)" >&2
            echo "  try: scripts/pr_create.sh --dry-run -- --title \"...\" --body \"...\"" >&2
            exit 2
            ;;
    esac
done

# preflight
need() {
    if ! command -v "$1" >/dev/null 2>&1; then
        fail "required tool not found: $1"
        echo "  install it, then re-run. see AGENTS.md for the tool inventory." >&2
        exit 2
    fi
}
need bash
need gh
need uv
need python

if [[ ! -x "$LOCAL_CI" ]]; then
    fail "scripts/local_ci.sh is missing or not executable: $LOCAL_CI"
    exit 2
fi

log ""
printf '%spr_create.sh — gated wrapper around `gh pr create`%s\n' "$C_BOLD" "$C_RESET"
log "  repo:    $REPO_ROOT"
log "  mode:    $([[ $DRY_RUN -eq 1 ]] && echo 'dry-run (no local CI, no network)' || echo 'enforce (run local CI first)')"

if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
    log "  gh args: ${PASSTHROUGH[*]}"
else
    log "  gh args: <none>  (gh pr create will use its own defaults / TTY prompts)"
fi
log ""

if [[ $DRY_RUN -eq 1 ]]; then
    info "[dry-run] Would now run: bash $LOCAL_CI"
    info "[dry-run] If that exits 0, would then run: gh pr create ${PASSTHROUGH[*]}"
    log ""
    ok "Dry-run plan printed. No gates were executed and no PR was created."
    log ""
    log "To actually create the PR after running local CI, drop --dry-run:"
    log "  scripts/pr_create.sh -- ${PASSTHROUGH[*]:-}"
    exit 0
fi

info "Step 1/2 — running scripts/local_ci.sh (blocking gates must pass)"
log ""
(
    set +e
    bash "$LOCAL_CI"
)
ci_rc=$?
log ""
if [[ $ci_rc -ne 0 ]]; then
    fail "local_ci.sh exited $ci_rc; refusing to call \`gh pr create\`."
    log "  Fix the blocking gates above, or run scripts/local_ci.sh manually to inspect them."
    log "  Use \`scripts/pr_create.sh --dry-run -- <args>\` to skip the gate while debugging."
    exit 1
fi
ok "local_ci.sh passed every blocking gate."

info "Step 2/2 — invoking gh pr create"
log ""
if [[ ${#PASSTHROUGH[@]} -gt 0 ]]; then
    gh pr create "${PASSTHROUGH[@]}"
else
    gh pr create
fi
gh_rc=$?
if [[ $gh_rc -ne 0 ]]; then
    fail "gh pr create exited $gh_rc"
    exit 3
fi

ok "PR created."
exit 0
