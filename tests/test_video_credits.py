from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TypedDict, cast

import asyncpg
import pytest

import db.video_credits as video_credits_module
from config.video_pricing import estimate_cost
from db.video_credits import Result, VideoCreditsDAL


class FakeUniqueViolationError(Exception):
    pass


class FakeTransactionRow(TypedDict):
    id: uuid.UUID
    user_id: uuid.UUID
    type: str
    amount: int
    description: str | None
    reference_id: str | None
    created_at: datetime


def make_transaction_row(
    *,
    user_id: uuid.UUID,
    transaction_type: str,
    amount: int,
    description: str | None,
    reference_id: str | None,
) -> FakeTransactionRow:
    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "type": transaction_type,
        "amount": amount,
        "description": description,
        "reference_id": reference_id,
        "created_at": datetime.now(timezone.utc),
    }


@dataclass
class FakeDbState:
    balances: dict[uuid.UUID, int] = field(default_factory=dict)
    transactions: list[FakeTransactionRow] = field(default_factory=list)
    balance_locks: dict[uuid.UUID, asyncio.Lock] = field(default_factory=dict)
    transaction_locks: dict[uuid.UUID, asyncio.Lock] = field(default_factory=dict)

    def get_balance_lock(self, user_id: uuid.UUID) -> asyncio.Lock:
        return self.balance_locks.setdefault(user_id, asyncio.Lock())

    def get_transaction_lock(self, transaction_id: uuid.UUID) -> asyncio.Lock:
        return self.transaction_locks.setdefault(transaction_id, asyncio.Lock())


class FakePoolAcquire:
    def __init__(self, state: FakeDbState) -> None:
        self._state = state

    async def __aenter__(self) -> FakeConnection:
        return FakeConnection(self._state)

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeTransaction:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeTransaction:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        while self._connection.held_locks:
            self._connection.held_locks.pop().release()
        return None


