"""Real POSIX encrypted copy/read/repair faults, no caller authority oracle."""

import ast
import errno
import hashlib
import multiprocessing
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet
import pytest

from orchestrator import resource_storage as storage
from orchestrator.artifact_envelope import ArtifactBinding, ArtifactEnvelopeCodec
from orchestrator.artifact_namespaces import _topology


@pytest.fixture
def object_store(tmp_path):
    root = tmp_path / "objects"
    root.mkdir(mode=0o700)
    for name in ("live", "private"):
        (root / name).mkdir(mode=0o700)
    content = b"column\nprivate value\n"
    namespace = uuid4().hex
    binding = storage.StoredBinding(
        ArtifactBinding(str(uuid4()), str(uuid4()), str(uuid4()), 0, "generated_file"),
        namespace,
        uuid4().hex,
        uuid4().hex,
        len(content),
        hashlib.sha256(content).hexdigest(),
    )
    codec = ArtifactEnvelopeCodec(Fernet(Fernet.generate_key()))
    _topology(root, namespace, create=True)
    return root, codec, binding, content


def test_private_read_before_no_clobber_publication_and_idempotent_repair(object_store):
    root, codec, binding, content = object_store
    storage.prepare_verified(root, codec, binding, content)
    private = root / "private" / binding.namespace / binding.intent
    public = root / "live" / binding.namespace / binding.public_name
    assert content not in private.read_bytes()
    assert storage.read_verified(root, codec, binding) == content
    assert not public.exists()
    storage.repair_verified(root, codec, binding)
    assert public.samefile(private)
    assert private.stat().st_nlink == 2
    storage.repair_verified(root, codec, binding)
    assert storage.read_verified(root, codec, binding) == content


def test_duplicate_preparation_resyncs_instead_of_trusting_observation(object_store, monkeypatch):
    root, codec, binding, content = object_store
    storage.prepare_verified(root, codec, binding, content)
    calls = []
    original = os.fsync

    def sync(fd):
        calls.append(fd)
        return original(fd)

    monkeypatch.setattr(os, "fsync", sync)
    storage.prepare_verified(root, codec, binding, content)
    assert len(calls) == 4


