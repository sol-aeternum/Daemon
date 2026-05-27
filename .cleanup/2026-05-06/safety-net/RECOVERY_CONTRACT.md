# T7 Safety Net — Recovery Contract (QA Repair v2)
# Plan: git-state-cleanup-post-parity-ship
# Created: 2026-05-08
# QA Repair v2: 2026-05-08 (fixed GIT_MASTER=1 prefix, stash wording, .cleanup exclusion)

## Contract Overview

This document specifies the recovery procedure to restore the T3 dirty state (436 entries =
45 modified tracked + 391 untracked at T3 capture time) after any bucket disposition operations.

**This contract is HONEST about git limitations:**
- `git apply` can restore tracked file modifications
- `git` alone CANNOT restore untracked files — they must be manually copied from archive

---

## Recovery Procedure

### Phase A: Restore Modified Tracked Files

**Command:**
```bash
cd /home/sol/daemon
GIT_MASTER=1 git apply --binary .cleanup/2026-05-06/safety-net/tracked_modifications.diff
```

**Effect:** Restores all 45 modified tracked files to their dirty state.

**Verification:**
```bash
GIT_MASTER=1 git diff --name-only | wc -l  # should output: 45
GIT_MASTER=1 git diff --name-only | diff - .cleanup/2026-05-06/safety-net/modified_tracked_files.txt | wc -l  # should output: 0
```

---

### Phase B: Restore Untracked Files (MANUAL — no single git command)

**CANONICAL METHOD — Per-file restore using manifest:**
```bash
cd /home/sol/daemon

# Restore original T3 untracked files (excluding T7 safety-net artifacts only)
while IFS= read -r path; do
  # Skip only safety-net-generated paths, NOT original cleanup artifacts
  case "$path" in
    .cleanup/2026-05-06/safety-net/*) continue ;;
  esac
  dir="$(dirname "$path")"
  mkdir -p "$dir"
  cp -p ".cleanup/2026-05-06/safety-net/untracked_archive/$path" "$path"
done < .cleanup/2026-05-06/safety-net/untracked_files.txt
```

**Effect:** Restores all 391 original T3 untracked files to their original locations,
including `.cleanup/2026-05-06/cleanup_ledger.md` if it was in the T3 inventory.
Skips only the T7 safety-net artifact paths under `.cleanup/2026-05-06/safety-net/`.

**Verification:**
```bash
GIT_MASTER=1 git status --short --untracked-files=all | grep '^??' | grep -v '.cleanup/2026-05-06/safety-net' | wc -l
```

---

## Multi-Step Recovery Contract (Honest)

Because `git` provides no native mechanism to restore untracked files, the complete
recovery requires BOTH:

1. `GIT_MASTER=1 git apply --binary tracked_modifications.diff` — for 45 modified tracked files
2. Manifest-driven file copy from `untracked_archive/` — for 391 original untracked files

**There is NO single user-facing `git` command that can restore both.**

---

## Artifact Inventory

| Artifact | Purpose | Size |
|---------|---------|------|
| `tracked_modifications.diff` | Binary git patch for all 45 modified tracked files | ~626 KB |
| `untracked_archive/` | Directory tree containing copies of all 392 untracked files (supersets T3) | varies |
| `modified_tracked_files.txt` | Manifest: one path per line for 45 modified tracked files | 45 lines |
| `untracked_files.txt` | Manifest: one path per line for 392 untracked files (at T7 creation) | 392 lines |

---

## Honest Post-T7 Working Tree State

**After safety-net creation, the working tree contains:**

| Category | Count | Notes |
|----------|-------|-------|
| Modified tracked (T3 dirty) | 45 | Unchanged |
| Untracked: original T3 files | 391 | Preserved in archive |
| Untracked: safety-net artifacts | ~396 | Archive directory + copies + metadata |
| **Total untracked** | **~787** | Increased due to in-repo archive |
| **Total dirty** | **~832** | 45 modified + 787 untracked |

**The statement "working tree unchanged" is FALSE.** The safety-net creation added
~396 new untracked entries (the archive and its contents) to the working tree.
However, no git refs, branches, or tags were modified.

---

## Git Ref State (Unchanged)

| Ref | SHA | Type |
|-----|-----|------|
| HEAD | d4e063fa | commit (detached) |
| harness-parity-followups | d4e063fa | branch (created at T5) |
| main | 290b7c02 | branch |
| harness-parity-shipped | d4e063fa | tag (protected) |
| pre-wave-1 | fdf97a75 | tag (protected) |

**Total refs: 5 items** (HEAD pseudo-ref + 2 branches + 2 tags)

---

## T3 Coverage

| T3 Category | T3 Count | T7 Coverage | Status |
|-------------|----------|------------|--------|
| Modified tracked | 45 | 45 in diff | ✅ COVERED |
| Untracked | 391 | 391 in archive (392 total) | ✅ COVERED |
| **Total** | **436** | **436+** | ✅ **FULL COVERAGE** |

---

## Safety Net Properties (Corrected)

1. **Non-mutating refs:** Creating the safety net did not alter any git refs, branches, tags, or HEAD.

2. **Complete coverage:** Every file in the T3 dirty inventory (436 paths) is represented either in the diff (for modified tracked) or the archive (for untracked).

3. **Verifiable:** The manifests provide exact path counts that can be verified before and after any disposition operations.

4. **Working tree changed:** The safety-net creation added in-repo artifacts (~396 untracked entries). This is expected and documented honestly above.

5. **Isolated:** The safety-net directory is itself untracked, so it will not interfere with any git operations on the main working tree.

---

## Reversion Trigger

If any bucket disposition operation (T8-T12) results in unintended data loss or
incomplete recovery, invoke the recovery contract immediately before proceeding.

**This safety net does not expire. It remains valid until explicitly superseded
by a new T7 capture after additional working-tree changes.**
