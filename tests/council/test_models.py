"""Tests for council data models."""

import pytest
from orchestrator.council.models import (
    CouncilConfig,
    CouncilSession,
    CouncilOutput,
    AuditFinding,
    PerspectiveType,
    PerspectiveResponse,
)


class TestCouncilConfig:
    def test_default_config(self):
        config = CouncilConfig()
        assert config.round_count == 2
        assert config.audit_enabled is False
        assert config.interview_bypass is False
        assert config.preset_name == "default"

    def test_custom_config(self):
        config = CouncilConfig(round_count=3, audit_enabled=True)
        assert config.round_count == 3
        assert config.audit_enabled is True

    def test_round_count_validation(self):
        with pytest.raises(ValueError):
            CouncilConfig(round_count=0)
        with pytest.raises(ValueError):
            CouncilConfig(round_count=5)

    def test_heterogeneity_validation(self):
        with pytest.raises(ValueError):
            CouncilConfig(
                roster={
                    "analyst": "openai/gpt-4",
                    "strategist": "openai/gpt-3.5",
                }
            )

    def test_valid_roster(self):
        config = CouncilConfig(
            roster={
                "analyst": "anthropic/claude-opus-4.6",
                "strategist": "google/gemini-3.1-pro-preview",
                "skeptic": "moonshotai/kimi-k2-thinking",
                "contrarian": "x-ai/grok-3",
                "auditor": "deepseek/deepseek-r1",
            }
        )
        assert len(config.roster) == 5


class TestPerspectiveType:
    def test_perspective_types(self):
        assert PerspectiveType.ANALYST.value == "analyst"
        assert PerspectiveType.STRATEGIST.value == "strategist"
        assert PerspectiveType.SKEPTIC.value == "skeptic"
        assert PerspectiveType.CONTRARIAN.value == "contrarian"
        assert PerspectiveType.AUDITOR.value == "auditor"


class TestPerspectiveResponse:
    def test_perspective_response(self):
        resp = PerspectiveResponse(
            perspective=PerspectiveType.ANALYST,
            content="Test content",
            confidence=7.5,
        )
        assert resp.perspective == PerspectiveType.ANALYST
        assert resp.content == "Test content"
        assert resp.confidence == 7.5


class TestAuditFinding:
    def test_audit_finding(self):
        finding = AuditFinding(
            category="logic",
            severity="critical",
            description="Test finding",
        )
        assert finding.category == "logic"
        assert finding.severity == "critical"
        assert finding.description == "Test finding"


class TestCouncilSession:
    def test_council_session_init(self):
        config = CouncilConfig()
        session = CouncilSession(
            session_id="test-123",
            conversation_id="conv-456",
            prompt="Test prompt?",
            config=config,
        )
        assert session.session_id == "test-123"
        assert session.conversation_id == "conv-456"
        assert session.prompt == "Test prompt?"
        assert session.config == config
        assert len(session.rounds) == 0

    def test_to_db_record(self):
        config = CouncilConfig()
        session = CouncilSession(
            session_id="test-123",
            conversation_id="conv-456",
            prompt="Test prompt?",
            config=config,
        )
        record = session.to_db_record()
        assert record["id"] == "test-123"
        assert record["conversation_id"] == "conv-456"
        assert record["prompt"] == "Test prompt?"


class TestCouncilOutput:
    def test_council_output(self):
        output = CouncilOutput(
            summary="Test summary",
            perspectives_summary={"analyst": "Analysis..."},
            consensus="Agreed",
            findings=[],
        )
        assert output.summary == "Test summary"
        assert output.perspectives_summary["analyst"] == "Analysis..."
        assert output.consensus == "Agreed"
        assert len(output.findings) == 0