@pytest.mark.parametrize("step", range(1, 8))
def test_repair_sync_fault_preserves_and_fresh_repair_resolves(object_store, monkeypatch, step):
    root, codec, binding, content = object_store
    storage.prepare_verified(root, codec, binding, content)
    original = os.fsync
    count = 0

    def sync(fd):
        nonlocal count
        count += 1
        if count == step:
            raise OSError("synthetic")
        original(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", sync)
        with pytest.raises(storage.StorageError, match="uncertain"):
            storage.repair_verified(root, codec, binding)
    assert storage.read_verified(root, codec, binding) == content
    storage.repair_verified(root, codec, binding)


@pytest.mark.parametrize("after", [False, True])
def test_uncertain_rename_preserves_original_bytes_and_identity(object_store, monkeypatch, after):
    root, codec, binding, content = object_store
    storage.prepare_verified(root, codec, binding, content)
    original = storage._rename_no_replace

    def rename(*args):
        if after:
            original(*args)
        raise OSError("synthetic")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_rename_no_replace", rename)
        with pytest.raises(storage.StorageError, match="uncertain"):
            storage.repair_verified(root, codec, binding)
    storage.repair_verified(root, codec, binding)
    assert storage.read_verified(root, codec, binding) == content


def test_conflicting_destination_never_overwritten(object_store):
    root, codec, binding, content = object_store
    storage.prepare_verified(root, codec, binding, content)
    target = root / "live" / binding.namespace / binding.public_name
    target.write_bytes(b"preserved-conflict")
    target.chmod(0o600)
    with pytest.raises(storage.StorageError):
        storage.repair_verified(root, codec, binding)
    assert target.read_bytes() == b"preserved-conflict"
    assert (root / "live" / binding.namespace / "staging" / binding.intent).exists()


def test_private_only_committed_repair_is_exclusive_and_reads_passively(object_store):
    root, codec, binding, content = object_store
    storage.prepare_verified(root, codec, binding, content)
    stage = root / "live" / binding.namespace / "staging" / binding.intent
    stage.unlink()  # Synthetic externally missing namespace edge, not service cleanup.
    assert storage.read_verified(root, codec, binding) == content
    assert not stage.exists()
    storage.repair_verified(root, codec, binding)
    assert (root / "live" / binding.namespace / binding.public_name).exists()


@pytest.mark.parametrize(
    "damage", ["hash", "key", "corrupt", "mode", "extra_link", "symlink", "oversize"]
)
def test_private_authentication_and_topology_failure_denied(object_store, damage):
    root, codec, binding, content = object_store
    storage.prepare_verified(root, codec, binding, content)
    private = root / "private" / binding.namespace / binding.intent
    if damage == "hash":
        binding = replace(binding, sha256="0" * 64)
    elif damage == "key":
        codec = ArtifactEnvelopeCodec(Fernet(Fernet.generate_key()))
    elif damage == "corrupt":
        private.write_bytes(b"broken")
    elif damage == "mode":
        private.chmod(0o644)
    elif damage == "extra_link":
        os.link(private, private.with_name("unexpected"))
    elif damage == "symlink":
        private.unlink()
        private.symlink_to(root / "live" / binding.namespace / "staging" / binding.intent)
    else:
        with private.open("r+b") as stream:
            stream.truncate(32 * 1024 * 1024)
    with pytest.raises(storage.StorageError):
        storage.read_verified(root, codec, binding)


def test_unsupported_rename_has_no_overwrite_fallback(object_store, monkeypatch):
    root, codec, binding, content = object_store
    storage.prepare_verified(root, codec, binding, content)

    def missing(*args, **kwargs):
        raise OSError(errno.ENOSYS, "synthetic")

    monkeypatch.setattr(storage.ctypes, "CDLL", missing)
    with pytest.raises(storage.StorageError, match="unsupported"):
        storage.repair_verified(root, codec, binding)
    assert (root / "live" / binding.namespace / "staging" / binding.intent).exists()


def _die_after_rename(root, key, binding):
    original = storage._rename_no_replace

    def rename(*args):
        original(*args)
        os._exit(73)

    storage._rename_no_replace = rename
    storage.repair_verified(root, ArtifactEnvelopeCodec(Fernet(key)), binding)


def test_owned_process_death_during_repair_releases_lock_and_keeps_private(object_store):
    root, _, binding, content = object_store
    key = Fernet.generate_key()
    codec = ArtifactEnvelopeCodec(Fernet(key))
    storage.prepare_verified(root, codec, binding, content)
    job = multiprocessing.get_context("spawn").Process(
        target=_die_after_rename, args=(root, key, binding)
    )
    job.start()
    try:
        job.join(10)
        assert job.exitcode == 73
    finally:
        if job.is_alive():
            job.terminate()
            job.join(5)
    storage.repair_verified(root, codec, binding)
    assert storage.read_verified(root, codec, binding) == content


def _registry_references_allowed(path: str, source: str) -> bool:
    """One exact static H import, not another admitted runtime module."""
    if "resource_storage" in source:
        return False
    if (
        path
        in {
            "orchestrator/services/restore_history/effects.py",
            "orchestrator/services/restore_history/native_publication.py",
        }
        and "resource_store" in source
    ):
        if source.count("resource_store") != 1:
            return False
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return False
        imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "orchestrator.resource_store"
        ]
        if len(imports) != 1:
            return False
        node = imports[0]
        if (
            node not in tree.body
            or node.level != 0
            or len(node.names) != 2
            or {name.name for name in node.names} != {"ReservedOutputPublisher", "Publication"}
            or any(name.asname is not None for name in node.names)
        ):
            return False
        segment = ast.get_source_segment(source, node)
        if segment is None or source.count(segment) != 1:
            return False
        source = source.replace(segment, "", 1)
    return "resource_store" not in source and "resource_storage" not in source


def test_registry_modules_not_imported_by_default_runtime():
    root = Path(__file__).resolve().parents[1]
    allowed = {
        "orchestrator/resource_storage.py",
        "orchestrator/resource_store.py",
        "orchestrator/resource_input_codec.py",
        "orchestrator/resource_inputs.py",
        "orchestrator/resource_catalog.py",
        "orchestrator/resource_presentations.py",
        "orchestrator/resource_http.py",
        "orchestrator/resource_csv.py",
    }
    for folder in ("orchestrator", "providers"):
        for path in (root / folder).rglob("*.py"):
            if path.relative_to(root).as_posix() not in allowed:
                assert _registry_references_allowed(
                    path.relative_to(root).as_posix(), path.read_text()
                ), path


