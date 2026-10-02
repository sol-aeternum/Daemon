'use client';

import { getAuthGeneration, subscribeAuthGeneration } from './auth';
import {
  ATTACHMENT_TTL_MS,
  MAX_PERSISTED_FILE_BYTES,
  MAX_PERSISTED_TOTAL_BYTES,
  clearDraftText,
  getAttachmentStore,
  loadDraftText,
  saveDraftText,
  type PersistedDraft,
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
  const stale = [...persistedIds.keys()].filter((id) => !referenced.has(id));
  for (const id of stale) persistedIds.delete(id);
  if (store && stale.length > 0)
    void store.delete(stale).catch(() => undefined);
  saveDraftText(drafts);
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
  const wanted = [...new Set([...pendingRestores.values()].flat())];
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
  const store = getAttachmentStore();
  void store
    ?.deleteSavedBefore(Date.now() - ATTACHMENT_TTL_MS)
    .catch(() => undefined)
    .then(() =>
      pendingRestores.size > 0 ? restoreAttachments(generation) : undefined,
    );
  if (!store) pendingRestores.clear();
}

if (typeof window !== 'undefined') hydrate();

/** Test seam: simulate a page reload of this tab's module state. */
export function reloadChatDraftsForTests(): Promise<void> {
  entries.clear();
  pendingRestores.clear();
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
