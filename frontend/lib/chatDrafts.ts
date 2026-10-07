'use client';

import { getAuthGeneration, subscribeAuthGeneration } from './auth';
import {
  ATTACHMENT_TTL_MS,
  MAX_PERSISTED_FILE_BYTES,
  MAX_PERSISTED_TOTAL_BYTES,
  clearDraftText,
  getAttachmentStore,
  loadDraftText,
  loadHeldSubmissions,
  saveDraftText,
  type PersistedDraft,
  type PersistedHeld,
} from './draftPersistence';
import type { SetStateAction } from 'react';

export interface DraftAttachment {
  id: string;
  file: File;
}

export interface ChatDraft {
  input: string;
  pendingAttachments: DraftAttachment[];
  /** Changes only on explicit discard; old setters cannot undo a reset. */
  epoch: number;
}

interface DraftEntry {
  snapshot: ChatDraft;
}

export interface ChatDraftScope {
  readonly generation: number;
  readonly conversationId: string | null;
  readonly epoch: number;
  readonly entry: DraftEntry;
}

/**
 * A sent draft held until its outcome is known. Its idempotency key belongs
 * to it: resending it unchanged (for example after a lost response, or after
 * a reload) reuses the key, so the backend replays the task it already
 * accepted instead of running it again. Kept like a draft (this tab's
 * session; attachments in the attachment store) and discarded with drafts.
 */
export interface HeldSubmission {
  readonly key: string;
  /** The conversation it belongs to (an unnamed new chat's is promoted). */
  conversationId: string | null;
  readonly input: string;
  pendingAttachments: DraftAttachment[];
}

export interface ChatDraftSubmission {
  readonly scope: ChatDraftScope;
  readonly input: string;
  readonly pendingAttachments: DraftAttachment[];
}

const EMPTY: ChatDraft = { input: '', pendingAttachments: [], epoch: 0 };
// The store of record is tab memory; draftPersistence mirrors it so a reload of
// this tab restores unsent text and attachments (never credentials or URLs).
// Retain unfinished drafts until explicit reset or sign-in-lifetime invalidation;
// silently evicting an old conversation would discard user work.
const entries = new Map<string | null, DraftEntry>();
const listeners = new Set<() => void>();
let generation = getAuthGeneration();
// Attachment IDs this tab has written to the attachment store.
const persistedIds = new Map<string, number>();
// Hydrated entries still waiting for their files, with the IDs to restore. Any
// attachment change by the user cancels the restore for that entry.
const pendingRestores = new Map<DraftEntry, string[]>();
// Sent drafts awaiting their outcome, by idempotency key (insertion order).
const held = new Map<string, HeldSubmission>();
// Hydrated held submissions still waiting for their files.
const heldRestores = new Map<string, string[]>();
// Settles once held submissions' files are back after a reload (or at once).
let heldHydrated: Promise<void> = Promise.resolve();

function emit(): void {
  persist();
  for (const listener of listeners) listener();
}

function persist(): void {
  if (typeof window === 'undefined') return;
  const store = getAttachmentStore();
  const referenced = new Set<string>();
  const drafts: PersistedDraft[] = [];
  let total = 0;
  for (const bytes of persistedIds.values()) total += bytes;
  for (const [conversationId, entry] of entries) {
    const { input, pendingAttachments } = entry.snapshot;
    const attachmentIds = [...(pendingRestores.get(entry) ?? [])];
    for (const { id, file } of pendingAttachments) {
      if (store && !persistedIds.has(id)) {
        if (
          file.size > MAX_PERSISTED_FILE_BYTES ||
          total + file.size > MAX_PERSISTED_TOTAL_BYTES
        )
          continue;
        persistedIds.set(id, file.size);
        total += file.size;
        void store.put({ id, file }).catch(() => persistedIds.delete(id));
      }
      if (persistedIds.has(id)) attachmentIds.push(id);
    }
    for (const id of attachmentIds) referenced.add(id);
    if (input || attachmentIds.length > 0) {
      drafts.push({ conversationId, input, attachmentIds });
    }
  }
  const heldRecords: PersistedHeld[] = [];
  for (const submission of held.values()) {
    const attachmentIds = [...(heldRestores.get(submission.key) ?? [])];
    for (const { id, file } of submission.pendingAttachments) {
      if (store && !persistedIds.has(id)) {
        if (
          file.size > MAX_PERSISTED_FILE_BYTES ||
          total + file.size > MAX_PERSISTED_TOTAL_BYTES
        )
          continue;
        persistedIds.set(id, file.size);
        total += file.size;
        void store.put({ id, file }).catch(() => persistedIds.delete(id));
      }
      if (persistedIds.has(id)) attachmentIds.push(id);
    }
    for (const id of attachmentIds) referenced.add(id);
    heldRecords.push({
      key: submission.key,
      conversationId: submission.conversationId,
      input: submission.input,
      attachmentIds,
    });
  }
  const stale = [...persistedIds.keys()].filter((id) => !referenced.has(id));
  for (const id of stale) persistedIds.delete(id);
  if (store && stale.length > 0)
    void store.delete(stale).catch(() => undefined);
  saveDraftText(drafts, heldRecords);
}

