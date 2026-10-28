"""Synthetic contract consistency, NOT runtime auth/storage/E2E qualification.

The oracle below lives only in tests. Future production adapters must consume
these vectors against real ownership/publication boundaries, not import it.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import csv
from io import StringIO
import json
from pathlib import Path
import struct
import unittest
from uuid import UUID
import zlib


FIXTURE = Path(__file__).parent / "fixtures" / "artifact_continuity" / "contract.json"


def fixture_bindings_valid(fixture: dict) -> bool:
    """Check explicit synthetic associations, not inferred filename ownership."""
    owner, task, job = fixture["account_a"], fixture["task"], fixture["job"]
    if task["owner_id"] != owner or job["owner_id"] != owner:
        return False
    if job["task_id"] != task["task_id"]:
        return False
    output = fixture["output"]
    if output["job_id"] != job["job_id"]:
        return False
    for record in [*fixture["inputs"], output]:
        if record["owner_id"] != owner or record["task_id"] != task["task_id"]:
            return False
        if record["thread_id"] != task["thread_id"]:
            return False
        expected_message = (
            task["result_message_id"] if record is output else task["user_message_id"]
        )
        if record["message_id"] != expected_message:
            return False
    return True


def resolve_fixture_reference(fixture: dict, owner: str, reference: dict) -> str:
    """Test-vector oracle; never an authentication implementation."""
    if set(reference) != {"artifact_id", "artifact_version_id"}:
        return "invalid_reference"
    if not fixture_bindings_valid(fixture) or owner != fixture["output"]["owner_id"]:
        return "not_found"
    if reference["artifact_id"] != fixture["output"]["resource_id"]:
        return "not_found"
    for version in fixture["output"]["versions"]:
        if reference["artifact_version_id"] == version["version_id"]:
            return version["text"]
    return "not_found"


class ArtifactContractVectorsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_server_identity_and_adapter_mapping(self) -> None:
        fixture = self.fixture
        self.assertEqual(fixture["fixture_version"], 1)
        identities = [fixture[key] for key in ("account_a", "account_b")]
        identities += [
            fixture["task"][key]
            for key in ("task_id", "thread_id", "user_message_id", "result_message_id")
        ]
        identities += [fixture["job"]["job_id"]]
        identities += [item["resource_id"] for item in fixture["inputs"]]
        identities += [item["version_id"] for item in fixture["inputs"]]
        identities += [fixture["output"]["resource_id"]]
        identities += [version["version_id"] for version in fixture["output"]["versions"]]
        self.assertEqual(len(identities), len(set(identities)))
        for identity in identities:
            self.assertEqual(str(UUID(identity)), identity)
        presentation = fixture["presentation"]
        self.assertEqual(presentation["schema_version"], 1)
        self.assertEqual(presentation["component_library_version"], 1)
        self.assertEqual(presentation["component"], "file-results")
        reference = presentation["files"][0]
        self.assertEqual(reference["artifact_id"], fixture["output"]["resource_id"])
        self.assertEqual(
            reference["artifact_version_id"], fixture["output"]["versions"][0]["version_id"]
        )

    def test_explicit_owner_task_thread_message_and_job_bindings(self) -> None:
        self.assertTrue(fixture_bindings_valid(self.fixture))
        mutations = [
            ("task", "owner_id", self.fixture["account_b"]),
            ("job", "owner_id", self.fixture["account_b"]),
            ("job", "task_id", self.fixture["task"]["thread_id"]),
            ("output", "owner_id", self.fixture["account_b"]),
            ("output", "job_id", self.fixture["task"]["task_id"]),
            ("output", "message_id", self.fixture["task"]["user_message_id"]),
        ]
        for record, key, value in mutations:
            with self.subTest(record=record, key=key):
                mutated = deepcopy(self.fixture)
                mutated[record][key] = value
                self.assertFalse(fixture_bindings_valid(mutated))
                self.assertEqual(
                    resolve_fixture_reference(
                        mutated, mutated["account_a"], mutated["presentation"]["files"][0]
                    ),
                    "not_found",
                )
        for key in ("owner_id", "task_id", "thread_id", "message_id"):
            with self.subTest(input_field=key):
                mutated = deepcopy(self.fixture)
                mutated["inputs"][0][key] = mutated["account_b"]
                self.assertFalse(fixture_bindings_valid(mutated))

    def test_image_fixture_is_complete_png_not_just_signature(self) -> None:
        image = self.fixture["inputs"][0]
        self.assertEqual(image["representation"], "submitted_image")
        self.assertEqual(image["availability"], "available")
        raw = base64.b64decode(image["content_base64"], validate=True)
        self.assertLessEqual(len(raw), 8 * 1024 * 1024)
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        offset = 8
        chunks = []
        pixels = b""
        while offset < len(raw):
            size = struct.unpack_from(">I", raw, offset)[0]
            kind = raw[offset + 4 : offset + 8]
            payload = raw[offset + 8 : offset + 8 + size]
            checksum = struct.unpack_from(">I", raw, offset + 8 + size)[0]
            self.assertEqual(zlib.crc32(kind + payload), checksum)
            chunks.append(kind)
            if kind == b"IHDR":
                self.assertEqual(struct.unpack(">IIBBBBB", payload), (1, 1, 8, 4, 0, 0, 0))
            if kind == b"IDAT":
                pixels += payload
            offset += 12 + size
        self.assertEqual(offset, len(raw))
        self.assertEqual(chunks, [b"IHDR", b"IDAT", b"IEND"])
        self.assertEqual(len(zlib.decompress(pixels)), 3)  # filter + grayscale/alpha

    def test_received_text_and_absent_binary_remain_honest(self) -> None:
        inputs = self.fixture["inputs"]
        self.assertEqual([item["ordinal"] for item in inputs], [0, 1, 2])
        text, binary = inputs[1:]
        self.assertEqual(text["representation"], "submitted_text")
        self.assertFalse(text["original_received"])
        self.assertEqual(text["submitted_text"], "Fictional excerpt\n... (truncated)")
        self.assertEqual(binary["representation"], "metadata_only")
        self.assertFalse(binary["original_received"])
        self.assertEqual(binary["availability"], "original_not_received")
        self.assertEqual(binary["reported_original_size"], 123)
        for absent_field in ("content_base64", "submitted_text", "byte_size", "checksum", "url"):
            self.assertNotIn(absent_field, binary)

    def test_pinned_output_does_not_follow_a_new_version(self) -> None:
        fixture = self.fixture
        owner = fixture["account_a"]
        reference = fixture["presentation"]["files"][0]
        original = resolve_fixture_reference(fixture, owner, reference)
        fixture["output"]["versions"].reverse()
        self.assertEqual(resolve_fixture_reference(fixture, owner, reference), original)
        self.assertEqual(list(csv.reader(StringIO(original))), [["label", "value"], ["alpha", "1"]])
        newer = {**reference, "artifact_version_id": fixture["output"]["versions"][0]["version_id"]}
        self.assertEqual(
            list(csv.reader(StringIO(resolve_fixture_reference(fixture, owner, newer)))),
            [["label", "value"], ["alpha", "2"]],
        )

    def test_reference_negative_vectors(self) -> None:
        fixture = self.fixture
        owner = fixture["account_a"]
        reference = fixture["presentation"]["files"][0]
        cases = [
            (fixture["account_b"], reference, "not_found"),
            (owner, {**reference, "artifact_id": fixture["inputs"][0]["resource_id"]}, "not_found"),
            (
                owner,
                {**reference, "artifact_version_id": fixture["inputs"][0]["version_id"]},
                "not_found",
            ),
            (owner, {"artifact_id": reference["artifact_id"]}, "invalid_reference"),
            (owner, {**reference, "url": "https://example.invalid/file"}, "invalid_reference"),
            (owner, {**reference, "action": "regenerate"}, "invalid_reference"),
        ]
        before = deepcopy(fixture)
        for caller, candidate, expected in cases:
            with self.subTest(candidate=candidate, caller=caller):
                self.assertEqual(resolve_fixture_reference(fixture, caller, candidate), expected)
        self.assertEqual(fixture, before)


if __name__ == "__main__":
    unittest.main()
