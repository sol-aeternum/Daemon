from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# LiteLLM defaults to DEV mode and calls load_dotenv() during import. Test
# worktrees can be nested below a primary checkout, so that search may cross
# the worktree boundary and load the primary checkout's credentials. Disable
# LiteLLM's implicit dotenv loading before any test module can import it.
os.environ["LITELLM_MODE"] = "PRODUCTION"

from orchestrator.config import get_settings


# Some runners/plugins change the working directory during collection/execution.
# Ensure the project root (which contains `orchestrator/`) is on sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Issue #454: opt-in dump of pending asyncio tasks when a test phase stalls.
# Inert unless DAEMON_PYTEST_ASYNCIO_DUMP_S is set (the CI pytest step sets it).
from tests.asyncio_stall_dump import (  # noqa: E402
    pytest_configure as pytest_configure,
    pytest_runtest_call as pytest_runtest_call,
    pytest_runtest_setup as pytest_runtest_setup,
    pytest_runtest_teardown as pytest_runtest_teardown,
)


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
