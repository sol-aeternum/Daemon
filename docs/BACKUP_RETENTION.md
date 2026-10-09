# Backup rotation prerequisite (P0.4)

`scripts/backup_db.py` implements the local-directory expiry/inventory portion
of [ACCOUNT_DELETION_DESIGN.md](ACCOUNT_DELETION_DESIGN.md) §6.6. It does not
implement journal pruning, applied-marker extraction, restore replay or a
continuously running retention service. No production execution is authorized
by this change.

## Operator contract

- `BACKUP_DIR` retains its existing default of `backups`, relative to the
  invocation working directory. Operators should configure an absolute path.
  As before, a missing directory is created; new directories are private.
- The final directory must be a real directory owned by the invoking user,
  without group/world write access. Symlinked final directories are refused.
  Ancestors and the clock must be trusted. Stop legacy/uncooperative writers
  before using this script; POSIX advisory locks do not constrain them.
- One persistent `.backup.lock` serializes the whole operation. Never remove
  or replace that lock while any participant may be using the directory.
  All file mutations use the same opened directory descriptor.
- Only immediately contained, owned, single-link regular files named exactly
  `daemon_backup_YYYYMMDD_HHMMSS.dump`, with valid nonfuture UTC timestamps,
  are recognized. Files **strictly older** than 30 days expire. At exactly
  30 days a file remains. Filename timestamps, not filesystem modification
  times, determine age: renamed files or inaccurate clocks invalidate the
  operator's age assumptions. Existing dumps are not restore-validated here.
- Unknown entries, directories, invalid/future names, symlinks, hardlinks and
  interrupted temporary files remain untouched and make retention unverified.
  Recognized expired dumps still expire when other entries are ambiguous, a
  new dump fails, or `DATABASE_URL` is absent. This can remove the last old
  backup during an outage, as explicitly approved by the owner.

## Publication and failures

The previous `backup_inventory.json` is removed and the directory fsynced
before dump or expiry mutations. A previous inventory must pass a bounded
nofollow read and recognition of this script's v1 fields, types, dump records
and directory device/inode. Unrelated, corrupt, unsupported-version, oversized
(over 8 MiB), or wrong-directory inventories are preserved and stop the
invocation before expiry or dumping. Operators must reconcile these files;
the filename alone is not permission to delete them. Recognition is a format
check within the trusted-directory contract, not cryptographic provenance or
a freshness assertion. An invalid lock file also stops the invocation.
Dumps are written to an exclusive private temporary
file through `pg_dump` stdout; failure or empty output cannot publish a dump.
After success and file fsync, a no-overwrite hardlink publishes the final name,
the temporary link is removed, and the directory is fsynced. Same-second name
collisions fail rather than overwrite a retained dump. Temporary names never
match recognized dump names. Controlled failures remove only the invocation's
own temporary file; a crash can leave unknown files for operator investigation.

Expiry runs before and after dumping. A final scan records recognized retained
dump names, UTC times, sizes and the oldest recognized retained time. A private,
fsynced JSON file atomically replaces the inventory. Errors return a nonzero
exit status; an inventory, if available, records `retention_verified: false`.
Error codes contain no database URL or raw subprocess stderr.

The inventory is evidence about its recorded directory device/inode **at
`scanned_at` only**. Unknown files make its dump list incomplete, and a null
oldest time in an unverified inventory does not mean no backups exist. An old,
unreadable, missing or unverified inventory cannot establish current retention.
Readers must coordinate on the same lock, validate directory identity and
freshness, and require a successful verification; a file left after a storage
failure is not proof of success. Scheduling, filesystem qualification,
monitoring, other directories/hosts/replicas and restore drills remain operator
deployment prerequisites. A periodic CLI alone cannot guarantee a continuous
30-day deadline, especially when it cannot run or cannot delete a file.

`journal_applied_markers` is explicitly null and
`journal_pruning_permitted` is always false. Even a verified local expiry scan
does **not** authorize deleting any journal entry. Marker evidence from each
dump's own snapshot and the all-backup inventory remain later work.

## Deterministic coverage

`tests/test_backup_db.py` uses temporary directories and stubbed `pg_dump`, not
a real database. It covers cutoff boundaries, failed/empty/missing dumps,
unknown entries, symlinks/hardlinks, collisions, deletion/inventory failures,
interruption, directory replacement and a separate-process lock contender.
Design §8's old-dump and unavailable-inventory rows are covered only for this
expiry/inventory prerequisite; journal cleanup and replay outcomes are deferred.