function cancelRestore(entry: DraftEntry): void {
  pendingRestores.delete(entry);
}

subscribeAuthGeneration(() => {
  generation = getAuthGeneration();
  // Revoke retained entry handles too; abandoned async closures must not keep
  // this store's references to discarded text/File objects alive.
  for (const entry of entries.values()) entry.snapshot = EMPTY;
  entries.clear();
  pendingRestores.clear();
  held.clear();
  heldRestores.clear();
  const ids = [...persistedIds.keys()];
  persistedIds.clear();
  clearDraftText();
  if (ids.length > 0) {
    void getAttachmentStore()
      ?.delete(ids)
      .catch(() => undefined);
  }
  emit();
});

async function restoreAttachments(expectedGeneration: number): Promise<void> {
  const store = getAttachmentStore();
  if (!store) {
    pendingRestores.clear();
    return;
  }
  const wanted = [
    ...new Set([
      ...[...pendingRestores.values()].flat(),
      ...[...heldRestores.values()].flat(),
    ]),
  ];
  let found: Map<string, File>;
  try {
    found = new Map(
      (await store.getMany(wanted)).map(({ id, file }) => [id, file]),
    );
  } catch {
    found = new Map();
  }
  if (expectedGeneration !== generation) return;
  for (const [entry, ids] of [...pendingRestores]) {
    pendingRestores.delete(entry);
    const present = new Set(entry.snapshot.pendingAttachments.map((a) => a.id));
    const restored: DraftAttachment[] = [];
    for (const id of ids) {
      const file = found.get(id);
      if (!file || present.has(id)) continue;
      persistedIds.set(id, file.size);
      restored.push({ id, file });
      // Rewrite to restart the expiry clock: the draft is still in use.
      void store.put({ id, file }).catch(() => undefined);
    }
    if (restored.length > 0) {
      entry.snapshot = {
        ...entry.snapshot,
        pendingAttachments: [...entry.snapshot.pendingAttachments, ...restored],
      };
    }
  }
  for (const [key, ids] of [...heldRestores]) {
    heldRestores.delete(key);
    const submission = held.get(key);
    if (!submission) continue;
    for (const id of ids) {
      const file = found.get(id);
      if (!file) continue;
      persistedIds.set(id, file.size);
      submission.pendingAttachments.push({ id, file });
      void store.put({ id, file }).catch(() => undefined);
    }
  }
  emit();
}

function hydrate(): void {
  const saved = loadDraftText();
  for (const draft of saved) {
    const entry: DraftEntry = {
      snapshot: { input: draft.input, pendingAttachments: [], epoch: 0 },
    };
    entries.set(draft.conversationId, entry);
    if (draft.attachmentIds.length > 0) {
      pendingRestores.set(entry, draft.attachmentIds);
      // Owned by this tab: a cancelled restore must delete them, not orphan them.
      for (const id of draft.attachmentIds) persistedIds.set(id, 0);
    }
  }
  for (const record of loadHeldSubmissions()) {
    held.set(record.key, {
      key: record.key,
      conversationId: record.conversationId,
      input: record.input,
      pendingAttachments: [],
    });
    if (record.attachmentIds.length > 0) {
      heldRestores.set(record.key, record.attachmentIds);
      for (const id of record.attachmentIds) persistedIds.set(id, 0);
    }
  }
  const store = getAttachmentStore();
  heldHydrated = store
    ? store
        .deleteSavedBefore(Date.now() - ATTACHMENT_TTL_MS)
        .catch(() => undefined)
        .then(() =>
          pendingRestores.size > 0 || heldRestores.size > 0
            ? restoreAttachments(generation)
            : undefined,
        )
        .catch(() => undefined)
    : Promise.resolve();
  if (!store) {
    pendingRestores.clear();
    heldRestores.clear();
  }
}

