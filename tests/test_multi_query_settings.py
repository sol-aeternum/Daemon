"""M1's bounded defaults and deployment surfaces, without loading a dotenv file."""

import pytest
from typing import Any, cast
from pydantic import ValidationError

from orchestrator.config import Settings


def test_multi_query_defaults():
    settings = cast(Any, Settings)(_env_file=None)
    assert settings.memory_multi_query_enabled is False
    assert settings.memory_multi_query_merge == "additive"
    assert settings.memory_decomposition_timeout_ms == 1500
    assert settings.memory_subquery_quota == 3
    assert settings.memory_max_subqueries == 3
    assert settings.memory_decomposition_profile == "memory_decomposition"


def test_enabled_round_robin_rejected_at_startup():
    with pytest.raises(ValidationError, match="round_robin.*evaluation-only"):
        cast(Any, Settings)(
            _env_file=None,
            memory_multi_query_enabled=True,
            memory_multi_query_merge="round_robin",
        )


def test_disabled_round_robin_is_valid():
    settings = cast(Any, Settings)(
        _env_file=None,
        memory_multi_query_enabled=False,
        memory_multi_query_merge="round_robin",
    )
    assert settings.memory_multi_query_enabled is False
    assert settings.memory_multi_query_merge == "round_robin"


def test_enabled_with_default_additive_merge_is_valid():
    settings = cast(Any, Settings)(_env_file=None, memory_multi_query_enabled=True)
    assert settings.memory_multi_query_enabled is True
    assert settings.memory_multi_query_merge == "additive"


@pytest.mark.parametrize(
    "key,value",
    [
        ("memory_multi_query_merge", "rrf"),
        ("memory_decomposition_timeout_ms", 0),
        ("memory_subquery_quota", 0),
        ("memory_max_subqueries", 4),
        ("memory_max_subqueries", -1),
        ("memory_decomposition_profile", "background"),
    ],
)
def test_multi_query_invalid_settings_rejected(key, value):
    with pytest.raises(ValidationError):
        cast(Any, Settings)(_env_file=None, **{key: value})
