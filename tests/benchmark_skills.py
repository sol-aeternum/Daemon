#!/usr/bin/env python3
"""Daemon autonomous skills benchmark.

Runs a deterministic verification benchmark over the completed autonomous-skill
surfaces without touching production state. The scenarios are intentionally
lightweight and use temporary files plus stubs/mocks for external systems.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, cast
from unittest.mock import AsyncMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator.skill_evaluator import (  # noqa: E402
    SkillDraft,
    SkillEvaluationRequest,
    SkillEvaluator,
)
from orchestrator.skills_projection import (  # noqa: E402
    SkillProjectionStore,
    compute_content_hash,
)
from orchestrator.skills_store import (  # noqa: E402
    SKILL_INDEX_TOKEN_BUDGET,
    build_skill_index,
)
from orchestrator.skills_upgrade import (  # noqa: E402
    MANIFEST_FILENAME,
    SNAPSHOT_DIRNAME,
    SkillManifest,
    SkillManifestEntry,
    SkillUpgradeService,
)
from orchestrator.tools.skill_manage import SkillManageTool  # noqa: E402


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    duration_seconds: float
    details: dict[str, Any]
    error: str | None = None


class MockRecord:
    """Small asyncpg.Record-like helper for benchmark stubs."""

    def __init__(self, **kwargs: Any) -> None:
        self._data = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Any:
        return iter(self._data.keys())

    def keys(self) -> Any:
        return self._data.keys()

    def values(self) -> Any:
        return self._data.values()

    def items(self) -> Any:
        return self._data.items()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


class StubStore:
    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        conversation: dict[str, Any] | None = None,
    ) -> None:
        self._messages = messages or []
        self._conversation = conversation or {"summary": "Benchmark conversation"}

    async def get_messages(
        self,
        conversation_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        del conversation_id, limit, offset
        return self._messages

    async def get_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        del conversation_id
        return self._conversation


class StubProjectionStore:
    def __init__(self, search_results: list[dict[str, Any]] | None = None) -> None:
        self.search_by_embedding = AsyncMock(return_value=search_results or [])
        self.update_autonomous_metadata = AsyncMock(return_value=True)
        self.get_projection = AsyncMock(return_value=None)


class StubSkillManageTool:
    def __init__(self, response: str = "{}") -> None:
        self._response = response
        self.call_args: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> str:
        self.call_args.append(kwargs)
        return self._response


class BenchmarkSkillManageTool(SkillManageTool):
    def install_benchmark_stubs(
        self,
        *,
        projection_store: object,
        sync_service: object,
    ) -> None:
        self._projection_store = cast(Any, projection_store)
        self._sync_service = cast(Any, sync_service)


def _build_qualifying_messages(assistant_message_id: uuid.UUID) -> list[dict[str, Any]]:
    return [
        {
            "id": uuid.uuid4(),
            "role": "user",
            "content": "Please turn this debugging sequence into a reusable procedure.",
            "status": "complete",
        },
        {
            "id": assistant_message_id,
            "role": "assistant",
            "content": "I'll convert the successful debugging steps into a reusable skill.",
            "status": "complete",
            "metadata": {"finish_reason": "stop"},
            "tool_calls": [
                {"name": "read", "arguments": {"file": "main.py"}},
                {"name": "grep", "arguments": {"pattern": "error"}},
                {"name": "read", "arguments": {"file": "config.py"}},
                {"name": "grep", "arguments": {"pattern": "debug"}},
                {"name": "read", "arguments": {"file": "utils.py"}},
            ],
            "tool_results": [
                {"name": "read", "result": {"content": "main"}},
                {"name": "grep", "result": {"matches": 3}},
                {"name": "read", "result": {"content": "config"}},
                {"name": "grep", "result": {"matches": 2}},
                {"name": "read", "result": {"content": "utils"}},
            ],
        },
    ]


async def _run_scenario(
    name: str,
    scenario_fn: Callable[[], Any],
) -> ScenarioResult:
    started = time.perf_counter()
    try:
        details = await scenario_fn()
        return ScenarioResult(
            name=name,
            passed=True,
            duration_seconds=round(time.perf_counter() - started, 4),
            details=details,
        )
    except Exception as exc:
        return ScenarioResult(
            name=name,
            passed=False,
            duration_seconds=round(time.perf_counter() - started, 4),
            details={},
            error=str(exc),
        )


async def scenario_skill_manage_roundtrip() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        tool = BenchmarkSkillManageTool(db_pool=AsyncMock())
        sync_service = SimpleNamespace(
            sync_skill=AsyncMock(return_value=SimpleNamespace(success=True))
        )
        projection_store = SimpleNamespace(
            list_projections=AsyncMock(
                return_value=[
                    {
                        "skill_id": "benchmark-skill",
                        "source_type": "autonomous",
                        "allow_autonomous_edit": True,
                        "repo_version": None,
                        "local_version": None,
                        "use_count": 7,
                        "last_used_at": None,
                    }
                ]
            ),
            touch_usage=AsyncMock(return_value=True),
        )
        tool.install_benchmark_stubs(
            projection_store=projection_store,
            sync_service=sync_service,
        )

        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            create_payload = json.loads(
                await tool.execute(
                    action="create",
                    name="Benchmark Skill",
                    description="Benchmark roundtrip verification.",
                    content="# Benchmark Skill\n\n## Purpose\n\nReusable verification steps.",
                    source_type="autonomous",
                    caller_autonomous=True,
                )
            )
            list_payload = json.loads(await tool.execute(action="list"))
            view_payload = json.loads(
                await tool.execute(action="view", skill_id="benchmark-skill")
            )

        assert create_payload["created"] is True
        assert create_payload["skill_id"] == "benchmark-skill"
        assert len(list_payload) == 1
        assert "content" not in list_payload[0]
        assert list_payload[0]["source_type"] == "autonomous"
        assert list_payload[0]["use_count"] == 7
        assert view_payload["id"] == "benchmark-skill"
        assert "Reusable verification steps." in view_payload["content"]
        projection_store.touch_usage.assert_awaited_once_with("benchmark-skill")

        return {
            "skill_id": create_payload["skill_id"],
            "l0_metadata_only": True,
            "l1_content_loaded": True,
            "usage_touched": True,
        }


async def scenario_l0_index_budget() -> dict[str, Any]:
    summaries = [
        {
            "id": "low-use",
            "name": "Low Use",
            "description": "Low-priority fallback skill.",
            "enabled": True,
            "updated_at": "2026-04-01T00:00:00Z",
            "source_type": "manual",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": 1,
            "last_used_at": None,
        },
        {
            "id": "high-use",
            "name": "High Use",
            "description": "Preferred debugging workflow.",
            "enabled": True,
            "updated_at": "2026-04-02T00:00:00Z",
            "source_type": "system",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": 50,
            "last_used_at": None,
        },
        {
            "id": "long-desc",
            "name": "Long Desc",
            "description": "token " * 700,
            "enabled": True,
            "updated_at": "2026-03-01T00:00:00Z",
            "source_type": "autonomous",
            "allow_autonomous_edit": None,
            "repo_version": None,
            "local_version": None,
            "pending_update": None,
            "use_count": 0,
            "last_used_at": None,
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        with patch("orchestrator.skills_store.SKILLS_DIR", tmp_path):
            with patch("orchestrator.skills_store.list_skills", return_value=summaries):
                index = await build_skill_index(db_pool=None)

    lines = [line for line in index.splitlines() if line.startswith("- ")]
    assert index.startswith("Skill Index (L0):")
    assert lines[0].startswith("- High Use:")
    assert "[system]" in index
    assert "[manual]" in index
    assert "Long Desc" not in index
    assert len(index) <= SKILL_INDEX_TOKEN_BUDGET * 4

    return {
        "entries": len(lines),
        "first_entry": lines[0],
        "budget_chars": len(index),
        "budget_limit_estimate": SKILL_INDEX_TOKEN_BUDGET * 4,
    }


async def scenario_autonomous_create() -> dict[str, Any]:
    assistant_message_id = uuid.uuid4()
    request = SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=assistant_message_id,
        tool_call_count=6,
    )
    projection_store = StubProjectionStore(search_results=[])
    skill_manage_tool = StubSkillManageTool(
        response=json.dumps(
            {
                "skill_id": "debug-workflow",
                "name": "Debug Workflow",
                "description": "A reusable debugging workflow.",
                "source_type": "autonomous",
                "created": True,
            }
        )
    )
    evaluator = SkillEvaluator(
        store=StubStore(_build_qualifying_messages(assistant_message_id)),
        db_pool=None,
        projection_store=projection_store,
        skill_manage_tool=skill_manage_tool,
        query_embedder=AsyncMock(return_value=[0.1] * 1024),
    )
    with patch.object(
        evaluator,
        "_generate_skill_draft",
        AsyncMock(
            return_value=SkillDraft(
                name="Debug Workflow",
                description="A reusable debugging workflow.",
                trigger_conditions="Use when debugging errors in the repository.",
                skill_markdown="# Debug Workflow\n\n## Purpose\n\nReusable debugging steps.",
            )
        ),
    ):
        result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "created"
    assert result.created_skill_id == "debug-workflow"
    assert len(skill_manage_tool.call_args) == 1
    assert skill_manage_tool.call_args[0]["action"] == "create"
    assert skill_manage_tool.call_args[0]["caller_autonomous"] is True
    projection_store.update_autonomous_metadata.assert_awaited_once_with(
        "debug-workflow",
        trigger_conditions="Use when debugging errors in the repository.",
        complexity_origin=6,
    )

    return {
        "classification": result.classification,
        "created_skill_id": result.created_skill_id,
        "metadata_updated": True,
    }


async def scenario_protected_skip() -> dict[str, Any]:
    assistant_message_id = uuid.uuid4()
    request = SkillEvaluationRequest(
        user_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        assistant_message_id=assistant_message_id,
        tool_call_count=7,
    )
    projection_store = StubProjectionStore(
        search_results=[
            {
                "skill_id": "system-debug",
                "name": "Debug Workflow",
                "source_type": "system",
                "allow_autonomous_edit": False,
                "similarity": 0.92,
                "complexity_origin": 6,
            }
        ]
    )
    skill_manage_tool = StubSkillManageTool()
    evaluator = SkillEvaluator(
        store=StubStore(_build_qualifying_messages(assistant_message_id)),
        db_pool=None,
        projection_store=projection_store,
        skill_manage_tool=skill_manage_tool,
        query_embedder=AsyncMock(return_value=[0.1] * 1024),
    )
    with patch.object(
        evaluator,
        "_generate_skill_draft",
        AsyncMock(
            return_value=SkillDraft(
                name="Debug Workflow",
                description="A debugging workflow.",
                trigger_conditions="Use when debugging.",
                skill_markdown="# Debug Workflow\n\nContent.",
            )
        ),
    ):
        result = await evaluator.evaluate_completed_turn(request)

    assert result.classification == "skipped_protected_match"
    assert result.matched_skill_id == "system-debug"
    assert result.protected is True
    assert len(skill_manage_tool.call_args) == 0

    return {
        "classification": result.classification,
        "matched_skill_id": result.matched_skill_id,
        "matched_source_type": result.matched_source_type,
        "protected": result.protected,
    }


async def scenario_pending_update() -> dict[str, Any]:
    old_content = (
        "---\nname: Modified Skill\ndescription: Modified\nenabled: true\n"
        "repo_version: 1.0.0\nlocal_version: 1.0.0\n---\n"
        "# Modified Skill\n\nOriginal content."
    )
    new_repo_content = (
        "---\nname: Modified Skill\ndescription: Modified\nenabled: true\n"
        "repo_version: 2.0.0\nlocal_version: 1.0.0\n---\n"
        "# Modified Skill\n\nNew content from repo."
    )
    user_modified_content = (
        "---\nname: Modified Skill\ndescription: Modified\nenabled: true\n"
        "repo_version: 2.0.0\nlocal_version: 1.1.0\n---\n"
        "# Modified Skill\n\nUser modified content."
    )
    old_hash = compute_content_hash(old_content)
    user_hash = compute_content_hash(user_modified_content)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        skill_file = tmp_path / "modified-skill.md"
        skill_file.write_text(user_modified_content, encoding="utf-8")

        snapshot_dir = tmp_path / SNAPSHOT_DIRNAME
        snapshot_dir.mkdir()
        (snapshot_dir / "modified-skill.md").write_text(old_content, encoding="utf-8")

        manifest = SkillManifest()
        manifest.skills["modified-skill"] = SkillManifestEntry(
            repo_hash=old_hash,
            repo_version="1.0.0",
            local_version="1.0.0",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        (tmp_path / MANIFEST_FILENAME).write_text(
            json.dumps(manifest.to_dict()), encoding="utf-8"
        )

        pool = AsyncMock()
        pool.fetchrow.return_value = MockRecord(
            skill_id="modified-skill",
            name="Modified Skill",
            description="Modified",
            source_file_path=str(skill_file),
            source_hash=user_hash,
            enabled=True,
            source_type="system",
            created_by="system",
            origin_url="",
            embedding=None,
            repo_version="2.0.0",
            local_version="1.1.0",
            pending_update=None,
            allow_autonomous_edit=False,
            trigger_conditions="",
            complexity_origin=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_synced_at=datetime.now(),
            last_used_at=None,
            use_count=0,
        )
        pool.fetch.return_value = []

        store = SkillProjectionStore(pool)
        with patch("orchestrator.skills_upgrade.SKILLS_DIR", tmp_path):
            with patch(
                "orchestrator.skills_upgrade.embed_skill_content",
                AsyncMock(return_value=[0.1] * 1024),
            ):
                service = SkillUpgradeService(store)
                result = await service.sync_repo_skills(
                    {"modified-skill": new_repo_content}
                )

        pending_action = next(a for a in result.actions if a.action == "pending_update")
        local_content = skill_file.read_text(encoding="utf-8")

    assert result.total_pending_updates == 1
    assert pending_action.skill_id == "modified-skill"
    assert pending_action.success is True
    assert pending_action.details is not None
    assert pending_action.details.get("repo_version") == "2.0.0"
    assert "User modified content" in local_content

    return {
        "action": pending_action.action,
        "repo_version": pending_action.details["repo_version"],
        "local_content_preserved": True,
    }


async def run_benchmark() -> list[ScenarioResult]:
    scenarios = [
        ("skill_manage_roundtrip", scenario_skill_manage_roundtrip),
        ("l0_index_budget", scenario_l0_index_budget),
        ("autonomous_create", scenario_autonomous_create),
        ("protected_skip", scenario_protected_skip),
        ("pending_update", scenario_pending_update),
    ]

    results: list[ScenarioResult] = []
    for name, scenario_fn in scenarios:
        results.append(await _run_scenario(name, scenario_fn))
    return results


def _print_summary(results: list[ScenarioResult]) -> bool:
    print("Daemon Skill Benchmark")
    print(f"Scenarios: {len(results)}")

    for result in results:
        status = "✓" if result.passed else "✗"
        print(f"{status} {result.name} ({result.duration_seconds:.4f}s)")
        if result.passed:
            print(f"  {json.dumps(result.details, sort_keys=True)}")
        elif result.error:
            print(f"  ERROR: {result.error}")

    passed = all(result.passed for result in results)
    print()
    print(
        "✅ BENCHMARK PASSED"
        if passed
        else "❌ BENCHMARK FAILED — see scenario errors above"
    )
    return passed


def _build_output(results: list[ScenarioResult], passed: bool) -> dict[str, Any]:
    return {
        "run_timestamp": datetime.now(timezone.utc).isoformat(),
        "benchmark": "autonomous-skills",
        "scenario_count": len(results),
        "passed": passed,
        "scenarios": [asdict(result) for result in results],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Daemon autonomous skills benchmark")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output after the human summary.",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Skip writing benchmark output under tests/results/.",
    )
    args = parser.parse_args()

    results = asyncio.run(run_benchmark())
    passed = _print_summary(results)
    output = _build_output(results, passed)

    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))

    if not args.no_save:
        results_dir = REPO_ROOT / "tests" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        output_path = results_dir / (
            f"skill_benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        output_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
        print(f"Results saved to {output_path.relative_to(REPO_ROOT)}")

    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