if (typeof window !== 'undefined') hydrate();

/** Test seam: simulate a page reload of this tab's module state. */
export function reloadChatDraftsForTests(): Promise<void> {
  entries.clear();
  pendingRestores.clear();
  held.clear();
  heldRestores.clear();
  persistedIds.clear();
  generation = getAuthGeneration();
  hydrate();
  return Promise.resolve();
}

export function subscribeChatDrafts(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getChatDraft(
  conversationId: string | null,
  expectedGeneration = getAuthGeneration(),
): ChatDraft {
  if (
    expectedGeneration !== getAuthGeneration() ||
    generation !== expectedGeneration
  )
    return EMPTY;
  return entries.get(conversationId)?.snapshot ?? EMPTY;
}

/** Open once per mounted scope (not on every edit). Revoked scopes cannot write. */
export function openChatDraft(
  conversationId: string | null,
  expectedGeneration: number,
): ChatDraftScope {
  let entry = entries.get(conversationId);
  if (
    expectedGeneration === generation &&
    expectedGeneration === getAuthGeneration()
  ) {
    if (entry) entries.delete(conversationId);
    else entry = { snapshot: EMPTY };
    entries.set(conversationId, entry);
  }
  entry ??= { snapshot: EMPTY };
  return {
    generation: expectedGeneration,
    conversationId,
    epoch: entry.snapshot.epoch,
    entry,
  };
}

function isCurrent(scope: ChatDraftScope): boolean {
  return (
    scope.generation === getAuthGeneration() &&
    entries.get(scope.conversationId) === scope.entry &&
    scope.entry.snapshot.epoch === scope.epoch
  );
}

function apply<T>(action: SetStateAction<T>, previous: T): T {
  return typeof action === 'function'
    ? (action as (value: T) => T)(previous)
    : action;
}

export function setChatDraftInput(
  scope: ChatDraftScope,
  action: SetStateAction<string>,
): void {
  if (!isCurrent(scope)) return;
  const previous = scope.entry.snapshot;
  const input = apply(action, previous.input);
  // An updater may itself change auth/reset the draft. Recheck before writing.
  if (!isCurrent(scope) || scope.entry.snapshot !== previous) return;
  if (input === previous.input) return;
  scope.entry.snapshot = { ...previous, input };
  emit();
}

export function setChatDraftAttachments(
  scope: ChatDraftScope,
  action: SetStateAction<DraftAttachment[]>,
): void {
  if (!isCurrent(scope)) return;
  const previous = scope.entry.snapshot;
  const pendingAttachments = apply(action, previous.pendingAttachments);
  if (!isCurrent(scope) || scope.entry.snapshot !== previous) return;
  if (pendingAttachments === previous.pendingAttachments) return;
  cancelRestore(scope.entry);
  scope.entry.snapshot = { ...previous, pendingAttachments };
  emit();
}

/** Explicit New Chat/discard: invalidate earlier async setters, even if empty. */
export function resetChatDraft(scope: ChatDraftScope): void {
  if (!isCurrent(scope)) return;
  cancelRestore(scope.entry);
  scope.entry.snapshot = { ...EMPTY, epoch: scope.epoch + 1 };
  emit();
}

/** Clear each submitted field only if it still matches; preserve newer edits. */
export function clearSubmittedChatDraft(
  scope: ChatDraftScope,
  input: string,
  pendingAttachments: DraftAttachment[],
): void {
  if (!isCurrent(scope)) return;
  const previous = scope.entry.snapshot;
  if (previous.pendingAttachments === pendingAttachments) {
    cancelRestore(scope.entry);
  }
  scope.entry.snapshot = {
    ...previous,
    input: previous.input === input ? '' : previous.input,
    pendingAttachments:
      previous.pendingAttachments === pendingAttachments
        ? []
        : previous.pendingAttachments,
  };
  emit();
}

/** A receipt can follow an explicit ID transfer, but not a reset/account switch. */
export function clearChatDraftSubmission(
  currentScope: ChatDraftScope,
  submission: ChatDraftSubmission,
): void {
  if (
    submission.scope.generation !== currentScope.generation ||
    submission.scope.entry !== currentScope.entry ||
    submission.scope.epoch !== currentScope.epoch
  )
    return;
  clearSubmittedChatDraft(
    currentScope,
    submission.input,
    submission.pendingAttachments,
  );
}

/**
 * Call synchronously BEFORE publishing the server-assigned conversation ID.
 * Only null/new -> assigned is supported; never overwrite an existing draft.
 * The new ID must then be passed to useChatDraft. Old null-scope writes stop.
 */
export function transferChatDraft(
  scope: ChatDraftScope,
  assignedId: string,
): boolean {
  if (!isCurrent(scope) || scope.conversationId !== null || !assignedId)
    return false;
  if (entries.has(assignedId)) return false;
  entries.delete(null);
  entries.set(assignedId, scope.entry);
  emit();
  return true;
}

/**
 * Hold a sent draft under ``key`` until its outcome is known. A resend of the
 * same draft (unchanged text and attachments) then reuses that key.
 */
export function holdSubmission(
  scope: ChatDraftScope,
  key: string,
  input: string,
  pendingAttachments: DraftAttachment[],
): void {
  if (
    scope.generation !== getAuthGeneration() ||
    scope.generation !== generation
  )
    return;
  held.delete(key); // re-insert as the newest
  held.set(key, {
    key,
    conversationId: scope.conversationId,
    input,
    pendingAttachments: [...pendingAttachments],
  });
  emit();
}

/** The newest held submission of ``conversationId``, if any. */
export function latestHeldSubmission(
  conversationId: string | null,
): HeldSubmission | undefined {
  let latest: HeldSubmission | undefined;
  for (const submission of held.values()) {
    if (submission.conversationId === conversationId) latest = submission;
  }
  return latest;
}

export function heldSubmission(key: string): HeldSubmission | undefined {
  return held.get(key);
}

/** Whether ``input``/``attachments`` are exactly the held submission (a resend). */
export function isHeldResend(
  submission: HeldSubmission | undefined,
  input: string,
  pendingAttachments: DraftAttachment[],
): submission is HeldSubmission {
  if (!submission || submission.input !== input) return false;
  const ids = (list: DraftAttachment[]) => list.map((a) => a.id).join('\u0000');
  return ids(submission.pendingAttachments) === ids(pendingAttachments);
}

/** The outcome is known: forget the held submission (and, unless used, its files). */
export function releaseSubmission(key: string): void {
  if (!held.delete(key)) return;
  heldRestores.delete(key);
  emit();
}

/** A new chat's held submission now belongs to the named conversation. */
export function promoteHeldSubmission(
  key: string,
  conversationId: string,
): void {
  const submission = held.get(key);
  if (!submission || submission.conversationId === conversationId) return;
  submission.conversationId = conversationId;
  emit();
}

/** Resolves once held submissions' files are back after a reload. */
export function heldSubmissionsReady(): Promise<void> {
  return heldHydrated;
}

/**
 * Put a held submission back in its conversation's composer, if that
 * composer is empty, so sending it again reuses its key. The submission stays
 * held. Returns whether it was restored. Await ``heldSubmissionsReady`` first
 * after a reload: a submission whose files are still loading is not restored.
 */
export function restoreHeldSubmission(key: string): boolean {
  const submission = held.get(key);
  if (!submission || heldRestores.has(key)) return false;
  let entry = entries.get(submission.conversationId);
  if (entry) {
    const { input, pendingAttachments } = entry.snapshot;
    if (input || pendingAttachments.length > 0 || pendingRestores.has(entry))
      return false;
  } else {
    entry = { snapshot: EMPTY };
    entries.set(submission.conversationId, entry);
  }
  entry.snapshot = {
    ...entry.snapshot,
    input: submission.input,
    pendingAttachments: [...submission.pendingAttachments],
  };
  emit();
  return true;
}
