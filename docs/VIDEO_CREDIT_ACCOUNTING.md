# Retained video-credit accounting

Video-credit balances and ledger amounts use PostgreSQL `INTEGER`. The debit
query casts both subtraction operands to `integer` so PostgreSQL can resolve the
operator before asyncpg binds their values.

This repair preserves positive-amount and available-balance checks, the balance
row lock, transactional balance/ledger writes, and the existing unique refund
reference. It does not enable video generation or change grant limits or routes.

`tests/test_video_credits_postgres.py` qualifies real debits under restricted W
and owner logins, integer boundaries, insufficient balances, concurrent spending,
sequential/concurrent refund idempotency, and rollback after ledger failure. Run
with `--compute-metadata-postgres` and the existing owned-fixture workflow. These
tests use synthetic credits and the cached network-none PostgreSQL harness only.
