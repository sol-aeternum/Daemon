'use client';

import { getAuthGeneration, subscribeAuthGeneration } from './auth';
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
// Tab-memory only: no credentials, serialized files, URLs or browser storage.
// Retain unfinished drafts until explicit reset or sign-in-lifetime invalidation;
// silently evicting an old conversation would discard user work.
const entries = new Map<string | null, DraftEntry>();
const listeners = new Set<() => void>();
let generation = getAuthGeneration();

function emit(): void {
  for (const listener of listeners) listener();
}

subscribeAuthGeneration(() => {
  generation = getAuthGeneration();
  // Revoke retained entry handles too; abandoned async closures must not keep
  // this store's references to discarded text/File objects alive.
  for (const entry of entries.values()) entry.snapshot = EMPTY;
  entries.clear();
  emit();
});

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
  scope.entry.snapshot = { ...previous, pendingAttachments };
  emit();
}

/** Explicit New Chat/discard: invalidate earlier async setters, even if empty. */
export function resetChatDraft(scope: ChatDraftScope): void {
  if (!isCurrent(scope)) return;
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
