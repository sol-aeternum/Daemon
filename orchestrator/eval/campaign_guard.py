"""Opt-in LongMemEval measurement guard, not a production compute authority.

No activation from environment variables or import side effects. The operator must
provide a pinned plan and hold the campaign context. All state is outside the DB
being restored. Interrupted/open campaigns are reconciliation-only, never resumed.
This bounds local request reservations, not a provider's eventual billing.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, AsyncIterator, Callable, Iterator, Mapping

ARMS = ("A0", "A1", "A2", "C0")
STAGES = ("embedding", "answer", "judge", "official_judge")
CAPS = {"embedding": 0, "answer": 256, "judge": 256, "official_judge": 10}
LLM_MODEL = "openrouter/openai/gpt-4o-2024-08-06"
EMBEDDING_MODEL = "openai/text-embedding-3-small"
BASE_URL = "https://openrouter.ai/api/v1"
INPUT_RATE = Decimal("0.0000025")
OUTPUT_RATE = Decimal("0.00001")
EMBEDDING_RATE = Decimal("0.00000002")
NEW_HOLD = Decimal("4.35")
PRIOR_HOLD = Decimal("6.0519050")
CAMPAIGN_CEILING = Decimal("10.4019050")
LLM_START_INTERVAL = 5.0
_active: ContextVar[Campaign | None] = ContextVar("lme_campaign", default=None)


class CampaignStopped(RuntimeError):
    """Campaign is stopped or a precondition failed; operator reconciliation only."""


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write(path: Path, value: Any) -> None:
    """Unlike the general eval writer, directory durability errors are fatal."""
    fd, temporary = tempfile.mkstemp(prefix=".campaign-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(_json(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _integer(value: object, *, positive: bool = False) -> int:
    if type(value) is not int or value < int(positive):
        raise CampaignStopped("Missing or invalid integer usage/allocation")
    return value


@dataclass(frozen=True)
class CampaignPlan:
    """Pre-approved exact campaign pins; no defaults authorizing execution.

    ``pins`` binds source SHAs, dataset/config/formatter/snapshot/fingerprint and
    dated route/rate evidence. The launcher must verify them against actual state.
    This primitive deliberately supplies no restore or paid-launch command.
    """

    question_ids: tuple[str, ...]
    input_limits: Mapping[str, Mapping[str, int]]
    pins: Mapping[str, Any]

    def payload(self) -> dict[str, Any]:
        recovery = "recovery_lineage" in self.pins
        if recovery:
            from orchestrator.eval.campaign_recovery import validate_mode

            validate_mode(self.pins)
        prior_hold = Decimal("10.4019050") if recovery else PRIOR_HOLD
        ceiling = Decimal("14.7519050") if recovery else CAMPAIGN_CEILING
        if len(self.question_ids) != 50 or len(set(self.question_ids)) != 50:
            raise CampaignStopped("Campaign requires exactly 50 unique dev IDs")
        if any(not isinstance(qid, str) or not qid for qid in self.question_ids):
            raise CampaignStopped("Invalid question identity")
        if set(self.input_limits) != set(self.question_ids) or not self.pins:
            raise CampaignStopped("Missing campaign pins/allocations")
        total = Decimal(0)
        for limits in self.input_limits.values():
            if set(limits) != set(STAGES):
                raise CampaignStopped("Each question needs exactly four stages")
            for stage in STAGES:
                tokens = _integer(limits[stage], positive=True)
                if stage == "embedding":
                    if tokens > 8000:
                        raise CampaignStopped("Embedding exceeds qualified input cap")
                    amount = (tokens * EMBEDDING_RATE * 1_000_000).to_integral_value(
                        rounding=ROUND_CEILING
                    ) / 1_000_000
                else:
                    amount = tokens * INPUT_RATE + CAPS[stage] * OUTPUT_RATE
                total += (3 if recovery else 4) * amount
        if total > NEW_HOLD or prior_hold + NEW_HOLD > ceiling:
            raise CampaignStopped("Allocations exceed approved whole-envelope reservation")
        payload = {
            "schema": 1,
            "arms": ARMS,
            "question_ids": self.question_ids,
            "input_limits": self.input_limits,
            "pins": self.pins,
            "new_reserved_usd": str(NEW_HOLD),
            "prior_reserved_usd": str(prior_hold),
            "cumulative_reserved_usd": str(prior_hold + NEW_HOLD),
            "allocated_usd": str(total),
            "llm_start_interval_seconds": LLM_START_INTERVAL,
            "prior_restore_attempts": 3 if recovery else 1,
            "maximum_cumulative_restore_attempts": 6 if recovery else 5,
        }
        if recovery:
            payload.update(execution_arms=("A1", "A2", "C0"), separate_capacity_reserved_usd="1")
        return payload


def current_campaign() -> Campaign | None:
    return _active.get()


def reject_legacy_embedding() -> None:
    """Must run before each legacy transport; ordinary fallback catches cannot send."""
    campaign = current_campaign()
    if campaign is not None:
        campaign.fail("Legacy embedding transport is outside the campaign")


class CallReceipt:
    def __init__(self, campaign: Campaign | None, stage: str, request: dict[str, Any]):
        self.campaign = campaign
        self.stage = stage
        self.request = request
        self.completed = False

    def capture(self, response: Mapping[str, Any]) -> None:
        if self.campaign is not None:
            self.campaign.capture(self.stage, self.request, response)

    def complete(self, response: Mapping[str, Any]) -> None:
        if self.campaign is not None:
            self.campaign.receive(self.stage, self.request, response)
        self.completed = True

    def failure_details(self, exc: BaseException) -> None:
        if self.campaign is not None:
            self.campaign.failure_details(self.stage, self.request, exc)


@contextmanager
def campaign_call(stage: str, request: dict[str, Any]) -> Iterator[CallReceipt]:
    campaign = current_campaign()
    receipt = CallReceipt(campaign, stage, request)
    if campaign is not None:
        campaign.before_send(stage, request)
    try:
        yield receipt
        if campaign is not None and not receipt.completed:
            campaign.fail("Call exited without a validated durable receipt")
    except BaseException:
        if campaign is not None:
            campaign.stop("Call interrupted or failed; retain reservation and pending evidence")
        raise


@asynccontextmanager
async def campaign_llm_call(stage: str, request: dict[str, Any]) -> AsyncIterator[CallReceipt]:
    """Await only the remaining start interval; ordinary calls stay default-inert."""
    campaign = current_campaign()
    if campaign is None:
        with campaign_call(stage, request) as receipt:
            yield receipt
        return
    campaign._check()
    if campaign._pacing or stage == "embedding":
        campaign.fail("Concurrent or non-LLM pacing request")
    campaign._pacing = True
    try:
        campaign._validate_request(stage, request)
        while True:
            campaign._check()
            remaining = LLM_START_INTERVAL - (time.monotonic() - campaign._last_llm_start)
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)
        # campaign_call revalidates stage/state and persists pending before yield.
        with campaign_call(stage, request) as receipt:
            campaign._last_llm_start = time.monotonic()
            yield receipt
    except BaseException:
        if not campaign._stopped:
            campaign.stop("Paced call interrupted; no automatic replay")
        raise
    finally:
        campaign._pacing = False


class Campaign:
    """One process owns the campaign. Any unclean exit permanently blocks reentry.

    The exclusive lock is cooperative, not proof of database producer exclusion;
    the action-time restore guard must separately establish that exclusion.
    """

    def __init__(self, directory: Path, plan: CampaignPlan, count_tokens: Callable[[str], int]):
        self.directory = directory
        # Freeze caller-owned mutable data before any network activity.
        self.plan: dict[str, Any] = json.loads(_json(plan.payload()))
        self._plan_hash = _hash(self.plan)
        self.count_tokens = count_tokens
        self.state: dict[str, Any] = {}
        self._lock_fd: int | None = None
        self._pid = os.getpid()
        self._live = False
        self._stopped = False
        self._pacing = False
        # Also enforce a full interval after a new process/clean arm handoff.
        self._last_llm_start = time.monotonic()
        self.database_pool: Any = None  # Explicitly bound only after verified restore.

    @property
    def state_path(self) -> Path:
        return self.directory / "campaign.json"

    def _save(self) -> None:
        try:
            _write(self.state_path, self.state)
        except BaseException:
            self._stopped = True
            raise

    def _check(self) -> None:
        if (
            not self._live
            or self._pid != os.getpid()
            or self._stopped
            or self.state.get("status") != "running"
            or _hash(self.plan) != self._plan_hash
        ):
            self._stopped = True
            raise CampaignStopped("Campaign inactive/stopped; no automatic replay")

    def _recovery_check(self) -> dict[str, Any] | None:
        if "recovery_lineage" not in self.plan["pins"]:
            return None
        from orchestrator.eval.campaign_recovery import verify_recovery

        return verify_recovery(self.plan, self.directory)

    def stop(self, reason: str) -> None:
        self._stopped = True  # Remains latched even if persistence/callers fail.
        self.state.update(status="stopped_reconciliation_required", reason=reason)
        self._save()

    def fail(self, reason: str) -> None:
        self.stop(reason)
        raise CampaignStopped(reason)

    @contextmanager
    def activate(self) -> Iterator[Campaign]:
        if self._live or current_campaign() is not None:
            raise CampaignStopped("Nested campaign activation refused")
        # The shared sibling lock prevents parallel campaigns in this evidence root.
        # The operator pins this root for the benchmark, never a DB-restored path.
        parent = self.directory.parent.resolve(strict=True)
        if self.directory.is_symlink() or parent != self.directory.parent.absolute():
            raise CampaignStopped("Campaign paths must be direct, nonsymlink evidence paths")
        fd = os.open(parent / ".lme-campaign.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        token = None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd = fd
            inherited = self._recovery_check()
            if self.directory.exists():
                saved = json.loads(self.state_path.read_bytes())
                if (
                    saved.get("status") != "ready_next_arm"
                    or saved.get("plan_sha256") != self._plan_hash
                    or saved.get("plan") != self.plan
                    or saved.get("pending") is not None
                    or saved.get("restore_pending") is not None
                    or saved.get("question") is not None
                    or saved.get("arm") is not None
                    or not 0 < saved.get("arm_index", 0) < len(ARMS)
                ):
                    raise CampaignStopped("Only a clean completed-arm handoff may continue")
                if inherited is not None:
                    from orchestrator.eval.campaign_recovery import validate_handoff

                    validate_handoff(self.directory, saved, inherited)
                self.state = saved
                self.state["status"] = "running"
            else:
                self.directory.mkdir(mode=0o700)
                _sync_directory(parent)
                self.state = {
                    "status": "running",
                    "plan": self.plan,
                    "plan_sha256": self._plan_hash,
                    "arm_index": 0,
                    "arm": None,
                    "question_index": 0,
                    "question": None,
                    "stage_index": 0,
                    "pending": None,
                    "receipts": [],
                    "restores": [],
                }
                if inherited is not None:
                    from orchestrator.eval.campaign_recovery import bootstrap

                    bootstrap(self.directory, self.state, inherited)
            self._live = True
            self._save()  # Whole-envelope hold is durable BEFORE context is exposed.
            token = _active.set(self)
            initial_arm_index = self.state["arm_index"]
            yield self
            self._check()
            if (
                self.state["arm"] is not None
                or self.state["arm_index"] == 0
                or (inherited is not None and self.state["arm_index"] != initial_arm_index + 1)
                or self.state["pending"]
                or self.state.get("restore_pending")
            ):
                self.fail("Incomplete campaign; reconcile rather than resume")
            self.state["status"] = (
                "completed" if self.state["arm_index"] == len(ARMS) else "ready_next_arm"
            )
            self._save()
        except BaseException:
            if self._live and not self._stopped:
                self.stop("Campaign exited before verified completion")
            raise
        finally:
            if token is not None:
                _active.reset(token)
            self._live = False
            self._lock_fd = None
            os.close(fd)

    def start_arm(
        self, arm: str, *, verified_pins: Mapping[str, Any], restore: Mapping[str, Any]
    ) -> None:
        """Called only AFTER separately reviewed restore and actual-state checks.

        This is evidence binding, NOT a substitute for the action-time restore
        guard, producer exclusion or qualification. No destructive commands here.
        """
        self._check()
        index = self.state["arm_index"]
        if "recovery_lineage" in self.plan["pins"]:
            if (
                index < 1
                or self.state.get("restore_pending") is not None
                or self.state.get("restore_attempts") != index
                or len(self.state.get("restore_receipts", [])) != index
                or len(self.state["restores"]) != index - 1
            ):
                self.fail("Recovery arm requires a new verified restore")
        if index >= len(ARMS) or arm != ARMS[index] or self.state["arm"] is not None:
            self.fail("Unexpected or repeated arm")
        if dict(verified_pins) != self.plan["pins"] or not restore:
            self.fail("Actual pins or restore evidence missing/mismatched")
        self.state["restores"].append(dict(restore))
        self.state.update(arm=arm, question_index=0, question=None, stage_index=0)
        self._save()

    def pending_restore(self, arm: str, identity: Mapping[str, Any]) -> None:
        """Reserve exactly one destructive attempt before any target ALTER/DROP."""
        self._check()
        try:
            self._recovery_check()  # Under sibling lock, immediately before mutation admission.
        except BaseException:
            self.stop("Recovery pre-mutation verification failed; no replay")
            raise
        index = self.state["arm_index"]
        offset = 1 if "recovery_lineage" in self.plan["pins"] else 0
        if (
            index < offset
            or self.plan["prior_restore_attempts"] + index - offset + 1
            > self.plan["maximum_cumulative_restore_attempts"]
            or index >= len(ARMS)
            or ARMS[index] != arm
            or self.state["arm"] is not None
            or self.state.get("restore_pending") is not None
            or self.state.get("restore_attempts", 0) != index - offset
        ):
            self.fail("Restore repeated, unordered or unresolved")
        self.state["restore_attempts"] = index - offset + 1
        self.state["restore_pending"] = {"arm": arm, "identity": dict(identity)}
        self._save()

    def restored(self, arm: str, evidence: Mapping[str, Any]) -> None:
        self._check()
        if self.state.get("restore_pending", {}).get("arm") != arm or not evidence:
            self.fail("Restore receipt lacks pending identity")
        self.state.setdefault("restore_receipts", []).append(dict(evidence))
        self.state["restore_pending"] = None
        self._save()

    def evaluation_pool(self) -> Any:
        self._check()
        if self.database_pool is None or self.state.get("restore_pending"):
            self.fail("Verified preopened campaign pool missing")
        return self.database_pool

    def begin_question(self, question_id: str) -> None:
        self._check()
        index = self.state["question_index"]
        if (
            self.state["arm"] is None
            or self.state["question"] is not None
            or index >= len(self.plan["question_ids"])
            or question_id != self.plan["question_ids"][index]
        ):
            self.fail("Question reordered, repeated or outside approved arm")
        self.state.update(question=question_id, stage_index=0)
        self._save()

    def before_send(self, stage: str, request: dict[str, Any]) -> None:
        self._check()
        try:
            self._validate_request(stage, request)
        except Exception:
            self.stop("Prepared request differs from approved allocation/route/stage")
            raise
        self.state["pending"] = {
            "arm": self.state["arm"],
            "question_id": self.state["question"],
            "stage": stage,
            "request_sha256": _hash(request),
        }
        self._save()

    def _validate_request(self, stage: str, request: dict[str, Any]) -> None:
        if (
            self.state["pending"] is not None
            or self.state["question"] is None
            or self.state["stage_index"] >= len(STAGES)
            or stage != STAGES[self.state["stage_index"]]
        ):
            raise CampaignStopped("Unexpected/concurrent send")
        bound = self.plan["input_limits"][self.state["question"]][stage]
        if stage == "embedding":
            if (
                set(request) != {"route_id", "model", "endpoint", "dimensions", "input", "provider"}
                or request["route_id"] != "azure-openrouter-small-1024"
                or request["model"] != EMBEDDING_MODEL
                or request["endpoint"] != BASE_URL
                or request["dimensions"] != 1024
                or not isinstance(request["input"], list)
                or len(request["input"]) != 1
                or not isinstance(request["input"][0], str)
            ):
                raise CampaignStopped("Unexpected embedding request")
            provider = request["provider"]
            if (
                provider.get("order") != ["azure"]
                or provider.get("only") != ["azure"]
                or provider.get("allow_fallbacks") is not False
                or provider.get("data_collection") != "deny"
                or provider.get("zdr") is not True
                or provider.get("require_parameters") is not True
            ):
                raise CampaignStopped("Embedding transport pin changed")
            # UTF-8 byte bound includes all input, never a character heuristic.
            measured = len(request["input"][0].encode())
        else:
            if (
                set(request)
                != {
                    "model",
                    "messages",
                    "temperature",
                    "max_tokens",
                    "seed",
                    "num_retries",
                    "extra_body",
                    "api_base",
                }
                or request["model"] != LLM_MODEL
                or request["api_base"] != BASE_URL
                or request["temperature"] != 0
                or type(request["max_tokens"]) is not int
                or request["max_tokens"] != CAPS[stage]
                or request["seed"] != 42
                or request["num_retries"] != 0
                or request["extra_body"]
                != {"provider": {"order": ["azure"], "allow_fallbacks": False}}
            ):
                raise CampaignStopped("Unexpected LLM request")
            messages = request["messages"]
            if not isinstance(messages, list) or not messages:
                raise CampaignStopped("Missing messages")
            # All actual message text, including the full generated hypothesis.
            # 64 is a separately validated envelope allowance, not variable-text slack.
            measured = 64
            for message in messages:
                if set(message) != {"role", "content"} or message["role"] not in {"system", "user"}:
                    raise CampaignStopped("Unexpected message envelope")
                for value in message.values():
                    if not isinstance(value, str):
                        raise CampaignStopped("Nontext message")
                    measured += _integer(self.count_tokens(value))
        if measured > bound:
            raise CampaignStopped("Actual prepared request exceeds reserved input allocation")

    def receive(self, stage: str, request: dict[str, Any], response: Mapping[str, Any]) -> None:
        self._check()
        pending = self.state["pending"]
        if not pending or pending["stage"] != stage or pending["request_sha256"] != _hash(request):
            self.fail("Receipt does not match pending request")
        try:
            self.capture(stage, request, response)
            usage = response.get("usage")
            if not isinstance(usage, dict):
                raise CampaignStopped("Missing usage")
            prompt = _integer(usage.get("prompt_tokens"), positive=True)
            completion = (
                0
                if stage == "embedding" and "completion_tokens" not in usage
                else _integer(usage.get("completion_tokens"))
            )
            total = _integer(usage.get("total_tokens"), positive=True)
            if total != prompt + completion or completion > CAPS[stage]:
                raise CampaignStopped("Inconsistent usage or output cap")
            if prompt > self.plan["input_limits"][self.state["question"]][stage]:
                raise CampaignStopped("Provider usage exceeds held allocation")
            expected = (
                (EMBEDDING_MODEL, "text-embedding-3-small")
                if stage == "embedding"
                else (LLM_MODEL, LLM_MODEL.removeprefix("openrouter/"))
            )
            if response.get("model") not in expected:
                raise CampaignStopped("Receipt model differs from pin")
            if "provider" in response and response["provider"] not in {"azure", "Azure"}:
                raise CampaignStopped("Receipt provider differs from pin")
            # Cost is optional provider metadata, never coerced into a zero charge.
            rate = EMBEDDING_RATE if stage == "embedding" else INPUT_RATE
            cost_bound = prompt * rate + completion * OUTPUT_RATE
            if "cost" in usage:
                if type(usage["cost"]) not in (str, float, int):
                    raise CampaignStopped("Invalid cost type")
                cost = Decimal(str(usage["cost"]))
                if not cost.is_finite() or cost < 0 or cost > cost_bound:
                    raise CampaignStopped("Invalid/unexpected provider cost")
            number = len(self.state["receipts"])
            path = self.directory / f"receipt-{number:04d}.json"
            self.state["receipts"].append({**pending, "artifact": path.name})
            self.state.update(pending=None, stage_index=self.state["stage_index"] + 1)
            self._save()
        except BaseException:
            self.stop("Receipt/usage/durability failure; no replay")
            raise

    def capture(self, stage: str, request: dict[str, Any], response: Mapping[str, Any]) -> None:
        """Retain allowlisted raw response fields even when validation later fails."""
        self._check()
        pending = self.state["pending"]
        if not pending or pending["stage"] != stage or pending["request_sha256"] != _hash(request):
            self.fail("Raw receipt differs from pending request")
        usage = response.get("usage")
        evidence: dict[str, Any] = {
            "model": response.get("model"),
            "usage": {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
                if key in usage
            }
            if isinstance(usage, dict)
            else None,
        }
        choices = response.get("choices")
        if isinstance(choices, list):
            evidence["choices"] = []
            for choice in choices:
                if isinstance(choice, dict):
                    item = {
                        key: choice[key]
                        for key in ("index", "finish_reason", "text")
                        if key in choice
                    }
                    message = choice.get("message")
                    if isinstance(message, dict):
                        item["message"] = {
                            key: message[key] for key in ("role", "content") if key in message
                        }
                    evidence["choices"].append(item)
        if stage == "embedding":
            # Exact receipt content hash without exporting the vector array.
            evidence["data_sha256"] = _hash(response.get("data"))
        path = self.directory / f"receipt-{len(self.state['receipts']):04d}.json"
        _write(path, {"pending": pending, "response": evidence})

    def failure_details(self, stage: str, request: dict[str, Any], exc: BaseException) -> None:
        """Separate sanitized failure evidence; never invent usage or clear pending."""
        self._check()
        pending = self.state["pending"]
        if not pending or pending["stage"] != stage or pending["request_sha256"] != _hash(request):
            self.fail("Failure does not match pending request")
        from orchestrator.eval.campaign_recovery import failure_diagnostics

        try:
            _write(
                self.directory / f"failure-{len(self.state['receipts']):04d}.json",
                {"pending": pending, "diagnostics": failure_diagnostics(exc)},
            )
        except BaseException:
            self.stop("Failure diagnostics durability failure; retain pending; no replay")
            raise

    def finish_question(self, result: Mapping[str, Any]) -> None:
        self._check()
        if (
            self.state["pending"]
            or self.state["stage_index"] != len(STAGES)
            or result.get("question_id") != self.state["question"]
            or result.get("error")
            or result.get("official_judge_status") != "completed"
            or result.get("official_judgment") not in {"correct", "incorrect"}
        ):
            self.fail("Question lacks complete canonical evidence")
        self.state.update(
            question=None, question_index=self.state["question_index"] + 1, stage_index=0
        )
        self._save()

    def finish_arm(self, *, qualification: Mapping[str, Any]) -> None:
        """Bind external full acceptance/manifest checks; never substitute for them."""
        self._check()
        if "recovery_lineage" in self.plan["pins"]:
            index = self.state["arm_index"]
            if (
                not 1 <= index < len(ARMS)
                or self.state["arm"] != ARMS[index]
                or self.state.get("restore_attempts") != index
                or len(self.state["restores"]) != index
                or len(self.state.get("restore_receipts", [])) != index
                or len(self.state["receipts"]) != index * 200
                or len(self.state["qualifications"]) != index
            ):
                self.fail("Recovery arm lacks full new execution/restore evidence")
        if (
            self.state["question"] is not None
            or self.state["question_index"] != 50
            or self.state["pending"]
            or self.state.get("restore_pending")
            or not qualification
        ):
            self.fail("Arm incomplete or not qualified")
        self.state.setdefault("qualifications", []).append(dict(qualification))
        self.state.update(arm=None, arm_index=self.state["arm_index"] + 1)
        self._save()
