'use client';

import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useSyncExternalStore,
} from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { getAuthGeneration, subscribeAuthGeneration } from '@/lib/auth';
import {
  clearChatDraftSubmission,
  clearSubmittedChatDraft,
  getChatDraft,
  holdSubmission,
  openChatDraft,
  resetChatDraft,
  setChatDraftAttachments,
  setChatDraftInput,
  subscribeChatDrafts,
  transferChatDraft,
  type DraftAttachment,
  type ChatDraftSubmission,
} from '@/lib/chatDrafts';

const getServerGeneration = () => 0;
const getServerDraft = () => getChatDraft(null, -1);

function createBinding(
  conversationId: string | null,
  generation: number,
  epoch: number,
) {
  const scope = openChatDraft(conversationId, generation);
  let active = false;
  const canWrite = () => active && scope.epoch === epoch;
  const setInput: Dispatch<SetStateAction<string>> = (action) => {
    if (canWrite()) setChatDraftInput(scope, action);
  };
  const setPendingAttachments: Dispatch<SetStateAction<DraftAttachment[]>> = (
    action,
  ) => {
    if (canWrite()) setChatDraftAttachments(scope, action);
  };
  return {
    activate: () => {
      active = true;
    },
    deactivate: () => {
      active = false;
    },
    setInput,
    setPendingAttachments,
    resetDraft: () => {
      if (canWrite()) resetChatDraft(scope);
    },
    clearSubmittedDraft: (input: string, attachments: DraftAttachment[]) => {
      if (canWrite()) clearSubmittedChatDraft(scope, input, attachments);
    },
    captureSubmission: (
      input: string,
      pendingAttachments: DraftAttachment[],
    ): ChatDraftSubmission => ({ scope, input, pendingAttachments }),
    clearSubmission: (submission: ChatDraftSubmission) => {
      if (canWrite()) clearChatDraftSubmission(scope, submission);
    },
    /** Hold a sent draft under its idempotency key until the outcome is known. */
    holdSubmission: (
      key: string,
      input: string,
      attachments: DraftAttachment[],
    ) => {
      if (canWrite()) holdSubmission(scope, key, input, attachments);
    },
    transferToConversation: (assignedId: string) =>
      canWrite() && transferChatDraft(scope, assignedId),
  };
}

/** Settings route unmounts retain data, but revoke that mount's write closures. */
export function useChatDraft(conversationId: string | null) {
  const generation = useSyncExternalStore(
    subscribeAuthGeneration,
    getAuthGeneration,
    getServerGeneration,
  );
  const getSnapshot = useCallback(
    () => getChatDraft(conversationId, generation),
    [conversationId, generation],
  );
  const draft = useSyncExternalStore(
    subscribeChatDrafts,
    getSnapshot,
    getServerDraft,
  );
  const binding = useMemo(
    () => createBinding(conversationId, generation, draft.epoch),
    [conversationId, generation, draft.epoch],
  );
  useLayoutEffect(() => {
    binding.activate();
    return binding.deactivate;
  }, [binding]);

  return {
    input: draft.input,
    pendingAttachments: draft.pendingAttachments,
    restoredKey: draft.restoredKey,
    setInput: binding.setInput,
    setPendingAttachments: binding.setPendingAttachments,
    resetDraft: binding.resetDraft,
    clearSubmittedDraft: binding.clearSubmittedDraft,
    captureSubmission: binding.captureSubmission,
    clearSubmission: binding.clearSubmission,
    holdSubmission: binding.holdSubmission,
    transferToConversation: binding.transferToConversation,
  };
}
