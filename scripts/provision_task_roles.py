"""Create the split task roles and apply the reviewed exact privilege maps.

Migration 050 makes backend/worker startup require separate task acceptor (A)
and worker (W) LOGIN roles (docs/TASK_STARTUP_COMPOSITION.md). This tool is the
only supported way to create them from the pinned maps in config/task_privileges:

  plan    print the grant SQL for the given role names (no connection, no secrets)
  apply   create both roles and apply the maps in ONE transaction, verify the
          effective catalog privileges, and commit only if verification passes
  verify  re-check an existing deployment against the maps (read-only)

Run from the repository root as ``python -m scripts.provision_task_roles``.

The owner connection string and both passwords are read from user-owned regular
files (mode 0600, no symlinks). Passwords are sent as client-computed
SCRAM-SHA-256 verifiers, so plaintext never reaches the server or its logs.
Output is limited to fixed status codes and counts.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Any

from orchestrator.tasks.composition import _RESULTS as _PROTECTED_RESULTS
from scripts import migrate

ROOT = Path(__file__).resolve().parents[1]
MAPS = ROOT / "config" / "task_privileges"

_ROLE = re.compile(r"[a-z_][a-z0-9_]{0,62}\Z")
_TABLE = re.compile(r"public\.[a-z_][a-z0-9_]*\Z")
_COLUMN = re.compile(r"[a-z_][a-z0-9_]*\Z")
_FUNCTION = re.compile(r"public\.[a-z_][a-z0-9_]*\([a-z ,]*\)\Z")
_TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
# The protected task functions, as startup's own schema check catalogues them.
PROTECTED_FUNCTIONS = frozenset(_PROTECTED_RESULTS)
_ROLE_ATTRIBUTES = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
SCRAM_ITERATIONS = 4096
SECRET_LIMIT = 4096


class ProvisionError(Exception):
    """A fixed, content-free refusal code."""


def proposal_sha256(value: dict[str, Any]) -> str:
    body = {k: v for k, v in value.items() if k != "proposal_sha256"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _checked_map(name: str) -> dict[str, Any]:
    value = json.loads((MAPS / f"{name}.json").read_text())
    if proposal_sha256(value) != value.get("proposal_sha256"):
        raise ProvisionError(f"{name}_map_digest_mismatch")
    for entry in value["ordinary_tables"]:
        if _TABLE.fullmatch(entry["table"]) is None:
            raise ProvisionError(f"{name}_map_invalid_table")
        if not set(entry["table_privileges"]) <= set(_TABLE_PRIVILEGES):
            raise ProvisionError(f"{name}_map_invalid_privilege")
        for column in entry.get("update_columns", []) + entry.get("insert_columns", []):
            if _COLUMN.fullmatch(column) is None:
                raise ProvisionError(f"{name}_map_invalid_column")
    for sequence in value.get("sequences_usage_only", []):
        if _TABLE.fullmatch(sequence) is None:
            raise ProvisionError(f"{name}_map_invalid_sequence")
    for function in functions(value):
        if _FUNCTION.fullmatch(function) is None:
            raise ProvisionError(f"{name}_map_invalid_function")
    return value


def load_maps() -> tuple[dict[str, Any], dict[str, Any]]:
    """(acceptor, worker), each verified against its own proposal digest."""
    return _checked_map("acceptor"), _checked_map("worker")


def functions(value: dict[str, Any]) -> list[str]:
    return (
        value.get("function_execute", [])
        + value.get("ordinary_helper_execute", [])
        + value.get("worker_public_entrypoints_execute", [])
    )


def _role(name: str) -> str:
    if _ROLE.fullmatch(name) is None:
        raise ProvisionError("invalid_role_name")
    return name


def grant_statements(value: dict[str, Any], role: str) -> list[str]:
    """Exact grants for one role; identifiers come only from the checked map."""
    role = _role(role)
    statements = [f"GRANT USAGE ON SCHEMA public TO {role}"]
    for entry in value["ordinary_tables"]:
        table = entry["table"]
        if entry["table_privileges"]:
            statements.append(
                f"GRANT {','.join(entry['table_privileges'])} ON TABLE {table} TO {role}"
            )
        if entry.get("update_columns"):
            statements.append(
                f"GRANT UPDATE({','.join(entry['update_columns'])}) ON TABLE {table} TO {role}"
            )
        if entry.get("insert_columns"):
            statements.append(
                f"GRANT INSERT({','.join(entry['insert_columns'])}) ON TABLE {table} TO {role}"
            )
    statements.extend(
        f"GRANT USAGE ON SEQUENCE {sequence} TO {role}"
        for sequence in value.get("sequences_usage_only", [])
    )
    statements.extend(f"GRANT EXECUTE ON FUNCTION {f} TO {role}" for f in functions(value))
    return statements


def scram_keys(password: str, salt: bytes, iterations: int) -> tuple[bytes, bytes, bytes]:
    """(ClientKey, StoredKey, ServerKey) per RFC 5802 with SHA-256 (RFC 7677)."""
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return client_key, hashlib.sha256(client_key).digest(), server_key


def scram_verifier(password: str, *, salt: bytes | None = None) -> str:
    """PostgreSQL's stored SCRAM-SHA-256 form; the plaintext never leaves this process."""
    # Printable ASCII avoids SASLprep normalisation differences with the server.
    if not 16 <= len(password) <= 1024 or not all(33 <= ord(c) <= 126 for c in password):
        raise ProvisionError("invalid_password")
    salt = salt if salt is not None else secrets.token_bytes(16)
    _client, stored, server = scram_keys(password, salt, SCRAM_ITERATIONS)
    b64 = lambda raw: base64.b64encode(raw).decode("ascii")  # noqa: E731
    return f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{b64(salt)}${b64(stored)}:{b64(server)}"


