"""Offline checks for the task-role provisioning script (no database)."""

import base64
import hashlib
import hmac
import json
import os

import pytest

from scripts import provision_task_roles as provision


def test_both_maps_match_their_pinned_digests_and_shapes():
    acceptor, worker = provision.load_maps()
    assert acceptor["schema"] == "exact-acceptor-privileges/v1"
    assert worker["schema"] == "exact-worker-privileges/v1"
    for value in (acceptor, worker):
        assert provision.proposal_sha256(value) == value["proposal_sha256"]
        tables = [entry["table"] for entry in value["ordinary_tables"]]
        assert tables == sorted(set(tables))


def test_acceptor_has_no_function_or_protected_table_authority_beyond_four_inserts():
    acceptor, _ = provision.load_maps()
    assert provision.functions(acceptor) == []
    assert acceptor["sequences_usage_only"] == []
    protected = {
        "public.compute_scope_metadata",
        "public.compute_reservation_metadata",
        "public.attempt_lifecycle_control",
        "public.attempt_lifecycle_commands",
    }
    entries = {entry["table"]: entry for entry in acceptor["ordinary_tables"]}
    assert not protected & set(entries)
    recovery = entries["public.task_inference_recovery"]
    assert recovery["table_privileges"] == [] and recovery["update_columns"] == []
    assert recovery["insert_columns"] == [
        "account_generation",
        "account_id",
        "protocol_version",
        "task_id",
    ]
    # Startup's ordinary check needs table-level UPDATE here; never DELETE anywhere.
    assert all("DELETE" not in entry["table_privileges"] for entry in entries.values())


def test_tampered_map_refuses(tmp_path, monkeypatch):
    for name in ("acceptor", "worker"):
        value = json.loads((provision.MAPS / f"{name}.json").read_text())
        if name == "worker":
            value["ordinary_tables"][0]["table_privileges"].append("DELETE")
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    monkeypatch.setattr(provision, "MAPS", tmp_path)
    with pytest.raises(provision.ProvisionError, match="worker_map_digest_mismatch"):
        provision.load_maps()


def test_grant_statements_quote_nothing_from_outside_the_map():
    acceptor, worker = provision.load_maps()
    statements = provision.grant_statements(acceptor, "daemon_task_acceptor")
    assert statements[0] == "GRANT USAGE ON SCHEMA public TO daemon_task_acceptor"
    assert (
        "GRANT INSERT(account_generation,account_id,protocol_version,task_id) "
        "ON TABLE public.task_inference_recovery TO daemon_task_acceptor"
    ) in statements
    assert not any("task_inference_recovery TO" in s and "GRANT SELECT" in s for s in statements)
    assert len(provision.grant_statements(worker, "w")) > len(statements)
    for bad in ("Daemon", "a;drop", "x" * 64, "", "1abc"):
        with pytest.raises(provision.ProvisionError, match="invalid_role_name"):
            provision.grant_statements(acceptor, bad)


def test_scram_keys_match_the_rfc_7677_exchange():
    salt = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
    client_key, stored_key, server_key = provision.scram_keys("pencil", salt, 4096)
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    auth = (
        "n=user,r=rOprNGfwEbeRWgbNEkqO,"
        f"r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
        f"c=biws,r={nonce}"
    ).encode()
    signature = hmac.new(stored_key, auth, hashlib.sha256).digest()
    proof = bytes(a ^ b for a, b in zip(client_key, signature))
    assert base64.b64encode(proof).decode() == "dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
    server = hmac.new(server_key, auth, hashlib.sha256).digest()
    assert base64.b64encode(server).decode() == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def test_scram_verifier_format_and_password_rules():
    verifier = provision.scram_verifier("Correct-Horse-Battery-1", salt=b"\0" * 16)
    head, keys = verifier.split("$", 1)[1].split("$")
    assert verifier.startswith("SCRAM-SHA-256$4096:")
    assert head == "4096:" + base64.b64encode(b"\0" * 16).decode()
    stored, server = (base64.b64decode(part) for part in keys.split(":"))
    assert len(stored) == len(server) == 32
    for bad in ("short", "has space in it here", "nön-ascii-password-1", "x" * 1025):
        with pytest.raises(provision.ProvisionError, match="invalid_password"):
            provision.scram_verifier(bad)


