from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from orchestrator.config import get_settings


# Some runners/plugins change the working directory during collection/execution.
# Ensure the project root (which contains `orchestrator/`) is on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Issue #66 added fail-closed validation of DAEMON_ALLOWED_HOSTS at
# production import time in orchestrator/main.py. The test environment
# defaults to development so the import succeeds without per-test env
# boilerplate. Tests that need production semantics set
# ``DAEMON_ENVIRONMENT=production`` themselves and supply an allowlist.
os.environ.setdefault("DAEMON_ENVIRONMENT", "development")


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    # get_settings() is lru_cache'd; keep env-var mutations from leaking between tests.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