def read_secret(path: str) -> str:
    """A user-owned regular file, mode 0600 or stricter, no symlink, small."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > SECRET_LIMIT
        ):
            raise ProvisionError("unsafe_secret_file")
        value = os.read(fd, SECRET_LIMIT + 1).decode("utf-8").strip()
    finally:
        os.close(fd)
    if not value:
        raise ProvisionError("empty_secret_file")
    return value


def expected_migrations() -> list[str]:
    """The migration runner's own complete set, in its apply order."""
    names = sorted(p.name for p in migrate.MIGRATIONS_DIR.glob("*.sql"))
    if not names:
        raise ProvisionError("migrations_unavailable")
    return names


async def require_complete_schema(conn: Any) -> None:
    """The maps describe the full chain; refuse any other migration history.

    Uses the runner's own bookkeeping read (``migrate.get_applied_migrations``):
    the recorded set must equal the file set exactly (filenames only; the runner
    records no checksum, and ``filename`` is UNIQUE). A hole, an unknown/newer
    row or a missing table refuses. Set equality is independent of the
    database collation. The caller's transaction also holds the runner's
    advisory lock (transaction scope), so no migration can apply before COMMIT.
    """
    expected = set(expected_migrations())
    if not conn.is_in_transaction():
        raise ProvisionError("transaction_required")
    if not await conn.fetchval(
        "SELECT pg_catalog.pg_try_advisory_xact_lock($1)", migrate.MIGRATION_ADVISORY_LOCK_ID
    ):
        raise ProvisionError("migration_in_progress")
    if await conn.fetchval("SELECT pg_catalog.to_regclass('_migrations')") is None:
        raise ProvisionError("migration_history_incomplete")
    if await migrate.get_applied_migrations(conn) != expected:
        raise ProvisionError("migration_history_incomplete")