class FakeConnection:
    def __init__(self, state: FakeDbState) -> None:
        self.state = state
        self.held_locks: list[asyncio.Lock] = []

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)

    async def fetchrow(
        self, query: str, *args: object
    ) -> FakeTransactionRow | dict[str, int | uuid.UUID] | None:
        normalized = " ".join(query.split()).lower()

        if "select balance from video_credit_balances" in normalized:
            user_id = args[0]
            assert isinstance(user_id, uuid.UUID)
            if "for update" in normalized:
                lock = self.state.get_balance_lock(user_id)
                await lock.acquire()
                self.held_locks.append(lock)
                await asyncio.sleep(0)
            if user_id not in self.state.balances:
                return None
            return {"balance": self.state.balances[user_id]}

        if (
            "insert into video_credit_transactions" in normalized
            and "values ($1, 'spend'" in normalized
        ):
            user_id, amount, description, reference_id = args
            assert isinstance(user_id, uuid.UUID)
            assert isinstance(amount, int)
            transaction = make_transaction_row(
                user_id=user_id,
                transaction_type="spend",
                amount=amount,
                description=cast(str | None, description),
                reference_id=cast(str | None, reference_id),
            )
            self.state.transactions.append(transaction)
            return {"id": transaction["id"]}

        if (
            "insert into video_credit_transactions" in normalized
            and "values ($1, 'refund'" in normalized
        ):
            user_id, amount, description, reference_id = args
            assert isinstance(user_id, uuid.UUID)
            assert isinstance(amount, int)
            if any(
                tx["type"] == "refund" and tx["reference_id"] == reference_id
                for tx in self.state.transactions
            ):
                raise video_credits_module.asyncpg.UniqueViolationError()
            transaction = make_transaction_row(
                user_id=user_id,
                transaction_type="refund",
                amount=amount,
                description=cast(str | None, description),
                reference_id=cast(str | None, reference_id),
            )
            self.state.transactions.append(transaction)
            return {"id": transaction["id"]}

        if (
            "insert into video_credit_transactions" in normalized
            and "values ($1, $2, $3, $4)" in normalized
        ):
            user_id, credit_type, amount, description = args
            assert isinstance(user_id, uuid.UUID)
            assert isinstance(credit_type, str)
            assert isinstance(amount, int)
            transaction = make_transaction_row(
                user_id=user_id,
                transaction_type=credit_type,
                amount=amount,
                description=cast(str | None, description),
                reference_id=None,
            )
            self.state.transactions.append(transaction)
            return {"id": transaction["id"]}

        if (
            "select id, user_id, type, amount, description, reference_id" in normalized
            and "for update" in normalized
        ):
            transaction_id = args[0]
            assert isinstance(transaction_id, uuid.UUID)
            transaction = next(
                (
                    tx
                    for tx in self.state.transactions
                    if tx["id"] == transaction_id and tx["type"] == "spend"
                ),
                None,
            )
            if transaction is None:
                return None
            lock = self.state.get_transaction_lock(transaction_id)
            await lock.acquire()
            self.held_locks.append(lock)
            return transaction

        if (
            "insert into video_credit_balances" in normalized
            and "returning balance" in normalized
        ):
            user_id = args[0]
            amount = args[1]
            assert isinstance(user_id, uuid.UUID)
            assert isinstance(amount, int)
            new_balance = self.state.balances.get(user_id, 0) + amount
            self.state.balances[user_id] = new_balance
            return {"balance": new_balance}

        raise AssertionError(f"Unhandled fetchrow query: {query}")

    async def fetch(self, query: str, *args: object) -> list[FakeTransactionRow]:
        normalized = " ".join(query.split()).lower()
        if "from video_credit_transactions" in normalized:
            user_id, limit, offset = args
            assert isinstance(user_id, uuid.UUID)
            assert isinstance(limit, int)
            assert isinstance(offset, int)
            filtered = [
                tx for tx in self.state.transactions if tx["user_id"] == user_id
            ]
            filtered.sort(key=lambda tx: tx["created_at"], reverse=True)
            return filtered[offset : offset + limit]

        raise AssertionError(f"Unhandled fetch query: {query}")

    async def execute(self, query: str, *args: object) -> str:
        normalized = " ".join(query.split()).lower()
        if (
            "insert into video_credit_balances" in normalized
            and "do update set balance = video_credit_balances.balance - $3"
            in normalized
        ):
            user_id, current_balance, amount = args
            assert isinstance(user_id, uuid.UUID)
            assert isinstance(current_balance, int)
            assert isinstance(amount, int)
            self.state.balances[user_id] = current_balance - amount
            return "UPDATE 1"

        raise AssertionError(f"Unhandled execute query: {query}")


class FakePool:
    def __init__(self, state: FakeDbState) -> None:
        self._state = state

    def acquire(self) -> FakePoolAcquire:
        return FakePoolAcquire(self._state)


@pytest.fixture
def fake_state(monkeypatch: pytest.MonkeyPatch) -> FakeDbState:
    monkeypatch.setattr(asyncpg, "UniqueViolationError", FakeUniqueViolationError)
    return FakeDbState()


@pytest.fixture
def dal(fake_state: FakeDbState) -> VideoCreditsDAL:
    return VideoCreditsDAL(cast(asyncpg.Pool, cast(object, FakePool(fake_state))))


@pytest.fixture
def user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.mark.asyncio
async def test_get_balance_defaults_to_zero(
    dal: VideoCreditsDAL, user_id: uuid.UUID
) -> None:
    assert await dal.get_balance(user_id) == 0


@pytest.mark.asyncio
async def test_credit_admin_grant_updates_balance_and_transaction(
    dal: VideoCreditsDAL,
    fake_state: FakeDbState,
    user_id: uuid.UUID,
) -> None:
    result = await dal.credit_credits(user_id, 25, "admin_grant", "grant for QA")

    assert result == Result(
        success=True,
        message="Credits credited successfully",
        transaction_id=result.transaction_id,
        new_balance=25,
    )
    assert result.transaction_id is not None
    assert fake_state.balances[user_id] == 25
    assert fake_state.transactions[0]["type"] == "admin_grant"
    assert fake_state.transactions[0]["amount"] == 25


