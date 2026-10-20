"""Pure tests for live-session support; no socket, process or Docker."""

from __future__ import annotations

import json

import pytest

from scripts.web_fetch_pilot_live import (
    MAX_INVENTORY,
    InventoryRefused,
    build_inventory,
    local_ipv4_addresses,
    run_evidence,
    url_evidence,
)

FIB_TRIE = """Main:
  +-- 0.0.0.0/0 3 0 5
     |-- 0.0.0.0
        /0 universe UNICAST
     +-- 127.0.0.0/8 2 0 2
        |-- 127.0.0.1
           /32 host LOCAL
     +-- 192.168.20.0/24 2 0 1
        |-- 192.168.20.42
           /32 host LOCAL
        |-- 192.168.20.255
           /32 link BROADCAST
     |-- 172.17.0.1
        /32 host LOCAL
Local:
  +-- 0.0.0.0/0 3 0 5
     |-- 192.168.20.42
        /32 host LOCAL
"""
SECRET_URL = "https://openai.com/index/introducing-dots/?token=SECRET"


def test_local_addresses_from_fib_trie_exclude_loopback_and_broadcast() -> None:
    assert local_ipv4_addresses(FIB_TRIE) == ("172.17.0.1", "192.168.20.42")
    assert local_ipv4_addresses("") == ()


def test_inventory_requires_owner_addresses_and_validates_every_entry() -> None:
    inventory = build_inventory(("192.168.20.42",), ["203.0.113.7", "198.51.100.0/24"], now=1.0)
    assert inventory.entries == ("192.168.20.42", "198.51.100.0/24", "203.0.113.7")
    assert len(inventory.sha256) == 64 and inventory.gathered_at == 1.0
    same = build_inventory(["192.168.20.42"], ("198.51.100.0/24", "203.0.113.7"), now=2.0)
    assert same.sha256 == inventory.sha256  # Order-independent, content-addressed.
    for owners in ([], (), None, "203.0.113.7"):
        with pytest.raises(InventoryRefused):
            build_inventory(("192.168.20.42",), owners)  # type: ignore[arg-type]
    with pytest.raises(InventoryRefused):
        build_inventory((), ["not-an-address"])
    with pytest.raises(InventoryRefused):
        build_inventory((), ["2001:db8::1"])  # Owner list must include IPv4.
    with pytest.raises(InventoryRefused):
        build_inventory([f"10.0.{i // 250}.{i % 250}" for i in range(MAX_INVENTORY)], ["1.1.1.1"])


def test_url_evidence_never_keeps_path_or_query() -> None:
    record = url_evidence(SECRET_URL)
    assert record["host"] == "openai.com" and len(record["url_sha256"]) == 64
    assert "SECRET" not in json.dumps(record) and "introducing" not in json.dumps(record)


def test_run_evidence_reduces_content_to_length_and_hash() -> None:
    inventory = build_inventory((), ["203.0.113.7"], now=0.0)
    record = run_evidence(
        mode="ordinary",
        url=SECRET_URL,
        result_status="success",
        content=b"article body that must not be stored",
        exit_codes={"browser": 0, "gateway": 0},
        counters={"opens": 3, "read_bytes": 717},
        hashes={"image": "a" * 64},
        inventory=inventory,
    )
    text = json.dumps(record)
    assert "article body" not in text and "SECRET" not in text
    assert record["content_bytes"] == 36 and len(str(record["content_sha256"])) == 64
    assert record["inventory_entries"] == 1
    empty = run_evidence(
        mode="ordinary",
        url=SECRET_URL,
        result_status="blocked",
        content=b"",
        exit_codes={},
        counters={},
        hashes={},
        inventory=None,
    )
    assert empty["content_sha256"] is None and empty["inventory_sha256"] is None
    for bad in ({"counters": {"x": "1"}}, {"hashes": {"image": "short"}}):
        kwargs = {
            "mode": "ordinary",
            "url": SECRET_URL,
            "result_status": None,
            "content": b"",
            "exit_codes": {},
            "counters": {},
            "hashes": {},
            "inventory": None,
            **bad,
        }
        with pytest.raises(ValueError):
            run_evidence(**kwargs)  # type: ignore[arg-type]
