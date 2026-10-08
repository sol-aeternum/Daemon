#!/usr/bin/env bash
# Durable chat restart drill (docs/DURABLE_REQUEST_DESIGN.md §17).
#
# Runs a throwaway Compose project (its own name, volumes and networks; no
# published ports except a loopback backend port) from THIS checkout, with
# DURABLE_CHAT_ENABLED on and the deterministic mock LLM (no paid inference,
# no provider keys). Never touches the live `daemon` project, its volumes,
# .env or data. Generated secrets live in a temporary directory that is
# removed afterwards, together with the project's containers and volumes.
#
# Scenarios:
#   A  accepted while the worker is down; the backend restarts; the worker
#      returns and the task completes exactly once; a same-key replay
#      returns the same task.
#   B  the worker is killed mid-stream (SIGKILL); after the lease lapses a
#      restarted worker regenerates the answer to completion.
#   C  an observer reattaches after a backend restart and sees the result.
#
# Usage: scripts/durable_restart_drill.sh [--keep]   (--keep: leave the stack up)
set -euo pipefail

PROJECT="${DRILL_PROJECT:-daemon-drill}"
PORT="${DRILL_PORT:-18080}"
KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1
if [[ "$PROJECT" == "daemon" ]]; then
  echo "refusing to run as the live project name 'daemon'" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -e "$ROOT/.env" ]]; then
  # A checkout with a .env may be the one live services run from: its .daemon
  # directory and configuration must not be touched. Use a clean worktree.
  echo "refusing to run from a checkout with a .env; use a clean worktree" >&2
  exit 2
fi
WORK="$(mktemp -d)"
ENV_FILE="$WORK/drill.env"
OVERRIDE="$WORK/drill.override.yml"
BASE="http://127.0.0.1:$PORT"
LOG="${DRILL_LOG:-${TMPDIR:-/tmp}/daemon-drill-$(date -u +%Y%m%dT%H%M%SZ).log}"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
fail() { log "FAIL: $*"; exit 1; }