_H_OUTPUT_OWNER = "orchestrator/services/restore_history/effects.py"
_OUTPUT_TYPES = "from orchestrator.resource_store import ReservedOutputPublisher, Publication\n"


@pytest.mark.parametrize(
    "source",
    [
        _OUTPUT_TYPES,
        "from orchestrator.resource_store import (\n Publication,\n ReservedOutputPublisher,\n)\n",
    ],
)
def test_output_type_import_exception_is_exact_and_does_not_admit_module(source):
    from tests.inference_composition_support import ADMITTED, composition_violations

    assert _H_OUTPUT_OWNER not in ADMITTED
    assert _registry_references_allowed(_H_OUTPUT_OWNER, source)
    assert not composition_violations({_H_OUTPUT_OWNER: source})


@pytest.mark.parametrize(
    "source",
    [
        "from orchestrator.resource_store import ReservedOutputPublisher\n",
        "from orchestrator.resource_store import Publication\n",
        _OUTPUT_TYPES.replace("Publication", "ResourceStore"),
        _OUTPUT_TYPES.rstrip() + ", ResourceStore\n",
        _OUTPUT_TYPES.rstrip() + ", NamespaceController\n",
        _OUTPUT_TYPES.replace("Publication", "Publication as P"),
        _OUTPUT_TYPES.replace("ReservedOutputPublisher", "ReservedOutputPublisher as P"),
        _OUTPUT_TYPES.replace("Publication", "ReservedOutputPublisher"),
        "from orchestrator.resource_store import *\n",
        "import orchestrator.resource_store as resources\n",
        "from orchestrator import resource_store\n",
        "import importlib\nimportlib.import_module('orchestrator.resource_store')\n",
        "__import__('orchestrator.resource_store')\n",
        _OUTPUT_TYPES + "other = 'orchestrator.resource_store'\n",
        _OUTPUT_TYPES + "import orchestrator.resource_storage\n",
        _OUTPUT_TYPES + _OUTPUT_TYPES,
        _OUTPUT_TYPES.replace("from orchestrator", "from .orchestrator"),
        "def install():\n " + _OUTPUT_TYPES,
        "if TYPE_CHECKING:\n " + _OUTPUT_TYPES,
    ],
)
def test_output_import_exception_rejects_extra_alias_dynamic_and_nested_access(source):
    assert not _registry_references_allowed(_H_OUTPUT_OWNER, source)


@pytest.mark.parametrize(
    "path",
    [
        "orchestrator/main.py",
        "orchestrator/db.py",
        "orchestrator/worker/worker.py",
        "orchestrator/tasks/runner.py",
        "orchestrator/services/restore_history/other.py",
        "providers/other.py",
    ],
)
def test_output_type_exception_does_not_admit_other_hosts(path):
    assert not _registry_references_allowed(path, _OUTPUT_TYPES)


@pytest.mark.parametrize(
    "effect",
    [
        "ReservedOutputPublisher(pool,root,path,identity,key,inventory_limit=1,snapshot_byte_limit=1)",
        "P = ReservedOutputPublisher\nP(pool,root,path,identity,key,inventory_limit=1,snapshot_byte_limit=1)",
        "compose(inference_recovery=collaborator)",
        "store._inference = collaborator",
        "state.task_composition = bundle",
        "state.history_runtime = owner",
        "qualify_task_composition(enabled=True)",
    ],
)
def test_output_type_import_never_admits_construction_injection_or_activation(effect):
    from tests.inference_composition_support import composition_violations

    source = _OUTPUT_TYPES + effect
    assert _registry_references_allowed(_H_OUTPUT_OWNER, source)
    assert composition_violations({_H_OUTPUT_OWNER: source})


@pytest.mark.parametrize("comment", ["resource_storage", "resource_store"])
def test_output_import_exception_does_not_hide_other_reference_tokens_inside_import(comment):
    source = (
        "from orchestrator.resource_store import (\n ReservedOutputPublisher, # "
        + comment
        + "\n Publication,\n)\n"
    )
    assert not _registry_references_allowed(_H_OUTPUT_OWNER, source)
