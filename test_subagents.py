#!/usr/bin/env python3
"""Test script for opencode subagents."""

import asyncio
import sys
import os


if "pytest" in sys.modules:
    import pytest

    pytest.skip(
        "Subagent smoke test script; run manually (not under pytest)",
        allow_module_level=True,
    )

# Add backend to path
sys.path.insert(0, "/app")

from orchestrator.subagents.base import SubagentType, SubagentManager
from orchestrator.subagents.research import ResearchSubagent
from orchestrator.subagents.image import ImageSubagent
from orchestrator.subagents.audio import AudioSubagent
from orchestrator.config import get_settings


async def test_subagents():
    """Test all implemented subagents."""
    print("=" * 60)
    print("OPENCODE SUBAGENT TEST SUITE")
    print("=" * 60)

    settings = get_settings()
    tier_config = settings.get_tier_config()

    # Initialize subagent manager
    manager = SubagentManager()

    shared_config = {
        "brave_api_key": settings.brave_api_key,
        "openrouter_api_key": settings.openrouter_api_key,
        "openrouter_base_url": settings.openrouter_base_url,
        "image_model": tier_config.image_agent.model
        if tier_config.image_agent
        else settings.tier_pro_image_model,
    }

    # Check if ElevenLabs key exists
    elevenlabs_key = getattr(settings, "elevenlabs_api_key", None)
    if elevenlabs_key:
        shared_config["elevenlabs_api_key"] = elevenlabs_key

    # Register subagents
    manager.register(ResearchSubagent(shared_config))
    manager.register(ImageSubagent(shared_config))
    manager.register(AudioSubagent(shared_config))

    results = []

    # Test 1: Research Subagent
    print("\n📚 TEST 1: Research Subagent")
    print("-" * 40)
    try:
        result = await manager.spawn(
            SubagentType.RESEARCH,
            "What are the top 3 AI breakthroughs in February 2026?",
            context={"task_type": "news"},
        )
        print(f"✅ Success: {result.success}")
        print(f"   Duration: {result.metadata.get('duration_ms', 'N/A')}ms")
        print(f"   Query Count: {result.metadata.get('query_count', 'N/A')}")
        # Result content is in result.data for research
        content_preview = str(result.data)[:200] if result.data else "No data"
        print(f"   Preview: {content_preview}...")
        results.append(("Research", True))
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        results.append(("Research", False))

    # Test 2: Image Subagent (skip if no API key)
    print("\n🖼️  TEST 2: Image Subagent")
    print("-" * 40)
    if not settings.openrouter_api_key:
        print("⚠️  Skipped: No OpenRouter API key configured")
        results.append(("Image", "skipped"))
    else:
        try:
            result = await manager.spawn(
                SubagentType.IMAGE,
                "A futuristic AI robot coding at a computer, pixel art style",
                context={"width": 512, "height": 512},
            )
            print(f"✅ Success: {result.success}")
            print(f"   Duration: {result.metadata.get('duration_ms', 'N/A')}ms")
            if result.data and result.data.get("image_path"):
                print(f"   Saved to: {result.data['image_path']}")
            results.append(("Image", True))
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback

            traceback.print_exc()
            results.append(("Image", False))

    # Test 3: Audio Subagent (skip if no API key)
    print("\n🔊 TEST 3: Audio Subagent")
    print("-" * 40)
    if not elevenlabs_key:
        print("⚠️  Skipped: No ElevenLabs API key configured")
        results.append(("Audio", "skipped"))
    else:
        try:
            result = await manager.spawn(
                SubagentType.AUDIO,
                "Create a sci-fi computer interface sound effect",
                context={"duration": 3},
            )
            print(f"✅ Success: {result.success}")
            print(f"   Duration: {result.metadata.get('duration_ms', 'N/A')}ms")
            if result.data and result.data.get("audio_path"):
                print(f"   Saved to: {result.data['audio_path']}")
            results.append(("Audio", True))
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback

            traceback.print_exc()
            results.append(("Audio", False))

    # Test 4: Unimplemented subagents
    print("\n⚠️  TEST 4: Unimplemented Subagents (CODE, READER)")
    print("-" * 40)
    for agent_type in [SubagentType.CODE, SubagentType.READER]:
        try:
            result = await manager.spawn(agent_type, "test task")
            status = (
                "✅ Implemented" if result.success else "❌ Not implemented (expected)"
            )
            print(f"   {agent_type.value.upper()}: {status}")
        except Exception as e:
            print(f"   {agent_type.value.upper()}: ❌ Error - {e}")

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    for name, status in results:
        icon = "✅" if status == True else "⚠️" if status == "skipped" else "❌"
        status_str = (
            status if isinstance(status, str) else ("PASS" if status else "FAIL")
        )
        print(f"{icon} {name}: {status_str}")

    passed = sum(1 for _, s in results if s == True)
    skipped = sum(1 for _, s in results if s == "skipped")
    failed = sum(1 for _, s in results if s == False)

    print(f"\nTotal: {passed} passed, {skipped} skipped, {failed} failed")
    print("=" * 60)

    return passed, skipped, failed


if __name__ == "__main__":
    passed, skipped, failed = asyncio.run(test_subagents())
    sys.exit(0 if failed == 0 else 1)
