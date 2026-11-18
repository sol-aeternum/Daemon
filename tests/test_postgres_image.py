"""Pinned PostgreSQL identity regression coverage without Docker or downloads."""

import json
from types import SimpleNamespace

import pytest

from tests import compute_metadata_support as support
from tests import postgres_image as image
from tests import test_artifact_restore_boundary_postgres as restore


def metadata(image_id=image.CONFIG_DIGEST):
    return [
        {
            "Id": image_id,
            "RepoDigests": [image.IMAGE_REFERENCE],
            "Os": "linux",
            "Architecture": "amd64",
        }
    ]


@pytest.mark.parametrize("image_id", [image.INDEX_DIGEST, image.CONFIG_DIGEST])
def test_fixture_launches_exact_qualified_id(tmp_path, monkeypatch, image_id):
    calls = []

    def command(args):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            assert args[3] == image.IMAGE_REFERENCE
            return SimpleNamespace(returncode=0, stdout=json.dumps(metadata(image_id)))
        if args[:2] == ["docker", "run"]:
            assert args[-1] == image_id
            assert "--pull=never" in args
            assert "--platform=linux/amd64" in args
            return SimpleNamespace(returncode=0, stdout="container-id", stderr="")
        return SimpleNamespace(returncode=0, stdout="init process complete")

    monkeypatch.setattr(support, "command", command)
    monkeypatch.setattr(support.Cluster, "sql", lambda *args: "synthetic version")
    cluster = support.Cluster(tmp_path, tmp_path)
    cluster.start()
    assert cluster.image == image_id
    assert cluster.receipts[0] == {
        "event": "qualified_image",
        "reference": image.IMAGE_REFERENCE,
        "platform": image.PLATFORM,
        "id": image_id,
    }
    assert calls[0][:3] == ["docker", "image", "inspect"]


BAD_METADATA = [None, {}, [], [None], [{}, {}], [{}]] + [
    [{**metadata()[0], key: value}]
    for key, value in [
        ("Id", None),
        ("Id", []),
        ("Id", "sha256:" + "f" * 64),
        ("Id", image.AMD64_MANIFEST_DIGEST),
        ("Os", "windows"),
        ("Architecture", "arm64"),
        ("RepoDigests", None),
        ("RepoDigests", []),
        ("RepoDigests", [image.IMAGE_REFERENCE, "foreign@" + image.INDEX_DIGEST]),
        ("RepoDigests", ["pgvector/pgvector:pg16"]),
        ("RepoDigests", ["foreign@" + image.INDEX_DIGEST]),
        ("RepoDigests", [{}]),
    ]
]


@pytest.mark.parametrize("data", BAD_METADATA)
def test_unqualified_image_never_launches(tmp_path, monkeypatch, data):
    calls = []

    def command(args):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))

    monkeypatch.setattr(support, "command", command)
    cluster = support.Cluster(tmp_path, tmp_path)
    with pytest.raises(ValueError):
        cluster.start()
    assert calls == [["docker", "image", "inspect", image.IMAGE_REFERENCE]]
    assert cluster.container is None


@pytest.mark.parametrize("image_id", [image.INDEX_DIGEST, image.CONFIG_DIGEST])
def test_restore_ownership_requires_exact_launch_image(monkeypatch, image_id):
    cluster = SimpleNamespace(container="container-id", nonce="nonce", image=image_id)
    info = {
        "Id": cluster.container,
        "Image": image_id,
        "Config": {
            "Labels": {
                "opencode.compute-metadata-fixture": cluster.nonce,
                "opencode.task": support.ROOT.name,
            }
        },
        "HostConfig": {"NetworkMode": "none"},
        "Mounts": [],
        "State": {"Running": True},
    }
    monkeypatch.setattr(restore, "command", lambda args: SimpleNamespace(stdout=json.dumps([info])))
    restore.owned(cluster)
    info["Image"] = image.CONFIG_DIGEST if image_id == image.INDEX_DIGEST else image.INDEX_DIGEST
    with pytest.raises(AssertionError):
        restore.owned(cluster)
    info["Image"] = image_id
    info["Config"]["Labels"]["opencode.compute-metadata-fixture"] = "foreign"
    with pytest.raises(AssertionError):
        restore.owned(cluster)


def test_inspection_failure_never_launches(tmp_path, monkeypatch):
    calls = []

    def command(args):
        calls.append(args)
        return SimpleNamespace(returncode=1, stderr="inspection failed")

    monkeypatch.setattr(support, "command", command)
    with pytest.raises(AssertionError, match="inspection failed"):
        support.Cluster(tmp_path, tmp_path).start()
    assert len(calls) == 1