# Compose gives the caller's shell variables precedence over --env-file, and
# docker-compose.yml forwards MOCK_LLM and provider keys into the services. A
# shell exporting MOCK_LLM=false or a real key must never reach a provider
# from this synthetically attested stack, so Compose sees only the drill's
# values (plus what it needs to find Docker).
compose() {
  env -i PATH="$PATH" HOME="$HOME" \
    ${DOCKER_HOST:+DOCKER_HOST="$DOCKER_HOST"} \
    ${DOCKER_CONTEXT:+DOCKER_CONTEXT="$DOCKER_CONTEXT"} \
    ${DOCKER_CONFIG:+DOCKER_CONFIG="$DOCKER_CONFIG"} \
    ${XDG_RUNTIME_DIR:+XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"} \
    docker compose -p "$PROJECT" --project-directory "$ROOT" \
    -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" --env-file "$ENV_FILE" "$@"
}

cleanup() {
  local status=$?
  if [[ $KEEP -eq 0 ]]; then
    compose down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  for dir in "${CREATED_DIRS[@]}"; do rmdir "$dir" 2>/dev/null || true; done
  rm -rf "$WORK"
  echo "drill log: $LOG"
  exit $status
}
CREATED_DIRS=()
trap cleanup EXIT

python3 - "$ENV_FILE" <<'PY'
import base64, os, secrets, sys
path = sys.argv[1]
values = {
    "POSTGRES_USER": "daemon",
    "POSTGRES_DB": "daemon",
    "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
    "DAEMON_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "DAEMON_AUTH_PEPPER": secrets.token_urlsafe(48),
    "DAEMON_ENVIRONMENT": "development",
    "DAEMON_DEPLOYMENT_MODE": "self_hosted",
    "MOCK_LLM": "true",
    # The production route policy; its routes are attested synthetically
    # below, which is safe only because the mock LLM never calls a provider.
    "DAEMON_INFERENCE_POLICY": "config/inference_policy.production.json",
    "DURABLE_CHAT_ENABLED": "true",
}
# No credentials of any kind: Compose would otherwise default these to blank
# with a warning each. Blank keys also mean no provider could be reached.
for key in ("OPENROUTER_API_KEY", "VOYAGE_API_KEY", "OPENAI_API_KEY",
            "BRAVE_API_KEY", "XAI_API_KEY", "FAL_KEY", "DAEMON_ADMIN_API_KEY"):
    values[key] = ""
with open(path, "w") as handle:
    for key, value in values.items():
        handle.write(f"{key}={value}\n")
os.chmod(path, 0o600)
PY

# Mount points for the project volumes below: created here, owned by you,
# so Docker does not create them as root inside the checkout.
for dir in data .daemon; do
  if [[ ! -e "$ROOT/$dir" ]]; then mkdir -p "$ROOT/$dir"; CREATED_DIRS+=("$ROOT/$dir"); fi
done

# The containers bind-mount this checkout and run as root. Runtime state
# (data/, .daemon/) goes to project volumes and no bytecode is written, so
# nothing root-owned is left in the checkout (it would break local tests).
cat >"$OVERRIDE" <<YAML
x-drill-state: &drill-state
  volumes:
    - drill_data:/app/data
    - drill_daemon:/app/.daemon
  environment:
    - PYTHONDONTWRITEBYTECODE=1
services:
  migrate:
    <<: *drill-state
  backend:
    <<: *drill-state
    ports: !override
      - "127.0.0.1:$PORT:8000"
    depends_on: !override
      postgres:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
  worker:
    <<: *drill-state
  postgres:
    ports: !reset []
  redis:
    ports: !reset []
volumes:
  drill_data:
  drill_daemon:
YAML

http() { # method path [json] -> body on stdout, status in $HTTP_STATUS
  local method=$1 path=$2 data=${3:-}
  local out
  out=$(curl -sS -o "$WORK/body" -w '%{http_code}' -X "$method" "$BASE$path" \
    -H "Authorization: Bearer ${TOKEN:-}" -H 'Content-Type: application/json' \
    ${data:+--data "$data"})
  HTTP_STATUS=$out
  cat "$WORK/body"
}

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

wait_health() {
  for _ in $(seq 1 120); do
    curl -fsS "$BASE/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  fail "backend did not become healthy"
}

task_field() { http GET "/tasks/$1" | json "d.get('$2')"; }

wait_status() { # task status timeout_s
  local deadline=$((SECONDS + $3)) status=""
  while (( SECONDS < deadline )); do
    status=$(task_field "$1" status)
    [[ "$status" == "$2" ]] && return 0
    sleep 1
  done
  fail "task $1 is '$status', expected '$2' within $3s"
}

submit() { # key message -> task id; the client disconnects right after acceptance
  local key=$1 message=$2 id="" pid
  : >"$WORK/headers-$key"
  curl -sS -N -D "$WORK/headers-$key" -o "$WORK/stream-$key" --max-time 30 \
    -X POST "$BASE/chat" -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -H "Idempotency-Key: $key" \
    -H 'X-Daemon-Client-Features: task-cancel, task-reset' \
    --data "{\"message\": \"$message\"}" >/dev/null 2>&1 &
  pid=$!
  for _ in $(seq 1 200); do
    id=$(grep -i '^x-daemon-task-id:' "$WORK/headers-$key" 2>/dev/null | tr -d '\r' | awk '{print $2}' || true)
    [[ -n "$id" ]] && break
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.05
  done
  kill "$pid" 2>/dev/null || true # the phone goes away
  wait "$pid" 2>/dev/null || true
  if [[ -z "$id" ]]; then
    log "submit $key: no task id; response follows"
    head -c 600 "$WORK/headers-$key" "$WORK/stream-$key" 2>/dev/null | tee -a "$LOG" >&2 || true
  fi
  echo "$id"
}

log "building and starting project '$PROJECT' from $ROOT (port $PORT)"
compose build migrate backend worker >>"$LOG" 2>&1 || fail "build failed (see log)"
compose up -d postgres redis migrate backend worker >>"$LOG" 2>&1 || fail "up failed"
wait_health
for service in backend worker; do
  # Belt and braces: the mock must be what each service actually runs with.
  mock=$(compose exec -T "$service" printenv MOCK_LLM 2>/dev/null | tr -d '\r\n' || true)
  [[ "$mock" == "true" ]] || fail "$service runs with MOCK_LLM='$mock', not the mock"
done

SETUP=""
for _ in $(seq 1 30); do
  # Read inside the container: the file belongs to the container's user.
  SETUP=$(compose exec -T backend cat .daemon/setup-token 2>/dev/null | tr -d '\n' || true)
  [[ -n "$SETUP" ]] && break
  sleep 1
done
[[ -n "$SETUP" ]] || fail "no setup token was written"
TOKEN=$(http POST /v1/auth/setup "{\"setup_token\": \"$SETUP\"}" | json "d['access_token']")
[[ -n "$TOKEN" ]] || fail "setup did not return an access token ($HTTP_STATUS)"
log "signed in as the drill owner"

# Admission requires a ZDR-attested route. A real deployment attests routes by
# checking providers; this throwaway stack has no provider keys and, with the
# mock LLM, sends nothing to a provider, so it records a synthetic
# attestation for each monitored route in its own database (test fixture
# only) and reloads the backend and worker so they see it.
compose exec -T backend python - >"$WORK/routes" <<'PY'
from orchestrator.entitlements.attestation import baseline_fingerprint
from orchestrator.entitlements.policy import load_inference_policy
for route in load_inference_policy().monitorable_routes():
    print(route.route_id, baseline_fingerprint(route))
PY
[[ -s "$WORK/routes" ]] || fail "no monitored routes in the policy"
while read -r route baseline; do
  compose exec -T postgres psql -q -U daemon -d daemon -c \
    "INSERT INTO inference_route_attestations (route_id, baseline_sha256, outcome, reasons)
     VALUES ('$route', '$baseline', 'attested', '{drill_fixture}')" >/dev/null
done <"$WORK/routes"
compose restart backend worker >>"$LOG" 2>&1
wait_health
log "attested $(wc -l <"$WORK/routes") routes synthetically (drill fixture)"
# The mock LLM streams fixed tokens; the saved result is whatever the engine
# persists for it, so the drill checks completion and attempt counts rather
# than exact text.

# --- A: accepted while the worker is down, across a backend restart -------
log "A: stopping the worker, then submitting"
compose stop worker >>"$LOG" 2>&1
KEY_A=$(python3 -c 'import uuid; print(uuid.uuid4())')
TASK_A=$(submit "$KEY_A" "drill A")
[[ -n "$TASK_A" ]] || fail "A: no task id (durable acceptance failed)"
[[ $(task_field "$TASK_A" status) == queued ]] || fail "A: not queued after acceptance"
log "A: task $TASK_A queued; restarting the backend"
compose restart backend >>"$LOG" 2>&1
wait_health
[[ $(task_field "$TASK_A" status) == queued ]] || fail "A: lost across the backend restart"
log "A: still queued after the restart; starting the worker"
compose start worker >>"$LOG" 2>&1
wait_status "$TASK_A" completed 120
CONTENT=$(task_field "$TASK_A" content)
[[ -n "$CONTENT" && "$CONTENT" != None ]] || fail "A: no saved result"
[[ $(task_field "$TASK_A" attempt_count) == 1 ]] || fail "A: ran more than once"
REPLAY=$(submit "$KEY_A" "drill A")
[[ "$REPLAY" == "$TASK_A" ]] || fail "A: same-key replay returned '$REPLAY'"
log "A: PASS (completed once; same-key replay returned the same task)"

# --- B: worker killed mid-stream --------------------------------------------
WORKER=$(compose ps -q worker)
B_DONE=0
for try in 1 2 3; do
  KEY_B=$(python3 -c 'import uuid; print(uuid.uuid4())')
  TASK_B=$(submit "$KEY_B" "drill B")
  [[ -n "$TASK_B" ]] || fail "B: no task id"
  killed=0
  for _ in $(seq 1 200); do
    status=$(task_field "$TASK_B" status)
    if [[ "$status" == running ]]; then
      docker kill "$WORKER" >/dev/null && killed=1
      break
    fi
    [[ "$status" == completed ]] && break
    sleep 0.05
  done
  if [[ $killed -eq 1 ]]; then B_DONE=1; break; fi
  log "B: try $try finished before the kill window; retrying"
done
[[ $B_DONE -eq 1 ]] || fail "B: could not kill the worker mid-stream in 3 tries"
log "B: worker killed mid-stream (task $TASK_B); waiting out the lease, then restarting"
[[ $(task_field "$TASK_B" status) == running ]] || fail "B: not running after the kill"
compose start worker >>"$LOG" 2>&1
wait_status "$TASK_B" completed 180
CONTENT=$(task_field "$TASK_B" content)
[[ -n "$CONTENT" && "$CONTENT" != None ]] || fail "B: no saved result"
OUTCOMES=$(compose exec -T postgres psql -tA -U daemon -d daemon -c \
  "SELECT string_agg(outcome, ',' ORDER BY epoch) FROM task_attempts WHERE task_id = '$TASK_B'")
[[ "$OUTCOMES" == *lost* && "$OUTCOMES" == *completed ]] \
  || fail "B: attempt outcomes were '$OUTCOMES' (expected a lost attempt, then completed)"
log "B: PASS (killed attempt recorded lost; recovered after the lease; outcomes $OUTCOMES)"

# --- C: observer reattaches after a backend restart -----------------------
compose stop worker >>"$LOG" 2>&1
KEY_C=$(python3 -c 'import uuid; print(uuid.uuid4())')
TASK_C=$(submit "$KEY_C" "drill C")
[[ -n "$TASK_C" ]] || fail "C: no task id"
log "C: task $TASK_C queued; restarting the backend before reattaching"
compose restart backend >>"$LOG" 2>&1
wait_health
curl -sS -N --max-time 150 "$BASE/tasks/$TASK_C/events" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'X-Daemon-Client-Features: task-cancel, task-reset' >"$WORK/observe-C" 2>/dev/null &
OBSERVER=$!
sleep 2
compose start worker >>"$LOG" 2>&1
wait "$OBSERVER" || true
grep -q '"status": *"completed"' "$WORK/observe-C" || fail "C: observer did not see completion"
log "C: PASS (reattached observer saw the task complete)"

LEFT=$(find "$ROOT" -path "$ROOT/.venv" -prune -o -path "$ROOT/frontend/node_modules" -prune \
  -o -not -user "$(id -u)" -print 2>/dev/null | head -3)
[[ -z "$LEFT" ]] || fail "files not owned by you were left in the checkout: $LEFT"
log "ALL PASS"
