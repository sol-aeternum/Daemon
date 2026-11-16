-- Migration: 070_shipped_skill_catalogue
-- Records which version of the shipped-skill catalogue the database holds
-- (docs/ACCOUNT_OWNED_SKILLS_DESIGN.md §5).
--
-- Shipped skills are published from a directory in the application image.
-- During a deploy or a rollback, instances of different releases run side by
-- side, each with its own copy of that directory. Without a fence, an older
-- instance would treat its older (or empty) directory as the truth and delete
-- or roll back what a newer release published.
--
-- One row holds the applied catalogue version. An instance applies its
-- directory only when its version is higher, under this row's lock; a lower
-- or equal version changes nothing. The table holds no skill content.

CREATE TABLE IF NOT EXISTS shipped_skill_catalogue (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    version INTEGER NOT NULL CHECK (version >= 0),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE shipped_skill_catalogue IS
    'One row: the version of the shipped-skill catalogue applied to this '
    'database. Only a higher version may publish or remove shipped skills.';
