"""Uncalled bounded deterministic CSV adapter using actual R0 and registry stores.

The host supplies the original operation/job and current attempt. No model calls,
legacy plaintext writer, automatic unknown-effect retry, or default tool changes.
"""

from __future__ import annotations

import csv
import hashlib
import io
from uuid import UUID, uuid4

import asyncpg

from orchestrator.artifact_reservations import IntentBinding, JobBinding, OutputReservations
from orchestrator.attempt_lifecycle import AttemptIdentity
from orchestrator.resource_presentations import ResourcePresentations
from orchestrator.resource_store import Publication, ResourceError
from orchestrator.services.account_control.guard import CapturedGeneration, guard_content_write


def encode_csv(rows: list[list[str]], *, byte_limit: int) -> bytes:
    if (
        type(byte_limit) is not int
        or not 1 <= byte_limit <= 8388608
        or type(rows) is not list
        or len(rows) > 10000
    ):
        raise ResourceError("too_large")
    output = io.StringIO(newline="")
    total = 0
    for row in rows:
        if (
            type(row) is not list
            or len(row) > 1000
            or any(type(v) is not str or len(v) > byte_limit for v in row)
        ):
            raise ResourceError("too_large")
        # UTF8 + worst-case quote escaping/delimiters bounded BEFORE row encoding.
        if sum(len(v) * 8 + 3 for v in row) > byte_limit * 8:
            raise ResourceError("too_large")
        line = io.StringIO(newline="")
        csv.writer(line, lineterminator="\n").writerow(row)
        value = line.getvalue()
        total += len(value.encode("utf-8"))
        if total > byte_limit:
            raise ResourceError("too_large")
        # Preserve native CSV semantics; data is downloaded, never executed here.
        output.write(value)
    return output.getvalue().encode("utf-8")


class ResourceCSV:
    def __init__(
        self,
        restricted_pool: asyncpg.Pool,
        presentations: ResourcePresentations,
        *,
        byte_limit: int,
    ) -> None:
        if (
            restricted_pool is presentations.inputs._tasks._pool
            or type(byte_limit) is not int
            or not 1 <= byte_limit <= 8388608
        ):
            raise ResourceError("invalid_composition")
        self.pool, self.presentations, self.store, self.byte_limit = (
            restricted_pool,
            presentations,
            presentations.store,
            byte_limit,
        )
        self.r0 = OutputReservations(self.store._descriptors)

    async def publish(
        self,
        identity: AttemptIdentity,
        job: JobBinding,
        slot: int,
        rows: list[list[str]],
        filename: str,
    ) -> Publication:
        identity.wire()
        job.wire()
        if (
            (job.account_id, job.account_generation, job.task_id)
            != (identity.account_id, identity.generation, identity.task_id)
            or type(slot) is not int
            or not 0 <= slot <= 2**31 - 1
        ):
            raise ResourceError("invalid")
        content = encode_csv(rows, byte_limit=self.byte_limit)
        expected = CapturedGeneration(identity.account_id, identity.generation)
        # Actual committed reservation, not a returned receipt, resolves replay.
        async with (
            self.presentations.inputs._account(expected.account_id) as conn,
            guard_content_write(conn, expected),
        ):
            existing = await conn.fetchval(
                "SELECT intent_id FROM artifact_output_intents WHERE account_id=$1 AND job_id=$2 AND output_slot=$3",
                expected.account_id,
                job.job_id,
                slot,
            )
            if existing is not None:
                command, kind = await self.store._intent(conn, expected, existing)
                descriptor = self.store._descriptors.decode(command)
                if (
                    kind != "output"
                    or command.job != job
                    or descriptor["filename"] != filename
                    or descriptor["sha256"] != hashlib.sha256(content).hexdigest()
                    or command.binding.measured_bytes != len(content)
                ):
                    raise ResourceError("conflict")
            else:
                await self.store._live(conn, identity)
                attempt = await conn.fetchval(
                    "SELECT id FROM task_attempts WHERE task_id=$1 AND epoch=$2",
                    identity.task_id,
                    identity.epoch,
                )
                if not isinstance(attempt, UUID):
                    raise ResourceError()
                command = self.store._descriptors.encode(
                    job,
                    IntentBinding(
                        uuid4(),
                        slot,
                        uuid4(),
                        uuid4(),
                        identity.lifecycle_id,
                        attempt,
                        identity.epoch,
                        len(content),
                    ),
                    filename=filename,
                    sha256=hashlib.sha256(content).hexdigest(),
                )
        if existing is None:
            # Preserve restrictive wrapper/role preflight. Commit BEFORE trusted I/O.
            conn = await self.pool.acquire()
            try:
                async with conn.transaction():
                    await self.r0.register_output_job(conn, identity, job)
                    await self.r0.reserve_output_intent(conn, identity, command)
            except BaseException:
                conn.terminate()
                raise ResourceError() from None
            finally:
                await self.pool.release(conn)
        publication = await self.store.publish_output(identity, command.binding.intent_id, content)
        await self.presentations.record(
            identity,
            [
                {
                    "resource_id": str(publication.resource_id),
                    "version_id": str(publication.version_id),
                }
            ],
        )
        return publication
