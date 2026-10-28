#!/usr/bin/env bash
# LongMemEval benchmark database helper. See RUNBOOK_LAPTOP.md.
#
#   bench_db.sh up                 start the dedicated Postgres+pgvector instance
#   bench_db.sh init               migrate it and stamp the benchmark marker
#   bench_db.sh run -- CMD...      run CMD with the benchmark environment
#   bench_db.sh snapshot FILE      pg_dump -Fc the benchmark database to FILE
#   bench_db.sh restore FILE       recreate the benchmark database from FILE
#   bench_db.sh status             show the instance, marker and row counts
#   bench_db.sh down               stop the instance (data volume kept)
#
# Only the benchmark database is ever addressed: every command goes through the
# daemon-lme-bench Compose project, and DATABASE_URL is always overwritten with
# the benchmark DSN. Provider keys (OPENROUTER_API_KEY) come from the caller's
# environment; nothing is read from any .env file.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
COMPOSE=(docker compose -f "$HERE/compose.bench.yml")
DB_NAME="daemon_lme_bench"
DB_USER="bench"
DB_MARKER="daemon-longmemeval-benchmark-v1"
BENCH_DSN="postgresql://bench:bench@127.0.0.1:55432/${DB_NAME}"
BENCH_HOME="${LME_BENCH_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/daemon-lme-bench}"
KEY_FILE="$BENCH_HOME/encryption.key"
PEPPER_FILE="$BENCH_HOME/auth.pepper"

die() { echo "bench_db: $*" >&2; exit 1; }

psql_bench() {
  "${COMPOSE[@]}" exec -T postgres psql -v ON_ERROR_STOP=1 -U "$DB_USER" "$@"
}

ensure_key() {
  mkdir -p "$BENCH_HOME"
  chmod 700 "$BENCH_HOME"
  if [[ ! -s "$KEY_FILE" ]]; then
    (cd "$REPO" && uv run --locked python -c \
      'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())') >"$KEY_FILE"
    chmod 600 "$KEY_FILE"
    echo "bench_db: generated a benchmark-only encryption key at $KEY_FILE" >&2
  fi
  if [[ ! -s "$PEPPER_FILE" ]]; then
    (umask 077 && python3 -c 'import secrets; print(secrets.token_urlsafe(32))' >"$PEPPER_FILE")
    echo "bench_db: generated a benchmark-only auth pepper at $PEPPER_FILE" >&2
  fi
}

stamp_marker() {
  psql_bench -d postgres -c "COMMENT ON DATABASE ${DB_NAME} IS '${DB_MARKER}'" >/dev/null
}

cmd_up() {
  "${COMPOSE[@]}" up -d --wait postgres
}

cmd_down() {
  "${COMPOSE[@]}" stop postgres
}

cmd_init() {
  ensure_key
  (cd "$REPO" && DATABASE_URL="$BENCH_DSN" PYTHONPATH="$REPO" uv run --locked python scripts/migrate.py)
  stamp_marker
  echo "bench_db: ${DB_NAME} migrated and marked" >&2
}

cmd_run() {
  [[ "${1:-}" == "--" ]] && shift
  [[ $# -gt 0 ]] || die "usage: bench_db.sh run -- CMD..."
  [[ -n "${OPENROUTER_API_KEY:-}" ]] || die "OPENROUTER_API_KEY must be set in the environment"
  [[ -s "$KEY_FILE" && -s "$PEPPER_FILE" ]] || die "no benchmark key or pepper; run 'bench_db.sh init' first"
  [[ ! -e "$REPO/.env" ]] || die "$REPO/.env exists; run the benchmark from a checkout without one"
  cd "$REPO"
  DATABASE_URL="$BENCH_DSN" \
    DAEMON_ENCRYPTION_KEY="$(cat "$KEY_FILE")" \
    DAEMON_AUTH_PEPPER="$(cat "$PEPPER_FILE")" \
    DAEMON_INFERENCE_POLICY="config/inference_policy.production.json" \
    PYTHONPATH="$REPO" \
    exec "$@"
}

cmd_snapshot() {
  local out="${1:-}"
  [[ -n "$out" ]] || die "usage: bench_db.sh snapshot FILE"
  [[ ! -e "$out" ]] || die "$out already exists; snapshots are never overwritten"
  local tmp="${out}.partial"
  "${COMPOSE[@]}" exec -T postgres pg_dump -Fc -U "$DB_USER" -d "$DB_NAME" >"$tmp"
  sync "$tmp"
  mv "$tmp" "$out"
  sha256sum "$out"
}

cmd_restore() {
  local in="${1:-}"
  [[ -s "$in" ]] || die "usage: bench_db.sh restore FILE (missing or empty)"
  psql_bench -d postgres -c "DROP DATABASE IF EXISTS ${DB_NAME} WITH (FORCE)" >/dev/null
  psql_bench -d postgres -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}" >/dev/null
  "${COMPOSE[@]}" exec -T postgres pg_restore --exit-on-error --no-owner \
    -U "$DB_USER" -d "$DB_NAME" <"$in"
  stamp_marker
  echo "bench_db: ${DB_NAME} restored from $in" >&2
}

cmd_status() {
  "${COMPOSE[@]}" ps postgres
  psql_bench -d "$DB_NAME" -At -c \
    "SELECT 'marker=' || coalesce(shobj_description(oid, 'pg_database'), '<none>')
       FROM pg_database WHERE datname = current_database()"
  psql_bench -d "$DB_NAME" -At -c \
    "SELECT 'conversations=' || (SELECT count(*) FROM conversations)
         || ' memories=' || (SELECT count(*) FROM memories)" 2>/dev/null || true
}

sub="${1:-}"
[[ $# -gt 0 ]] && shift
case "$sub" in
  up) cmd_up ;;
  down) cmd_down ;;
  init) cmd_init ;;
  run) cmd_run "$@" ;;
  snapshot) cmd_snapshot "$@" ;;
  restore) cmd_restore "$@" ;;
  status) cmd_status ;;
  *) sed -n '2,12p' "$0" >&2; exit 2 ;;
esac
