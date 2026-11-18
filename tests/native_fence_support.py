"""Offline stand-in for the #731 native fence in fake-pool startup tests.

Real-PostgreSQL behaviour is covered by test_history_native_fence_postgres.py.
This only replaces the presence connection where the test already fakes
``asyncpg.create_pool``; it is never used by production code.
"""

import importlib
from unittest.mock import AsyncMock

from orchestrator import db as db_module
from orchestrator.services.restore_history import native_fence

# ``orchestrator.worker.worker`` is shadowed by the package's ARQ Worker object.
worker_module = importlib.import_module("orchestrator.worker.worker")


class FakeFence(native_fence.NativeFence):
    instances: list["FakeFence"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(on_lost=lambda: None)
        self.dsn: str | None = None
        self.closed = False
        self.guard = AsyncMock()  # type: ignore[method-assign]
        FakeFence.instances.append(self)

    async def open(self, dsn: str, *, timeout: int = 10) -> None:
        self.dsn = dsn
        self._open = True

    @property
    def alive(self) -> bool:
        return self._open

    async def close(self) -> None:
        self.closed, self._open = True, False


def install_fake_fence(monkeypatch) -> list[FakeFence]:
    FakeFence.instances = []
    monkeypatch.setattr(db_module, "NativeFence", FakeFence)
    monkeypatch.setattr(worker_module, "NativeFence", FakeFence)
    return FakeFence.instances
