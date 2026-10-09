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
#   B  a file-gated synthetic provider is killed only after partial output is
#      committed. SQL expires its lease; a second authenticated client sees
#      the exact recovered result, notice, settlements and stale-worker fence.
#   C  an observer reattaches after a backend restart while Redis publications
#      are dropped; durable lifecycle/tool events and the result are recovered.
#   D  a synthetic material effect is performed once before a worker kill;
#      recovery preserves its evidence at needs_attention without repeating it.
#
# Usage: scripts/durable_restart_drill.sh [--keep]   (--keep: leave the stack up)
# Each run uses its own Compose project (daemon-drill-<random>, or
# DRILL_PROJECT), which must not exist yet; only that project is removed.
set -euo pipefail

PROJECT="${DRILL_PROJECT:-daemon-drill-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')}"
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
BASE="http://127.0.0.1:$PORT"
LOG="${DRILL_LOG:-${TMPDIR:-/tmp}/daemon-drill-$(date -u +%Y%m%dT%H%M%SZ).log}"

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"; }
fail() {
  log "FAIL: $*"
  # Synthetic-only runtime diagnostics must survive verified disposal so a
  # failing fixture can be diagnosed without retaining an enabled stack.
  compose logs --no-color --tail 150 backend worker >>"$LOG" 2>&1 || true
  exit 1
}

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
    ${DOCKER_TLS_VERIFY:+DOCKER_TLS_VERIFY="$DOCKER_TLS_VERIFY"} \
    ${DOCKER_CERT_PATH:+DOCKER_CERT_PATH="$DOCKER_CERT_PATH"} \
    ${XDG_RUNTIME_DIR:+XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"} \
    docker compose -p "$PROJECT" --project-directory "$ROOT" \
    -f "$ROOT/docker-compose.yml" -f "$OVERRIDE" --env-file "$ENV_FILE" "$@"
}

# Lists the project's Docker resources of one kind; fails (never prints
# nothing) when Docker cannot be asked.
project_resources() { # kind
  local all=()
  [[ $1 == container ]] && all=(--all)
  docker "$1" ls -q "${all[@]}" --filter "label=com.docker.compose.project=$PROJECT"
}

SCENARIOS_PASSED=0
cleanup() {
  local status=$? kind left pid teardown_ok=1
  # Only this shell's still-running children (for example an observer curl),
  # never unrelated processes. Stop them before disposing of their files.
  for pid in $(jobs -pr); do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
  if [[ $KEEP -eq 0 ]]; then
    # Teardown is part of the result: a stack left running (flag on, mock
    # stack, volumes) fails the drill with the command to remove it.
    if ! compose down -v --remove-orphans >>"$LOG" 2>&1; then
      log "FAIL: teardown failed; remove it with: docker compose -p $PROJECT down -v --remove-orphans"
      status=1
      teardown_ok=0
    fi
    for kind in container volume network; do
      if ! left=$(project_resources "$kind" 2>>"$LOG") || [[ -n "$left" ]]; then
        log "FAIL: project $PROJECT still has a $kind (or Docker could not be asked); remove it with: docker compose -p $PROJECT down -v --remove-orphans"
        status=1
        teardown_ok=0
        break
      fi
    done
  fi
  for dir in "${CREATED_DIRS[@]}"; do rmdir "$dir" 2>/dev/null || true; done
  if [[ $KEEP -eq 0 && $teardown_ok -eq 1 ]]; then
    rm -rf "$WORK"
  else
    # Keep the generated, permission-restricted fixture configuration until
    # its resources are disposed. Without it Compose cannot parse the required
    # DB password, making recovery after a Docker failure unusable.
    log "retained disposable teardown configuration: $WORK"
    printf 'recovery: env -i PATH=%q HOME=%q docker compose -p %q --project-directory %q -f %q -f %q --env-file %q down -v --remove-orphans\n' \
      "$PATH" "$HOME" "$PROJECT" "$ROOT" "$ROOT/docker-compose.yml" "$OVERRIDE" "$ENV_FILE"
    printf 'after verified teardown remove retained configuration: %q\n' "$WORK"
  fi
  rmdir "$LOCK" 2>/dev/null || true
  if [[ $status -eq 0 && $SCENARIOS_PASSED -eq 1 ]]; then
    if [[ $KEEP -eq 1 ]]; then
      log "ALL PASS (stack kept as project $PROJECT; teardown not verified)"
    else
      log "ALL PASS (project $PROJECT removed and verified gone)"
    fi
  fi
  echo "drill log: $LOG"
  exit $status
}

# Teardown runs "down -v", which deletes the project's volumes: this run must
# own the project. Reserve its name first (two runs with the same name cannot
# both pass), then refuse a name that already has any resource, failing
# closed if Docker cannot be asked. All before the teardown trap exists.
LOCK="${TMPDIR:-/tmp}/$PROJECT.drill-lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  echo "refusing to run: another drill holds project '$PROJECT' ($LOCK)" >&2
  exit 2
