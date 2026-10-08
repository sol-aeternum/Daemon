import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useChatDraft } from '../hooks/useChatDraft';
import * as auth from '../lib/auth';
import {
  acceptSubmission,
  getChatDraft,
  heldSubmission,
  heldSubmissionComplete,
  heldSubmissionsReady,
  isHeldResend,
  latestHeldSubmission,
  promoteHeldSubmission,
  releaseSubmission,
  reloadChatDraftsForTests,
  restoreHeldSubmission,
} from '../lib/chatDrafts';
import {
  setAttachmentStoreForTests,
  type AttachmentStore,
  type PersistedAttachment,
} from '../lib/draftPersistence';

class MemoryAttachmentStore implements AttachmentStore {
  records = new Map<string, File>();
  gate: Promise<void> | null = null;
  async put({ id, file }: PersistedAttachment) {
    this.records.set(id, file);
  }
  async getMany(ids: string[]) {
    if (this.gate) await this.gate;
    return ids.flatMap((id) => {
      const file = this.records.get(id);
      return file ? [{ id, file }] : [];
    });
  }
  async delete(ids: string[]) {
    for (const id of ids) this.records.delete(id);
  }
  async clear() {
    this.records.clear();
  }
  async deleteSavedBefore() {}
}

function installStorage(name: 'localStorage' | 'sessionStorage'): void {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => void values.delete(key),
    setItem: (key, value) => void values.set(key, String(value)),
  };
  for (const target of [globalThis, window]) {
    Object.defineProperty(target, name, { configurable: true, value: storage });
  }
}

const flush = () => act(async () => new Promise((r) => setTimeout(r, 0)));
let store: MemoryAttachmentStore;

beforeEach(async () => {
  installStorage('sessionStorage');
  installStorage('localStorage');
  store = new MemoryAttachmentStore();
  setAttachmentStoreForTests(store);
  auth.clearLocalAuthState();
  await act(() => reloadChatDraftsForTests());
});
afterEach(() => {
  cleanup();
  setAttachmentStoreForTests(undefined);
});

function sendFrom(conversationId: string | null, key: string) {
  const { result } = renderHook(() => useChatDraft(conversationId));
  const file = new File(['bytes'], 'notes.txt', { type: 'text/plain' });
  const attachments = [{ id: 'att-1', file }];
  act(() => {
    result.current.setInput('Summarise the notes');
    result.current.setPendingAttachments(attachments);
  });
  act(() =>
    result.current.holdSubmission(key, 'Summarise the notes', attachments),
  );
  act(() => {
    result.current.setInput('');
    result.current.setPendingAttachments([]);
  });
  return { result, attachments };
}

