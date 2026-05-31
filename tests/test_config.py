from __future__ import annotations

from orchestrator.config import Settings


def test_background_reasoning_model_default() -> None:
    """Test BACKGROUND_REASONING_MODEL default value."""
    settings = Settings()
    assert settings.background_reasoning_model == "openrouter/deepseek/deepseek-chat"


def test_background_reasoning_model_env_override() -> None:
    """Test BACKGROUND_REASONING_MODEL can be overridden via env var."""
    settings = Settings(background_reasoning_model="openrouter/anthropic/claude-3.5-sonnet")
    assert settings.background_reasoning_model == "openrouter/anthropic/claude-3.5-sonnet"


def test_background_reasoning_model_from_env(monkeypatch) -> None:
    """Test BACKGROUND_REASONING_MODEL loaded from env var."""
    monkeypatch.setenv("BACKGROUND_REASONING_MODEL", "openrouter/google/gemini-2.5-flash")
    settings = Settings()
    assert settings.background_reasoning_model == "openrouter/google/gemini-2.5-flash"


def test_dreaming_flags_defaults() -> None:
    settings = Settings()
    assert settings.dreaming_enabled is True
    assert settings.dream_schedule_hour == 3
    assert settings.dream_min_cluster_size == 5


def test_dreaming_flags_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DREAMING_ENABLED", "true")
    monkeypatch.setenv("DREAM_SCHEDULE_HOUR", "5")
    monkeypatch.setenv("DREAM_MIN_CLUSTER_SIZE", "6")
    settings = Settings()
    assert settings.dreaming_enabled is True
    assert settings.dream_schedule_hour == 5
    assert settings.dream_min_cluster_size == 6


def test_retrieval_logging_flags_defaults() -> None:
    """Test retrieval_logging_enabled and retrieval_logging_debug defaults are False."""
    settings = Settings()
    assert settings.retrieval_logging_enabled is False
    assert settings.retrieval_logging_debug is False


def test_retrieval_logging_flags_from_env(monkeypatch) -> None:
    """Test retrieval_logging flags can be overridden via env vars."""
    monkeypatch.setenv("RETRIEVAL_LOGGING_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_LOGGING_DEBUG", "true")
    settings = Settings()
    assert settings.retrieval_logging_enabled is True
    assert settings.retrieval_logging_debug is True


def test_retrieval_logging_debug_only_from_env(monkeypatch) -> None:
    """Test retrieval_logging_debug can be enabled without retrieval_logging_enabled."""
    monkeypatch.setenv("RETRIEVAL_LOGGING_DEBUG", "true")
    settings = Settings()
    assert settings.retrieval_logging_enabled is False
    assert settings.retrieval_logging_debug is True


def test_background_reasoning_model_whitespace_env_preserved(monkeypatch) -> None:
    """Test BACKGROUND_REASONING_MODEL preserves whitespace-only value as-is."""
    monkeypatch.setenv("BACKGROUND_REASONING_MODEL", "   ")
    settings = Settings()
    assert settings.background_reasoning_model == "   "


def test_background_reasoning_model_empty_string_preserved(monkeypatch) -> None:
    """Test BACKGROUND_REASONING_MODEL preserves empty string as-is."""
    monkeypatch.setenv("BACKGROUND_REASONING_MODEL", "")
    settings = Settings()
    assert settings.background_reasoning_model == ""
