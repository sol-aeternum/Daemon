"""Synthetic J1a commands; never an application phase applier."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from orchestrator.services.deletion_journal.model import Cause, Command, RequestFields

NOW = datetime(2035, 2, 10, 12, tzinfo=timezone.utc)


def request(account: UUID | None = None, operation: UUID | None = None, *, reset=False) -> Command:
    operation = operation or uuid4()
    return Command(
        account or uuid4(),
        operation,
        "reset_requested" if reset else "requested",
        NOW,
        RequestFields(
            "active",
            None,
            (Cause("reset" if reset else "deletion", operation),),
            1,
            NOW + timedelta(minutes=13),
            NOW + timedelta(days=1),
            NOW + timedelta(days=1),
        ),
    )