def _secret(path, mode=0o600, text="postgresql://owner@db/daemon"):
    path.write_text(text)
    os.chmod(path, mode)
    return str(path)


def test_read_secret_accepts_only_private_regular_files(tmp_path):
    assert provision.read_secret(_secret(tmp_path / "ok")) == "postgresql://owner@db/daemon"
    with pytest.raises(provision.ProvisionError, match="unsafe_secret_file"):
        provision.read_secret(_secret(tmp_path / "open", 0o644))
    with pytest.raises(provision.ProvisionError, match="empty_secret_file"):
        provision.read_secret(_secret(tmp_path / "empty", text="\n"))
    with pytest.raises(provision.ProvisionError, match="unsafe_secret_file"):
        provision.read_secret(_secret(tmp_path / "big", text="x" * 5000))
    os.symlink(tmp_path / "ok", tmp_path / "link")
    with pytest.raises(OSError):
        provision.read_secret(str(tmp_path / "link"))


def test_plan_prints_sql_and_digests_but_no_secret(capsys):
    assert provision.main(["plan", "--acceptor-role", "a_role", "--worker-role", "w_role"]) == 0
    out = capsys.readouterr().out
    assert "CREATE ROLE a_role LOGIN NOSUPERUSER" in out
    assert "PASSWORD '<SCRAM verifier>'" in out and "SCRAM-SHA-256$" not in out
    acceptor, worker = provision.load_maps()
    assert acceptor["proposal_sha256"] in out and worker["proposal_sha256"] in out


def test_cli_refusals_print_codes_only(capsys):
    assert provision.main(["plan", "--acceptor-role", "same", "--worker-role", "Bad"]) == 2
    assert capsys.readouterr().err.strip() == "refused: invalid_role_name"


class _History:
    """Offline stand-in for the owner connection's bookkeeping reads."""

    def __init__(self, rows, *, locked=True, in_tx=True, table=True):
        self.rows, self.locked, self.in_tx, self.table = rows, locked, in_tx, table

    def is_in_transaction(self):
        return self.in_tx

    async def fetchval(self, sql, *args):
        return self.locked if "advisory" in sql else ("_migrations" if self.table else None)

    async def fetch(self, sql, *args):
        assert sql == "SELECT filename FROM _migrations"  # the runner's own read
        return [{"filename": name} for name in self.rows]


@pytest.mark.asyncio
async def test_history_check_is_set_equality_independent_of_collation():
    expected = provision.expected_migrations()
    # en_US-style orders differ from codepoint order; any order of the full set passes.
    await provision.require_complete_schema(_History(list(reversed(expected))))
    for rows, code in (
        (expected[:-1], "migration_history_incomplete"),
        ([n for n in expected if not n.startswith("036_memory")], "migration_history_incomplete"),
        ([*expected, "999_unknown.sql"], "migration_history_incomplete"),
    ):
        with pytest.raises(provision.ProvisionError, match=code):
            await provision.require_complete_schema(_History(rows))
    for history, code in (
        (_History(expected, locked=False), "migration_in_progress"),
        (_History(expected, in_tx=False), "transaction_required"),
        (_History(expected, table=False), "migration_history_incomplete"),
    ):
        with pytest.raises(provision.ProvisionError, match=code):
            await provision.require_complete_schema(history)


def test_missing_migration_files_refuse_instead_of_matching_empty_history(monkeypatch, tmp_path):
    monkeypatch.setattr(provision.migrate, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(provision.ProvisionError, match="migrations_unavailable"):
        provision.expected_migrations()
