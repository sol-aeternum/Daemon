import asyncpg
import uuid
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Transaction:
    id: uuid.UUID
    user_id: uuid.UUID
    type: str
    amount: int
    description: str | None
    reference_id: str | None
    created_at: str


@dataclass
class Result:
    success: bool
    message: str
    transaction_id: uuid.UUID | None = None
    new_balance: int | None = None


class VideoCreditsDAL:
    def __init__(self, db_pool: asyncpg.Pool):
        self._pool: asyncpg.Pool = db_pool

    async def get_balance(self, user_id: uuid.UUID) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT balance FROM video_credit_balances 
                WHERE user_id = $1
                """,
                user_id,
            )
            return row["balance"] if row else 0

    async def debit_credits(
        self,
        user_id: uuid.UUID,
        amount: int,
        description: str,
        reference_id: str | None = None,
    ) -> Result:
        if amount <= 0:
            return Result(success=False, message="Amount must be positive")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT balance FROM video_credit_balances 
                    WHERE user_id = $1
                    FOR UPDATE
                    """,
                    user_id,
                )

                current_balance = row["balance"] if row else 0

                if current_balance < amount:
                    return Result(success=False, message="Insufficient balance")

                await conn.execute(
                    """
                    INSERT INTO video_credit_balances (user_id, balance, updated_at)
                    VALUES ($1, $2 - $3, NOW())
                    ON CONFLICT (user_id) 
                    DO UPDATE SET balance = video_credit_balances.balance - $3, 
                                  updated_at = NOW()
                    """,
                    user_id,
                    current_balance,
                    amount,
                )

                transaction_row = await conn.fetchrow(
                    """
                    INSERT INTO video_credit_transactions 
                    (user_id, type, amount, description, reference_id)
                    VALUES ($1, 'spend', $2, $3, $4)
                    RETURNING id
                    """,
                    user_id,
                    amount,
                    description,
                    reference_id,
                )

                new_balance = current_balance - amount
                return Result(
                    success=True,
                    message="Credits debited successfully",
                    transaction_id=transaction_row["id"],
                    new_balance=new_balance,
                )

    async def credit_credits(
        self, user_id: uuid.UUID, amount: int, credit_type: str, description: str
    ) -> Result:
        if amount <= 0:
            return Result(success=False, message="Amount must be positive")

        if credit_type not in ("purchase", "refund", "admin_grant"):
            return Result(success=False, message="Invalid credit type")

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO video_credit_balances (user_id, balance, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id) 
                    DO UPDATE SET balance = video_credit_balances.balance + $2, 
                                  updated_at = NOW()
                    RETURNING balance
                    """,
                    user_id,
                    amount,
                )

                new_balance = row["balance"]

                transaction_row = await conn.fetchrow(
                    """
                    INSERT INTO video_credit_transactions 
                    (user_id, type, amount, description)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    user_id,
                    credit_type,
                    amount,
                    description,
                )

                return Result(
                    success=True,
                    message="Credits credited successfully",
                    transaction_id=transaction_row["id"],
                    new_balance=new_balance,
                )

    async def get_transactions(
        self, user_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> list[Transaction]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, type, amount, description, reference_id, created_at
                FROM video_credit_transactions
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                user_id,
                limit,
                offset,
            )

            return [
                Transaction(
                    id=row["id"],
                    user_id=row["user_id"],
                    type=row["type"],
                    amount=row["amount"],
                    description=row["description"],
                    reference_id=row["reference_id"],
                    created_at=row["created_at"].isoformat()
                    if row["created_at"]
                    else "",
                )
                for row in rows
            ]

    async def refund_transaction(self, transaction_id: uuid.UUID) -> Result:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT id, user_id, type, amount, description, reference_id
                    FROM video_credit_transactions
                    WHERE id = $1 AND type = 'spend'
                    FOR UPDATE
                    """,
                    transaction_id,
                )

                if not row:
                    return Result(
                        success=False, message="Transaction not found or not refundable"
                    )

                original_transaction = row

                refund_description = f"Refund for transaction {transaction_id}: {original_transaction['description']}"
                try:
                    transaction_row = await conn.fetchrow(
                        """
                        INSERT INTO video_credit_transactions 
                        (user_id, type, amount, description, reference_id)
                        VALUES ($1, 'refund', $2, $3, $4)
                        RETURNING id
                        """,
                        original_transaction["user_id"],
                        original_transaction["amount"],
                        refund_description,
                        str(transaction_id),
                    )
                except asyncpg.UniqueViolationError:
                    return Result(success=False, message="Transaction already refunded")

                row = await conn.fetchrow(
                    """
                    INSERT INTO video_credit_balances (user_id, balance, updated_at)
                    VALUES ($1, $2, NOW())
                    ON CONFLICT (user_id) 
                    DO UPDATE SET balance = video_credit_balances.balance + $2, 
                                  updated_at = NOW()
                    RETURNING balance
                    """,
                    original_transaction["user_id"],
                    original_transaction["amount"],
                )

                new_balance = row["balance"]

                return Result(
                    success=True,
                    message="Transaction refunded successfully",
                    transaction_id=transaction_row["id"],
                    new_balance=new_balance,
                )