async def verify(
    conn: Any, acceptor_map: dict, worker_map: dict, acceptor: str, worker: str
) -> None:
    """Effective catalog privileges must equal the maps exactly.

    Every public table, view and sequence is checked, so a privilege outside the
    map (including one reached through role membership) refuses. Functions are
    checked positively for mapped entries and negatively for the 18 protected
    task functions startup catalogues; other functions keep PostgreSQL's default
    PUBLIC EXECUTE.

    Privileges only: this does NOT check migration history. Deployment
    verification is ``verify_deployment`` (the ``verify`` command), and
    ``apply`` checks history itself.
    """
    acceptor, worker = _role(acceptor), _role(worker)
    if acceptor == worker:
        raise ProvisionError("roles_not_distinct")
    for role in (acceptor, worker):
        row = await conn.fetchrow(
            "SELECT rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole "
            "OR rolreplication OR rolbypassrls) AS safe, "
            "NOT EXISTS(SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid) AS no_membership, "
            "NOT EXISTS(SELECT 1 FROM pg_catalog.pg_database d WHERE d.datname=current_database() "
            "AND pg_catalog.pg_has_role(r.oid,d.datdba,'MEMBER')) AS not_owner, "
            "pg_catalog.has_database_privilege(r.oid,current_database(),'CONNECT') AS can_connect, "
            "NOT pg_catalog.has_schema_privilege(r.oid,'public','CREATE') AS no_create "
            "FROM pg_catalog.pg_roles r WHERE r.rolname=$1",
            role,
        )
        if row is None or not all(row.values()):
            raise ProvisionError("role_attributes_refused")
    if await conn.fetchval(
        "SELECT pg_catalog.pg_has_role($1::name,$2::name,'MEMBER') "
        "OR pg_catalog.pg_has_role($2::name,$1::name,'MEMBER')",
        acceptor,
        worker,
    ):
        raise ProvisionError("roles_share_membership")
    relations = await conn.fetch(
        "SELECT 'public.'||c.relname AS name, c.relkind FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f','S') ORDER BY 1"
    )
    for role, value in ((acceptor, acceptor_map), (worker, worker_map)):
        tables = {e["table"]: e for e in value["ordinary_tables"]}
        sequences = set(value.get("sequences_usage_only", []))
        present = {r["name"] for r in relations}
        if not set(tables) <= present or not sequences <= present:
            raise ProvisionError("mapped_relation_missing")
        for relation in relations:
            name = relation["name"]
            if relation["relkind"] == "S":
                for privilege, expected in (
                    ("USAGE", name in sequences),
                    ("SELECT", False),
                    ("UPDATE", False),
                ):
                    if (
                        await conn.fetchval(
                            "SELECT pg_catalog.has_sequence_privilege($1::name,$2,$3)",
                            role,
                            name,
                            privilege,
                        )
                        is not expected
                    ):
                        raise ProvisionError("sequence_privileges_differ")
                continue
            entry = tables.get(name, {"table_privileges": []})
            granted = set(entry["table_privileges"])
            for privilege in _TABLE_PRIVILEGES:
                if await conn.fetchval(
                    "SELECT pg_catalog.has_table_privilege($1::name,$2,$3)", role, name, privilege
                ) is not (privilege in granted):
                    raise ProvisionError("table_privileges_differ")
            columns = await conn.fetch(
                "SELECT attname FROM pg_catalog.pg_attribute "
                "WHERE attrelid=$1::regclass AND attnum>0 AND NOT attisdropped",
                name,
            )
            for column in columns:
                column_name = column["attname"]
                for privilege, expected in (
                    ("SELECT", "SELECT" in granted),
                    ("REFERENCES", "REFERENCES" in granted),
                    (
                        "UPDATE",
                        "UPDATE" in granted or column_name in entry.get("update_columns", []),
                    ),
                    (
                        "INSERT",
                        "INSERT" in granted or column_name in entry.get("insert_columns", []),
                    ),
                ):
                    if (
                        await conn.fetchval(
                            "SELECT pg_catalog.has_column_privilege($1::name,$2,$3,$4)",
                            role,
                            name,
                            column_name,
                            privilege,
                        )
                        is not expected
                    ):
                        raise ProvisionError("column_privileges_differ")
        mapped = set(functions(value))
        for function in mapped:
            if (
                await conn.fetchval(
                    "SELECT pg_catalog.has_function_privilege($1::name,$2,'EXECUTE')",
                    role,
                    function,
                )
                is not True
            ):
                raise ProvisionError("function_privileges_differ")
        protected = await conn.fetch(
            "SELECT p.oid::regprocedure::text AS signature FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='public' AND p.proname=ANY($1::text[])",
            sorted(PROTECTED_FUNCTIONS),
        )
        if len(protected) != len(PROTECTED_FUNCTIONS):
            raise ProvisionError("protected_function_catalogue_differs")
        for row in protected:
            signature = "public." + row["signature"].removeprefix("public.")
            if await conn.fetchval(
                "SELECT pg_catalog.has_function_privilege($1::name,$2::regprocedure,'EXECUTE')",
                role,
                row["signature"],
            ) is not (signature in mapped):
                raise ProvisionError("protected_function_privileges_differ")


