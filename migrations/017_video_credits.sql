-- Video Credit Balances and Transactions
-- Tracks user credit balances and all credit transactions (purchases, spends, refunds, admin grants)

-- Table for tracking user credit balances
CREATE TABLE IF NOT EXISTS video_credit_balances (
    user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Table for tracking all credit transactions
CREATE TABLE IF NOT EXISTS video_credit_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('purchase', 'spend', 'refund', 'admin_grant')),
    amount INTEGER NOT NULL,  -- Positive for credits added, negative for credits spent
    description TEXT,
    reference_id TEXT,  -- Nullable reference to related entity (e.g., payment ID, video generation ID)
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for efficient transaction history queries
CREATE INDEX IF NOT EXISTS idx_video_credit_transactions_user_created
    ON video_credit_transactions(user_id, created_at);