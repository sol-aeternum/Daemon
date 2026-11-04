"""Exact reservation import/seam admission without runtime activation."""

import pytest

from tests.inference_composition_support import ADMITTED, composition_violations
from tests.test_artifact_reservations import reservation_references_allowed

PATH = "orchestrator/services/restore_history/effects.py"
IMPORT = "from orchestrator.artifact_reservations import OutputReservations, IntentCommand\n"


def test_exact_reservation_types_do_not_admit_a_runtime_module():
    assert reservation_references_allowed(PATH, IMPORT)
    assert PATH not in ADMITTED
    assert not reservation_references_allowed("orchestrator/main.py", IMPORT)
    assert not composition_violations({PATH: IMPORT})


@pytest.mark.parametrize(
    "source",
    [
        IMPORT.replace("IntentCommand", "IntentCommand as Packet"),
        IMPORT.replace(", IntentCommand", ""),
        IMPORT.rstrip() + ", JobBinding\n",
        IMPORT.replace("orchestrator.artifact_reservations", ".artifact_reservations"),
        "import orchestrator.artifact_reservations as r0",
        "from orchestrator import artifact_reservations",
        "def other():\n " + IMPORT,
        IMPORT + "other='artifact_reservations'\n",
        "__import__('orchestrator.artifact_reservations')",
    ],
)
def test_reservation_import_exception_is_exact(source):
    assert not reservation_references_allowed(PATH, source)


def test_exact_reservation_seams_are_owned_only_by_the_new_join():
    calls = (
        "publisher._reserved_transaction()\n "
        "publisher._presentation_snapshot(conn, identity)\n "
        "reservations.register_output_job(conn, identity, command.job)\n "
        "reservations.reserve_output_intent(conn, identity, command)\n "
        "retain_pool_cleanup()"
    )
    assert not composition_violations({PATH: "async def _reserve_output():\n " + calls})
    assert composition_violations({PATH: "async def other():\n " + calls})
    assert composition_violations(
        {"orchestrator/main.py": "async def _reserve_output():\n " + calls}
    )


@pytest.mark.parametrize(
    "call",
    [
        "reservations.register_output_job(conn, identity, command.job, callback)",
        "reservations.reserve_output_intent(conn, identity, command, stage=callback)",
        "other.reserve_output_intent(conn, identity, command)",
        "alias(conn, identity, command)",
        "getattr(reservations,'reserve_output_intent')(conn, identity, command, callback)",
        "publisher._presentation_snapshot(conn, other_identity)",
        "OutputReservations(codec)",
    ],
)
def test_reservation_seams_refuse_aliases_callbacks_and_construction(call):
    source = "async def _reserve_output():\n alias=reservations.reserve_output_intent\n " + call
    assert composition_violations({PATH: source})