fi
for kind in container volume network; do
  if ! existing=$(project_resources "$kind"); then
    rmdir "$LOCK"
    echo "refusing to run: could not list Docker ${kind}s for project '$PROJECT'" >&2
    exit 2
  fi
  if [[ -n "$existing" ]]; then
    rmdir "$LOCK"
    echo "refusing to run: Compose project '$PROJECT' already has a $kind." >&2
    echo "Remove it, or set DRILL_PROJECT to an unused name." >&2
    exit 2
  fi
done
# The override below uses !override and !reset (Compose 2.24.4 or later).
COMPOSE_VERSION=$(docker compose version --short 2>/dev/null | sed 's/^v//' || true)
if ! printf '2.24.4\n%s\n' "$COMPOSE_VERSION" | sort -VC; then
  rmdir "$LOCK"
  echo "refusing to run: Docker Compose 2.24.4 or later is required (found '${COMPOSE_VERSION:-none}')" >&2
  exit 2
fi

WORK="$(mktemp -d)"
ENV_FILE="$WORK/drill.env"
OVERRIDE="$WORK/drill.override.yml"
CREATED_DIRS=()
trap cleanup EXIT
log "project $PROJECT reserved"

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
    command: ["python", "scripts/durable_restart_fixture.py", "worker"]
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
    if [[ "$status" == failed || "$status" == cancelled || "$status" == needs_attention ]]; then
      fail "task $1 ended at '$status', expected '$2'"
    fi
    sleep 1
  done
  fail "task $1 is '$status', expected '$2' within $3s"
}

wait_partial() { # task: the provider gate prevents completion while this polls
  local deadline=$((SECONDS + 60)) content=""
  while (( SECONDS < deadline )); do
    content=$(task_field "$1" content)
    [[ "$content" == "(drill) " ]] && return 0
    sleep 0.05
  done
  fail "task $1 did not commit the gated partial output (last '$content')"
}

control() { compose exec -T backend python -c "from pathlib import Path; Path('/app/.daemon/durable-drill/$1').touch()"; }
proof() { compose exec -T backend python scripts/durable_restart_assertions.py "$@"; }

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
compose up -d postgres redis migrate backend >>"$LOG" 2>&1 || fail "up failed"
wait_health
for service in backend; do
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
compose exec -T backend python -c "from pathlib import Path; p=Path('/app/.daemon/durable-drill'); p.mkdir(); (p/'permit').write_text('disposable synthetic fixture only')"
# Fixture-only issuance in this disposable database, using the normal token
# helper and a distinct device/session. No new auth endpoint or live account.
TOKEN_TWO=$(proof session)
[[ -n "$TOKEN_TWO" && "$TOKEN_TWO" != "$TOKEN" ]] || fail "second client was not issued independently"

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
compose restart backend >>"$LOG" 2>&1
wait_health
log "attested $(wc -l <"$WORK/routes") routes synthetically (drill fixture)"

# --- A: accepted while the worker is down, across a backend restart -------
log "A: stopping the worker, then submitting"
KEY_A=$(python3 -c 'import uuid; print(uuid.uuid4())')
TASK_A=$(submit "$KEY_A" "drill A")
[[ -n "$TASK_A" ]] || fail "A: no task id (durable acceptance failed)"
[[ $(task_field "$TASK_A" status) == queued ]] || fail "A: not queued after acceptance"
log "A: task $TASK_A queued; restarting the backend"
compose restart backend >>"$LOG" 2>&1
wait_health
[[ $(task_field "$TASK_A" status) == queued ]] || fail "A: lost across the backend restart"
log "A: still queued after the restart; starting the worker"
compose up -d worker >>"$LOG" 2>&1
[[ $(compose exec -T worker printenv MOCK_LLM | tr -d '\r\n') == true ]] || fail "worker mock setting overridden"
wait_status "$TASK_A" completed 120
[[ $(compose exec -T worker cat .daemon/durable-drill/installed) == "guarded synthetic transport" ]] || fail "worker did not install the synthetic guarded transport"
CONTENT=$(task_field "$TASK_A" content)
[[ "$CONTENT" == "(drill) committed answer A" ]] || fail "A: saved result differs from mock output"
[[ $(task_field "$TASK_A" attempt_count) == 1 ]] || fail "A: ran more than once"
REPLAY=$(submit "$KEY_A" "drill A")
[[ "$REPLAY" == "$TASK_A" ]] || fail "A: same-key replay returned '$REPLAY'"
log "A: PASS (completed once; same-key replay returned the same task)"

