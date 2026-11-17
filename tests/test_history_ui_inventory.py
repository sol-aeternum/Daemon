"""Exact cleanup seam: no directory, alias, receiver or foreign-method admission."""

import pytest

from tests.inference_composition_support import ADMITTED, composition_violations

PATH = "orchestrator/services/restore_history/acceptance_host.py"
SOURCE = "class IsolatedAcceptanceHost:\n async def _ui_read(self):\n  async with retain_pool_cleanup():\n   pass\n"


def test_exact_ui_cleanup_callsite():
    assert PATH not in ADMITTED
    assert composition_violations({PATH: SOURCE}) == []


@pytest.mark.parametrize(
    "source,path",
    [
        (SOURCE.replace("_ui_read", "foreign"), PATH),
        (SOURCE.replace("retain_pool_cleanup()", "other.retain_pool_cleanup()"), PATH),
        (SOURCE.replace("retain_pool_cleanup()", "retain_pool_cleanup(extra=True)"), PATH),
        (
            "from orchestrator.services.connection_cleanup import retain_pool_cleanup as retained\n"
            + SOURCE.replace("retain_pool_cleanup()", "retained()"),
            PATH,
        ),
        (SOURCE, "orchestrator/services/restore_history/other.py"),
    ],
)
def test_ui_cleanup_rejects_nearby_forms(source, path):
    assert composition_violations({path: source})
