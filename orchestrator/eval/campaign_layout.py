"""Canonical campaign layouts; no execution mode or schema-based dispatch.

The legacy receipt stride is 4 slots x 50 questions = 200 per arm. B2b1 adds
generic v3 arm completion using receipts_per_arm. The following sites remain
legacy-only and unreachable for v3: Campaign.activate rejects fresh, recovery,
retry, final-verdict and replacement lineage pins before lineage verification.
- campaign_guard.py: finish_arm's recovery-only index * 200 receipt count.
- campaign_fresh.py:295-297,440,454,471: attempt index and arm receipt counts.
- campaign_recovery.py:119-124,339,345: receipt identities and handoff count/manifest.
- campaign_retry.py:883,891,1038: handoff count/manifest and recovery completion.
- campaign_final_verdict.py:639,685: receipt manifest and handoff count.
- campaign_restore.py:344: recovered-arm attach receipt count.

Allocation arm count is independent of receipt stride. Campaign defaults to the
legacy layout; B2a's v3 sampling is reachable only by explicit test injection.
Production launch, retry and finalisation remain legacy. In particular,
payload["schema"] is not a layout selector. Fresh-style handoff and retry wiring
are deferred to B2b2; B2b1's v3 allocation/attempt validation is pure and unbound.
"""

from __future__ import annotations

from dataclasses import dataclass

Slot = tuple[str, int | None]
LEGACY: tuple[Slot, ...] = (
    ("embedding", None),
    ("answer", None),
    ("judge", None),
    ("official_judge", None),
)
V3: tuple[Slot, ...] = (
    ("embedding", None),
    ("answer", 1),
    ("judge", 1),
    ("official_judge", 1),
    ("answer", 2),
    ("judge", 2),
    ("official_judge", 2),
    ("answer", 3),
    ("judge", 3),
    ("official_judge", 3),
)


def _index(value: int, bound: int, label: str) -> None:
    if type(value) is not int or not 0 <= value < bound:
        raise ValueError(f"Invalid {label}")


def _positive(value: int, label: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"Invalid {label}")


@dataclass(frozen=True)
class CampaignLayout:
    """Canonical ordered slots; constructing one grants no execution authority."""

    name: str
    slots: tuple[Slot, ...]

    def __post_init__(self) -> None:
        canonical = {"legacy": LEGACY, "v3": V3}.get(self.name)
        if (
            canonical is None
            or type(self.slots) is not tuple
            or self.slots != canonical
            or any(
                type(slot) is not tuple or (sample is not None and type(sample) is not int)
                for slot in self.slots
                for _, sample in (slot,)
            )
        ):
            raise ValueError("Unknown or non-canonical campaign layout")

    @property
    def slots_per_question(self) -> int:
        return len(self.slots)

    def receipts_per_arm(self, question_count: int) -> int:
        _positive(question_count, "question count")
        return question_count * self.slots_per_question

    def receipt_index(
        self,
        arm_index: int,
        question_index: int,
        slot_index: int,
        *,
        question_count: int,
        arm_count: int,
    ) -> int:
        """Zero-based serial index; arm_count is the allocation multiplier."""
        _positive(arm_count, "arm count")
        stride = self.receipts_per_arm(question_count)
        _index(arm_index, arm_count, "arm index")
        _index(question_index, question_count, "question index")
        _index(slot_index, self.slots_per_question, "slot index")
        return arm_index * stride + question_index * self.slots_per_question + slot_index

    def inverse(
        self, receipt_index: int, *, question_count: int, arm_count: int
    ) -> tuple[int, int, int]:
        _positive(arm_count, "arm count")
        stride = self.receipts_per_arm(question_count)
        _index(receipt_index, arm_count * stride, "receipt index")
        arm, within = divmod(receipt_index, stride)
        question, slot = divmod(within, self.slots_per_question)
        return arm, question, slot

    def identity(
        self, arm: str, question_id: str, slot_index: int, request_sha256: str
    ) -> dict[str, str | int]:
        _index(slot_index, self.slots_per_question, "slot index")
        if type(arm) is not str or not arm:
            raise ValueError("Invalid arm identity")
        if type(question_id) is not str or not question_id:
            raise ValueError("Invalid question identity")
        if (
            type(request_sha256) is not str
            or not request_sha256
            or len(request_sha256) != 64
            or any(c not in "0123456789abcdef" for c in request_sha256)
        ):
            raise ValueError("Invalid request SHA-256")
        stage, sample = self.slots[slot_index]
        result: dict[str, str | int] = {
            "arm": arm,
            "question_id": question_id,
            "stage": stage,
            "request_sha256": request_sha256,
        }
        if sample is not None:
            result["sample"] = sample
        return result


def layout(name: str) -> CampaignLayout:
    """Resolve only a named canonical layout, never a persisted schema field."""
    if name == "legacy":
        return CampaignLayout(name, LEGACY)
    if name == "v3":
        return CampaignLayout(name, V3)
    raise ValueError("Unknown campaign layout")