@pytest.mark.asyncio
async def test_debit_credits_success(
    dal: VideoCreditsDAL,
    fake_state: FakeDbState,
    user_id: uuid.UUID,
) -> None:
    fake_state.balances[user_id] = 12

    result = await dal.debit_credits(
        user_id, 7, "video generation", reference_id="vid-123"
    )

    assert result.success is True
    assert result.message == "Credits debited successfully"
    assert result.new_balance == 5
    assert fake_state.balances[user_id] == 5
    assert fake_state.transactions[0]["type"] == "spend"
    assert fake_state.transactions[0]["reference_id"] == "vid-123"


@pytest.mark.asyncio
async def test_debit_credits_rejects_insufficient_balance(
    dal: VideoCreditsDAL,
    fake_state: FakeDbState,
    user_id: uuid.UUID,
) -> None:
    fake_state.balances[user_id] = 3

    result = await dal.debit_credits(user_id, 4, "too expensive")

    assert result == Result(success=False, message="Insufficient balance")
    assert fake_state.balances[user_id] == 3
    assert fake_state.transactions == []


@pytest.mark.asyncio
async def test_refund_transaction_success(
    dal: VideoCreditsDAL,
    fake_state: FakeDbState,
    user_id: uuid.UUID,
) -> None:
    fake_state.balances[user_id] = 2
    spend_id = uuid.uuid4()
    fake_state.transactions.append(
        {
            "id": spend_id,
            "user_id": user_id,
            "type": "spend",
            "amount": 8,
            "description": "video debit",
            "reference_id": "gen-1",
            "created_at": datetime.now(timezone.utc),
        }
    )

    result = await dal.refund_transaction(spend_id)

    assert result.success is True
    assert result.new_balance == 10
    assert fake_state.balances[user_id] == 10
    refund = fake_state.transactions[-1]
    assert refund["type"] == "refund"
    assert refund["reference_id"] == str(spend_id)


@pytest.mark.asyncio
async def test_refund_transaction_is_idempotent(
    dal: VideoCreditsDAL,
    fake_state: FakeDbState,
    user_id: uuid.UUID,
) -> None:
    spend_id = uuid.uuid4()
    fake_state.transactions.append(
        {
            "id": spend_id,
            "user_id": user_id,
            "type": "spend",
            "amount": 5,
            "description": "video debit",
            "reference_id": "gen-2",
            "created_at": datetime.now(timezone.utc),
        }
    )

    first = await dal.refund_transaction(spend_id)
    second = await dal.refund_transaction(spend_id)

    assert first.success is True
    assert second == Result(success=False, message="Transaction already refunded")
    assert sum(1 for tx in fake_state.transactions if tx["type"] == "refund") == 1


@pytest.mark.asyncio
async def test_get_transactions_returns_latest_first(
    dal: VideoCreditsDAL,
    fake_state: FakeDbState,
    user_id: uuid.UUID,
) -> None:
    older = datetime(2026, 3, 14, 1, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 3, 14, 2, 0, tzinfo=timezone.utc)
    fake_state.transactions.extend(
        [
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "type": "purchase",
                "amount": 5,
                "description": "older",
                "reference_id": None,
                "created_at": older,
            },
            {
                "id": uuid.uuid4(),
                "user_id": user_id,
                "type": "refund",
                "amount": 3,
                "description": "newer",
                "reference_id": None,
                "created_at": newer,
            },
        ]
    )

    transactions = await dal.get_transactions(user_id)

    assert [transaction.description for transaction in transactions] == [
        "newer",
        "older",
    ]


@pytest.mark.asyncio
async def test_concurrent_debit_allows_only_one_success(
    dal: VideoCreditsDAL,
    fake_state: FakeDbState,
    user_id: uuid.UUID,
) -> None:
    fake_state.balances[user_id] = 10

    results = await asyncio.gather(
        dal.debit_credits(user_id, 10, "video A", reference_id="gen-a"),
        dal.debit_credits(user_id, 10, "video B", reference_id="gen-b"),
    )

    successes = [result for result in results if result.success]
    failures = [result for result in results if not result.success]

    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].message == "Insufficient balance"
    assert fake_state.balances[user_id] == 0
    assert sum(1 for tx in fake_state.transactions if tx["type"] == "spend") == 1


def test_estimate_cost_applies_tier_discounts() -> None:
    assert estimate_cost(5, tier="pro") == 5
    assert estimate_cost(10, tier="max") == 8
    assert estimate_cost(15, tier="byok") == 0