describe('held submissions (#476: the key lives on the draft)', () => {
  it('match a resend only when text and attachments are unchanged', () => {
    const { attachments } = sendFrom('conv-a', 'key-1');
    const held = latestHeldSubmission('conv-a');
    expect(held?.key).toBe('key-1');
    expect(isHeldResend(held, 'Summarise the notes', attachments)).toBe(true);
    expect(isHeldResend(held, 'Summarise the notes!', attachments)).toBe(false);
    expect(isHeldResend(held, 'Summarise the notes', [])).toBe(false);
    expect(latestHeldSubmission('conv-b')).toBeUndefined();
  });

  it('go back to an empty composer, keeping the key, but never overwrite one', () => {
    sendFrom('conv-a', 'key-1');
    expect(restoreHeldSubmission('key-1')).toBe(true);
    const draft = getChatDraft('conv-a');
    expect(draft.input).toBe('Summarise the notes');
    expect(draft.pendingAttachments.map((a) => a.id)).toEqual(['att-1']);
    expect(heldSubmission('key-1')).toBeDefined(); // a resend reuses the key
    expect(restoreHeldSubmission('key-1')).toBe(false); // composer not empty
  });

  it('survive a reload with their attachments, under the same ids', async () => {
    sendFrom('conv-a', 'key-1');
    await flush();
    cleanup();
    await act(() => reloadChatDraftsForTests());
    await waitFor(() =>
      expect(heldSubmission('key-1')?.pendingAttachments).toHaveLength(1),
    );
    const held = heldSubmission('key-1');
    expect(held?.input).toBe('Summarise the notes');
    expect(held?.pendingAttachments[0].id).toBe('att-1');
  });

  it('are restored only once their files are back after a reload', async () => {
    sendFrom('conv-a', 'key-1');
    await flush();
    cleanup();
    let open!: () => void;
    store.gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    await act(() => reloadChatDraftsForTests());
    // Files still loading: restoring now would drop them.
    expect(restoreHeldSubmission('key-1')).toBe(false);
    open();
    await act(() => heldSubmissionsReady());
    expect(restoreHeldSubmission('key-1')).toBe(true);
    expect(getChatDraft('conv-a').pendingAttachments.map((a) => a.id)).toEqual([
      'att-1',
    ]);
  });

  it('cannot be replayed once a file is gone (too large to keep, or expired)', async () => {
    sendFrom('conv-a', 'key-1');
    await flush();
    cleanup();
    store.records.clear(); // the file expired (or was never persistable)
    await act(() => reloadChatDraftsForTests());
    await act(() => heldSubmissionsReady());
    expect(heldSubmission('key-1')?.input).toBe('Summarise the notes');
    expect(heldSubmissionComplete('key-1')).toBe(false);
    expect(restoreHeldSubmission('key-1')).toBe(false);
  });

  it('mark the restored draft with its key until the draft is edited', () => {
    sendFrom('conv-a', 'key-1');
    expect(restoreHeldSubmission('key-1')).toBe(true);
    const { result } = renderHook(() => useChatDraft('conv-a'));
    expect(result.current.restoredKey).toBe('key-1');
    act(() => result.current.setInput('Summarise the notes, briefly'));
    expect(result.current.restoredKey).toBeUndefined();
  });

  it('follow a new chat to its conversation and are released with the outcome', () => {
    sendFrom(null, 'key-1');
    promoteHeldSubmission('key-1', 'conv-new');
    expect(latestHeldSubmission('conv-new')?.key).toBe('key-1');
    releaseSubmission('key-1');
    expect(heldSubmission('key-1')).toBeUndefined();
  });

  it('empty the composer still holding them once the server has the task', () => {
    const { result } = renderHook(() => useChatDraft('conv-a'));
    const file = new File(['bytes'], 'notes.txt', { type: 'text/plain' });
    const attachments = [{ id: 'att-1', file }];
    act(() => {
      result.current.setInput('Summarise the notes');
      result.current.setPendingAttachments(attachments);
    });
    // Sent, and the stream is still running: the composer has not been
    // cleared yet when the task is accepted.
    act(() =>
      result.current.holdSubmission(
        'key-1',
        'Summarise the notes',
        attachments,
      ),
    );
    act(() => acceptSubmission('key-1'));
    expect(heldSubmission('key-1')).toBeUndefined();
    expect(result.current.input).toBe('');
    expect(result.current.pendingAttachments).toEqual([]);
  });

  it('keep a composer edited since, when the server has the task', () => {
    const { result } = renderHook(() => useChatDraft('conv-a'));
    act(() => result.current.setInput('Summarise the notes'));
    act(() =>
      result.current.holdSubmission('key-1', 'Summarise the notes', []),
    );
    act(() => result.current.setInput('Summarise the notes, briefly'));
    act(() => acceptSubmission('key-1'));
    expect(heldSubmission('key-1')).toBeUndefined();
    expect(result.current.input).toBe('Summarise the notes, briefly');
  });

  it('empty a composer a reload restored with them once the task is found', async () => {
    const { result } = renderHook(() => useChatDraft('conv-a'));
    const file = new File(['bytes'], 'notes.txt', { type: 'text/plain' });
    const attachments = [{ id: 'att-1', file }];
    act(() => {
      result.current.setInput('Summarise the notes');
      result.current.setPendingAttachments(attachments);
    });
    act(() =>
      result.current.holdSubmission(
        'key-1',
        'Summarise the notes',
        attachments,
      ),
    );
    await flush();
    cleanup();
    // Reloaded before the stream named its task: the draft comes back as
    // ordinary composer text, then reconciliation finds the running task.
    await act(() => reloadChatDraftsForTests());
    await act(() => heldSubmissionsReady());
    await waitFor(() =>
      expect(getChatDraft('conv-a').pendingAttachments).toHaveLength(1),
    );
    expect(getChatDraft('conv-a').input).toBe('Summarise the notes');
    act(() => acceptSubmission('key-1'));
    expect(getChatDraft('conv-a').input).toBe('');
    expect(getChatDraft('conv-a').pendingAttachments).toEqual([]);
  });

  it('are discarded with drafts when the sign-in changes', () => {
    sendFrom('conv-a', 'key-1');
    act(() => auth.clearLocalAuthState());
    expect(heldSubmission('key-1')).toBeUndefined();
  });
});
