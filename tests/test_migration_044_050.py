"""Closed release/credential boundaries, with no database or application env."""

from dataclasses import replace
import hashlib
import json
import shutil
import ssl
from typing import Any, cast

import pytest

from scripts import migrate_044_050 as driver


def copied_release(tmp_path):
    for relative in driver.RELEASE_FILES:
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(driver.ROOT / relative, dest)
    return tmp_path


def test_fixed_release_excludes_051_and_preserves_two_036_files():
    release = driver.load_release()
    assert len(release.previous) == 44
    assert len([p for p in release.previous if p.startswith("036_")]) == 2
    assert len(release.payloads) == 7
    assert release.payloads[-1].filename == "050_task_inference_recovery.sql"
    assert all(not p.filename.startswith("051_") for p in release.payloads)
    assert release.sha256 == driver.load_release().sha256


@pytest.mark.parametrize("change", ["sql", "manifest", "missing", "symlink", "helper"])
def test_release_tampering_fails_before_connection(tmp_path, change):
    copied_release(tmp_path)
    sql = tmp_path / driver.SQL_FILES[0]
    if change == "sql":
        sql.write_bytes(sql.read_bytes() + b"\n")
    elif change == "manifest":
        p = tmp_path / driver.MANIFEST
        m = json.loads(p.read_text())
        m["files"].append(m["files"][-1])
        p.write_text(json.dumps(m))
    elif change == "missing":
        sql.unlink()
    elif change == "symlink":
        original = tmp_path / "elsewhere.sql"
        sql.rename(original)
        sql.symlink_to(original)
    else:
        p = tmp_path / "scripts/migrate.py"
        p.write_bytes(p.read_bytes() + b"\n")
        # Helper changes must change the externally pinned source digest.
        assert driver.load_release(tmp_path).sha256 != driver.load_release().sha256
        return
    with pytest.raises(driver.Refused, match="release_invalid"):
        driver.load_release(tmp_path)


def test_051_file_is_never_read_or_admitted(tmp_path):
    copied_release(tmp_path)
    (tmp_path / "migrations/051_artifact_output_reservations.sql").write_text("DO NOT EXECUTE")
    assert driver.load_release(tmp_path).sha256 == driver.load_release().sha256


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_private_input_refuses_exposed_file(tmp_path, mode):
    path = tmp_path / "input"
    path.write_text("synthetic-private-value")
    path.chmod(mode)
    with pytest.raises(driver.Refused, match="private_file_invalid"):
        driver.read_private(path)


def test_private_input_refuses_links_and_never_prints_contents(tmp_path):
    path = tmp_path / "input"
    path.write_text("synthetic-private-value")
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(driver.Refused) as caught:
        driver.read_private(link)
    assert "synthetic-private-value" not in str(caught.value)
    assert driver.read_private(path) == "synthetic-private-value"


@pytest.mark.parametrize("url", ["", "postgresql:///db", "postgresql://user@host/db", "https://x"])
def test_connection_never_uses_application_or_pg_environment(monkeypatch, url):
    monkeypatch.setenv("DATABASE_URL", "postgresql://private-app-value")
    monkeypatch.setenv("POSTGRES_PASSWORD", "private-owner-value")
    with pytest.raises(driver.Refused, match="connection_input_invalid") as caught:
        driver.connection_options(url)
    assert "private" not in str(caught.value)


def test_connection_is_explicit_and_disables_passfile_fallback():
    options = driver.connection_options(
        "postgresql://synthetic:synthetic@localhost:5432/db?sslmode=verify-full"
    )
    assert options["passfile"] == "/dev/null"
    assert options["user"] == "synthetic" and options["database"] == "db"
    assert isinstance(options["ssl"], ssl.SSLContext)
    assert options["ssl"].verify_mode == ssl.CERT_REQUIRED and options["ssl"].check_hostname


@pytest.mark.asyncio
async def test_wrong_source_refuses_before_connect_or_receipt(tmp_path):
    async def forbidden(**kwargs):
        pytest.fail("connection must not be attempted")

    release = driver.load_release()
    target = driver.Target("db", 1, "migrator", "123", "a" * 64, "b" * 64)
    with pytest.raises(driver.Refused, match="source_mismatch"):
        await driver.execute(
            release,
            expected_source="0" * 64,
            target=target,
            database_url="unused",
            receipt=tmp_path / "receipt",
            connect=forbidden,
        )
    assert not (tmp_path / "receipt").exists()


def test_plan_is_offline_and_contains_no_connection_input(capsys):
    assert driver.main(["plan"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["target_watermark"] == "050_task_inference_recovery.sql"
    assert plan["excluded"] == "051_artifact_output_reservations.sql"
    assert len(plan["sha256"]) == hashlib.sha256().digest_size * 2


def test_receipt_refuses_overwrite_and_preserves_original(tmp_path):
    p = tmp_path / "receipt"
    p.write_text("preserved")
    p.chmod(0o600)
    with pytest.raises(driver.Refused, match="receipt_invalid"):
        driver.Receipt(p)
    assert p.read_text() == "preserved"


def test_target_rejects_partial_or_extra_fields():
    with pytest.raises(driver.Refused, match="target_input_invalid"):
        driver.Target.from_json('{"database":"x"}')
    target = driver.Target("db", 1, "migrator", "123", "a" * 64, "b" * 64)
    assert replace(target, database="other") != target
    with pytest.raises(driver.Refused, match="target_input_invalid"):
        driver.Target.from_json('{"database":"x","database":"y"}')


@pytest.mark.asyncio
async def test_validated_payload_helper_never_rereads_the_path(tmp_path):
    from scripts import migrate

    calls = []

    class Transaction:
        async def __aenter__(self):
            calls.append("begin")

        async def __aexit__(self, *args):
            calls.append("commit")

    class Connection:
        def transaction(self):
            return Transaction()

        async def execute(self, sql, *args):
            calls.append((sql, args))

    await migrate.apply_migration(
        cast(Any, Connection()),
        tmp_path / "does_not_exist.sql",
        sql_content="SELECT 1;",
        before_commit=lambda: calls.append("before_commit"),
    )
    assert calls == [
        "begin",
        ("SELECT 1;", ()),
        ("INSERT INTO _migrations (filename) VALUES ($1)", ("does_not_exist.sql",)),
        "before_commit",
        "commit",
    ]
