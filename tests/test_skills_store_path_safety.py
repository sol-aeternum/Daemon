from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import skills_store


@pytest.mark.parametrize(
    "skill_id",
    [
        "..",
        "../etc/passwd",
        "/etc/passwd",
        "abc..def",
        "abc/def",
        "abc\\def",
        "Code Review V2",
        "C++ Review",
        "Research & Synthesis",
        "# Debug Workflow!!!",
        "",
    ],
)
def test_normalize_skill_id_rejects_path_traversal_and_non_normalized_inputs(
    skill_id: str,
) -> None:
    with pytest.raises(ValueError):
        skills_store.normalize_skill_id(skill_id)


@pytest.mark.parametrize(
    "skill_id",
    [
        "code-review-v2",
        "_code-review-v2_",
        "code_review_v2",
    ],
)
def test_normalize_skill_id_accepts_already_safe_ids(skill_id: str) -> None:
    assert skills_store.normalize_skill_id(skill_id) == skill_id


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("code-review-v2", "code-review-v2"),
        ("Code Review V2", "code-review-v2"),
        ("_Code Review V2_", "code-review-v2"),
        ("C++ Review", "c-review"),
        ("Research & Synthesis", "research-synthesis"),
        ("# Debug Workflow!!!", "debug-workflow"),
    ],
)
def test_slugify_skill_name_produces_safe_slug(
    raw_name: str,
    expected: str,
) -> None:
    assert skills_store.slugify_skill_name(raw_name) == expected


@pytest.mark.parametrize(
    "raw_name",
    [
        "../etc/passwd",
        "/etc/passwd",
        "..",
        "...",
        "abc/def",
        "abc\\def",
        "",
        "   ",
    ],
)
def test_slugify_skill_name_rejects_path_traversal_inputs(raw_name: str) -> None:
    with pytest.raises(ValueError):
        skills_store.slugify_skill_name(raw_name)


def test_skill_path_resolves_inside_skills_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(skills_store, "SKILLS_DIR", skills_dir)

    path = skills_store._skill_path("code-review-v2")

    assert path == (skills_dir / "code-review-v2.md").resolve(strict=False)
    assert path.relative_to(skills_dir.resolve())


def test_skill_path_containment_guard_rejects_resolved_escape(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skills_dir = tmp_path / "skills"
    monkeypatch.setattr(skills_store, "SKILLS_DIR", skills_dir)

    with pytest.raises(ValueError, match="escapes the skills directory"):
        skills_store._assert_within_skills_dir(skills_dir / ".." / "escape.md")