# --- B: deterministic post-persistence worker kill -------------------------------------
WORKER=$(compose ps -q worker)
KEY_B=$(python3 -c 'import uuid; print(uuid.uuid4())')
TASK_B=$(submit "$KEY_B" "drill B")
[[ -n "$TASK_B" ]] || fail "B: no task id"
wait_partial "$TASK_B"
docker kill "$WORKER" >/dev/null
log "B: killed only after committed partial output (task $TASK_B)"
[[ $(task_field "$TASK_B" status) == running ]] || fail "B: not running after the kill"
proof expire "$TASK_B"
compose start worker >>"$LOG" 2>&1
TOKEN_ONE=$TOKEN
TOKEN=$TOKEN_TWO
wait_status "$TASK_B" completed 180
CONTENT=$(task_field "$TASK_B" content)
[[ "$CONTENT" == "(drill) committed answer B" ]] || fail "B: second client saw different committed text"
[[ $(submit "$KEY_B" "drill B") == "$TASK_B" ]] || fail "B: second-client same-key replay changed task"
proof B "$TASK_B" | tee -a "$LOG"
TOKEN=$TOKEN_ONE
log "B: PASS (second authenticated device, persisted partial, lost/completed, settlements, stale fencing)"

# --- C: observer reattaches after a backend restart -----------------------
compose stop worker >>"$LOG" 2>&1
KEY_C=$(python3 -c 'import uuid; print(uuid.uuid4())')
TASK_C=$(submit "$KEY_C" "drill C")
[[ -n "$TASK_C" ]] || fail "C: no task id"
log "C: task $TASK_C queued; restarting the backend before reattaching"
compose restart backend >>"$LOG" 2>&1
wait_health
control drop-redis
curl -sS -N --max-time 150 "$BASE/tasks/$TASK_C/events" \
  -H "Authorization: Bearer $TOKEN_TWO" \
  -H 'X-Daemon-Client-Features: task-cancel, task-reset' >"$WORK/observe-C" 2>/dev/null &
OBSERVER=$!
compose start worker >>"$LOG" 2>&1
wait_partial "$TASK_C"
control release-C
wait "$OBSERVER" || true
python3 - "$WORK/observe-C" <<'PY'
import json, sys
frames = []
for block in open(sys.argv[1]).read().split('\n\n'):
    for line in block.splitlines():
        if line.startswith('data:'):
            frames.append(json.loads(line[5:]))
assert any(f.get('type') == 'final' and f['data'].get('text') == '(drill) committed answer C' for f in frames), 'committed result not recovered'
progress = [f for f in frames if f.get('type') in ('tool_call', 'tool_result')]
assert len(progress) == 2, 'tool progress missing or duplicated after Redis gap'
# calculate's recognized read-only result is successful without a success flag.
# Material uncertainty remains conservative; replay preserves this exact evidence.
assert progress[-1]['data'].get('outcome') == 'succeeded', 'truthful persisted tool outcome missing'
seqs = [f['data']['event_seq'] for f in frames if 'event_seq' in f.get('data', {}) and (f['data'].get('lifecycle_kind') or f.get('type') in ('tool_call', 'tool_result'))]
assert seqs and seqs == sorted(set(seqs)), 'durable cursor order/deduplication failed'
assert any(f['data'].get('lifecycle_kind') == 'attempt_started' for f in frames), 'lifecycle evidence missing'
PY
log "C: PASS (second client recovered lifecycle, tool outcome and exact result across Redis delivery gap)"
compose exec -T backend python -c "from pathlib import Path; Path('/app/.daemon/durable-drill/drop-redis').unlink()"

# --- D: never automatically repeat a performed material effect -------------------------
KEY_D=$(python3 -c 'import uuid; print(uuid.uuid4())')
TASK_D=$(submit "$KEY_D" "drill D")
[[ -n "$TASK_D" ]] || fail "D: no task id"
effect_done=0
for _ in $(seq 1 200); do
  count=$(compose exec -T postgres psql -tA -U daemon -d daemon -c \
    "SELECT count(*) FROM task_operations WHERE task_id = '$TASK_D' AND outcome = 'succeeded'")
  if [[ "$count" == 1 ]]; then effect_done=1; break; fi
  sleep 0.05
done
[[ "$effect_done" == 1 ]] || fail "D: synthetic effect did not commit its outcome"
docker kill "$(compose ps -q worker)" >/dev/null
proof expire "$TASK_D"
compose start worker >>"$LOG" 2>&1
wait_status "$TASK_D" needs_attention 120
proof D "$TASK_D" | tee -a "$LOG"
log "D: PASS (performed once; needs_attention preserves evidence, no automatic retry)"

LEFT=$(find "$ROOT" -path "$ROOT/.venv" -prune -o -path "$ROOT/frontend/node_modules" -prune \
  -o -not -user "$(id -u)" -print 2>/dev/null | head -3)
[[ -z "$LEFT" ]] || fail "files not owned by you were left in the checkout: $LEFT"
log "all scenarios passed; tearing down"
SCENARIOS_PASSED=1
