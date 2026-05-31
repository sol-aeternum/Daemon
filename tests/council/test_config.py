"""Tests for council config."""

from orchestrator.council.config import load_roster


class TestLoadRoster:
    def test_load_default_preset(self):
        roster = load_roster("default")
        assert "analyst" in roster
        assert "strategist" in roster
        assert "skeptic" in roster
        assert "contrarian" in roster
        assert "auditor" in roster
        assert len(roster) == 5

    def test_load_adversarial_preset(self):
        roster = load_roster("adversarial")
        assert len(roster) == 5

    def test_load_lean_preset(self):
        roster = load_roster("lean")
        assert "analyst" in roster
        assert "skeptic" in roster
        assert "contrarian" in roster
        assert len(roster) == 3

    def test_load_invalid_preset_fallback(self):
        roster = load_roster("nonexistent")
        assert len(roster) == 5

    def test_roster_contains_model_ids(self):
        roster = load_roster("default")
        for role, model_id in roster.items():
            assert model_id
            assert "/" in model_id

    def test_auditor_excluded_from_debate_seats(self):
        default_roster = load_roster("default")
        lean_roster = load_roster("lean")
        assert "auditor" in default_roster
        assert "auditor" not in lean_roster