async def apply(
    conn: Any,
    acceptor: str,
    worker: str,
    acceptor_verifier: str,
    worker_verifier: str,
) -> None:
    """Create both roles and apply the maps atomically; verify before COMMIT."""
    acceptor_map, worker_map = load_maps()
    acceptor, worker = _role(acceptor), _role(worker)
    if acceptor == worker:
        raise ProvisionError("roles_not_distinct")
    for verifier in (acceptor_verifier, worker_verifier):
        if not verifier.startswith(f"SCRAM-SHA-256${SCRAM_ITERATIONS}:") or "'" in verifier:
            raise ProvisionError("invalid_verifier")
    async with conn.transaction():
        await require_complete_schema(conn)
        if await conn.fetchval(
            "SELECT count(*) FROM pg_catalog.pg_roles WHERE rolname=ANY($1::name[])",
            [acceptor, worker],
        ):
            raise ProvisionError("role_already_exists")
        for role, verifier in ((acceptor, acceptor_verifier), (worker, worker_verifier)):
            await conn.execute(f"CREATE ROLE {role} {_ROLE_ATTRIBUTES} PASSWORD '{verifier}'")
        for statement in grant_statements(acceptor_map, acceptor) + grant_statements(
            worker_map, worker
        ):
            await conn.execute(statement)
        await verify(conn, acceptor_map, worker_map, acceptor, worker)


async def verify_deployment(conn: Any, acceptor: str, worker: str) -> None:
    """The ``verify`` command: complete history, then exact maps, read-only."""
    acceptor_map, worker_map = load_maps()
    async with conn.transaction(readonly=True):
        await require_complete_schema(conn)
        await verify(conn, acceptor_map, worker_map, acceptor, worker)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--acceptor-role", required=True)
        command.add_argument("--worker-role", required=True)
        if name != "plan":
            command.add_argument("--database-url-file", required=True)
        if name == "apply":
            command.add_argument("--acceptor-password-file", required=True)
            command.add_argument("--worker-password-file", required=True)
    return parser


async def _run(args: argparse.Namespace) -> None:
    import asyncpg

    acceptor_map, worker_map = load_maps()
    if args.command == "plan":
        for role, value in ((args.acceptor_role, acceptor_map), (args.worker_role, worker_map)):
            print(f"CREATE ROLE {_role(role)} {_ROLE_ATTRIBUTES} PASSWORD '<SCRAM verifier>';")
            for statement in grant_statements(value, role):
                print(statement + ";")
        print(f"-- acceptor map {acceptor_map['proposal_sha256']}")
        print(f"-- worker map {worker_map['proposal_sha256']}")
        return
    conn = await asyncpg.connect(read_secret(args.database_url_file), timeout=10)
    try:
        if args.command == "apply":
            await apply(
                conn,
                args.acceptor_role,
                args.worker_role,
                scram_verifier(read_secret(args.acceptor_password_file)),
                scram_verifier(read_secret(args.worker_password_file)),
            )
        else:
            await verify_deployment(conn, args.acceptor_role, args.worker_role)
    finally:
        await conn.close()
    print(
        f"{args.command}_ok acceptor={acceptor_map['proposal_sha256'][:12]} worker={worker_map['proposal_sha256'][:12]}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_run(args))
    except ProvisionError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # Never echo DSNs, passwords or server text.
        print(f"failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
