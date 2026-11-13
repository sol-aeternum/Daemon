# Shipped skills

Skills in this directory ship with Daemon. Every account sees them in its skill
index, read-only. Design: `docs/ACCOUNT_OWNED_SKILLS_DESIGN.md` §5.

One directory per skill, named with the skill's slug (lowercase letters,
digits, hyphens or underscores), holding a `SKILL.md`:

```
orchestrator/shipped_skills/
  house-style/
    SKILL.md
```

`SKILL.md` has frontmatter with `name` and `description`, then the Markdown
body:

```
---
name: House Style
description: When and how to apply the house writing style.
---
# House Style
...
```

At backend startup the loader publishes each skill into the database. A changed
file becomes a new revision, an unchanged file writes nothing, and a removed
directory deletes that shipped skill. A file that cannot be parsed is logged
and its existing row is left alone. This file and any other top-level file are
ignored.

There are no shipped skills yet. Accounts cannot switch a shipped skill off
until the per-account setting in owner decision O2 exists, so add that before
adding the first one.
