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

## Catalogue version

`VERSION` holds one whole number: the version of this directory's catalogue.
**Increase it in the same change whenever anything under this directory
changes**: a skill added, edited or removed.

At backend startup the loader applies the directory to the database only when
its version is higher than the one the database already holds. It then
publishes every skill (a changed file becomes a new revision, an unchanged file
writes nothing), removes shipped skills that are no longer here, and records
the version, all in one transaction.

The version is what keeps releases from undoing each other. During a deploy,
and after a rollback, older and newer instances run side by side, each with its
own copy of this directory. An instance whose version is not higher changes
nothing, so:

- an older release can never delete or roll back a newer release's skills;
- rolling the application back leaves the newer skills in place. To retire or
  revert a skill, ship a newer version without it or with the old content;
- a change made without increasing `VERSION` is not applied, and the loader
  logs a warning.

If any skill cannot be read, nothing is applied and its directory name is
logged. Version `0` means there is no catalogue. This file and any other
top-level file are ignored.

There are no shipped skills yet, and `VERSION` is `0`. Accounts cannot switch a
shipped skill off until the per-account setting in owner decision O2 exists, so
add that before adding the first one.
