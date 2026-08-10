from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ORCHESTRATOR_MAIN = REPO_ROOT / "orchestrator" / "main.py"
LEGACY_IMAGE_GEN_DIR = REPO_ROOT / "backend" / "image_gen"


def test_legacy_backend_image_gen_package_is_removed():
    assert not LEGACY_IMAGE_GEN_DIR.exists(), (
        "The unsupported backend.image_gen package was removed because it is not "
        "packaged or copied into runtime images. Do not restore it; build supported "
        "image APIs under orchestrator/routes instead."
    )


def test_orchestrator_does_not_importlib_load_legacy_backend_image_gen():
    text = ORCHESTRATOR_MAIN.read_text(encoding="utf-8", errors="ignore")
    assert "importlib.import_module" not in text, (
        "orchestrator/main.py still uses importlib.import_module. "
        "Issue #21: the legacy backend.image_gen.router is unreachable "
        "(not in pyproject.toml, not in either Dockerfile's COPY) and "
        "crashes the app at startup in Docker. Use a proper route module "
        "in orchestrator/routes/ instead."
    )
    assert "backend.image_gen" not in text, (
        "orchestrator/main.py still references backend.image_gen. See issue #21 for rationale."
    )


def test_orchestrator_no_longer_imports_importlib():
    text = ORCHESTRATOR_MAIN.read_text(encoding="utf-8", errors="ignore")
    assert "import importlib" not in text, (
        "orchestrator/main.py still imports the importlib module. "
        "After removing the legacy image-gen router (issue #21), "
        "importlib is no longer used. Drop the import to keep the surface clean."
    )
