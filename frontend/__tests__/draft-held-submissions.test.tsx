import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useChatDraft } from '../hooks/useChatDraft';
import * as auth from '../lib/auth';
import {
  acceptSubmission,
  getChatDraft,
  heldResendStatus,
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
  MAX_PERSISTED_FILE_BYTES,
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

  it("empty a new chat's composer when accepted before promotion", () => {
    const { result } = renderHook(() => useChatDraft(null));
    act(() => result.current.setInput('Plan the trip'));
    act(() => result.current.holdSubmission('key-1', 'Plan the trip', []));
    // Reconciliation after a reload: record (accept) first, then promote.
    act(() => acceptSubmission('key-1'));
    promoteHeldSubmission('key-1', 'conv-new');
    expect(getChatDraft(null).input).toBe('');
    expect(heldSubmission('key-1')).toBeUndefined();
  });

  it('keep a retyped identical draft when the old submission is accepted (identity, not text)', () => {
    const { result } = renderHook(() => useChatDraft('conv-a'));
    act(() => result.current.setInput('Summarise the notes'));
    act(() =>
      result.current.holdSubmission('key-1', 'Summarise the notes', []),
    );
    expect(result.current.restoredKey).toBe('key-1'); // the sent draft
    // The user clears the composer, then types the same words as a new draft.
    act(() => result.current.setInput(''));
    act(() => result.current.setInput('Summarise the notes'));
    expect(result.current.restoredKey).toBeUndefined();
    act(() => acceptSubmission('key-1'));
    expect(heldSubmission('key-1')).toBeUndefined();
    expect(result.current.input).toBe('Summarise the notes');
  });

  it('never overwrite a held submission with a different request under its key', () => {
    const { attachments } = sendFrom('conv-a', 'key-1');
    const { result } = renderHook(() => useChatDraft('conv-a'));
    act(() =>
      result.current.holdSubmission('key-1', 'Summarise the notes', []),
    );
    expect(heldSubmission('key-1')?.attachmentCount).toBe(1);
    expect(heldSubmission('key-1')?.pendingAttachments).toEqual(attachments);
  });

  it('allow a resend under the key only with every file, once loaded', async () => {
    const { attachments } = sendFrom('conv-a', 'key-1');
    expect(heldResendStatus('key-1', 'Summarise the notes', attachments)).toBe(
      'resend',
    );
    expect(heldResendStatus('key-1', 'Summarise the notes', [])).toBe(
      'incomplete',
    );
    expect(heldResendStatus('key-2', 'anything', [])).toBe('new');
    await flush();
    cleanup();
    let open!: () => void;
    store.gate = new Promise<void>((resolve) => {
      open = resolve;
    });
    await act(() => reloadChatDraftsForTests());
    expect(heldResendStatus('key-1', 'Summarise the notes', [])).toBe(
      'loading',
    );
    store.records.clear(); // the file expired meanwhile
    open();
    await act(() => heldSubmissionsReady());
    expect(heldResendStatus('key-1', 'Summarise the notes', [])).toBe(
      'incomplete',
    );
  });

  it("mark an earlier version's sent draft so acceptance clears it (#485 review)", async () => {
    // Written before drafts carried their held key: no heldMarks, no
    // restoredKey on the sent draft.
    sendFrom('conv-a', 'key-1');
    await flush();
    const saved = JSON.parse(sessionStorage.getItem('daemon:chat-drafts:v1')!);
    sessionStorage.setItem(
      'daemon:chat-drafts:v1',
      JSON.stringify({
        v: 1,
        epoch: saved.epoch,
        held: saved.held,
        drafts: [
          {
            conversationId: 'conv-a',
            input: 'Summarise the notes',
            attachmentIds: ['att-1'],
          },
          {
            conversationId: 'conv-b',
            input: 'Something else',
            attachmentIds: [],
          },
        ],
      }),
    );
    cleanup();
    await act(() => reloadChatDraftsForTests());
    await act(() => heldSubmissionsReady());
    expect(getChatDraft('conv-a').restoredKey).toBe('key-1');
    expect(getChatDraft('conv-b').restoredKey).toBeUndefined();
    act(() => acceptSubmission('key-1'));
    expect(getChatDraft('conv-a').input).toBe('');
    expect(getChatDraft('conv-b').input).toBe('Something else');
  });

  it('never mark a draft by content when this version saved it', async () => {
    const { result } = renderHook(() => useChatDraft('conv-a'));
    act(() => result.current.setInput('Same words'));
    act(() =>
      result.current.holdSubmission(
        'key-1',
        'Same words',
        result.current.pendingAttachments,
      ),
    );
    // Cleared, then the same words typed again as a new draft.
    act(() => result.current.setInput(''));
    act(() => result.current.setInput('Same words'));
    await flush();
    cleanup();
    await act(() => reloadChatDraftsForTests());
    expect(getChatDraft('conv-a').restoredKey).toBeUndefined();
  });

  it('are discarded with drafts when the sign-in changes', () => {
    sendFrom('conv-a', 'key-1');
    act(() => auth.clearLocalAuthState());
    expect(heldSubmission('key-1')).toBeUndefined();
  });
});

describe('held submissions under the persistence cap (#477)', () => {
  const TEXT_KEY = 'daemon:chat-drafts:v1';
  const bigFiles = () =>
    [1, 2, 3, 4].map((i) => ({
      id: `big-${i}`,
      file: fileOfSize(`big-${i}.bin`, MAX_PERSISTED_FILE_BYTES),
    }));

  function fileOfSize(name: string, size: number): File {
    const file = new File(['bytes'], name);
    Object.defineProperty(file, 'size', { value: size });
    return file;
  }

  it('free a released submission\u2019s capacity so a newer file is saved and readable after a reload', async () => {
    const { result } = renderHook(() => useChatDraft('conv-a'));
    const big = bigFiles();
    act(() => {
      result.current.setInput('Big request');
      result.current.setPendingAttachments(big);
    });
    act(() => result.current.holdSubmission('key-1', 'Big request', big));
    act(() => {
      result.current.setInput('');
      result.current.setPendingAttachments([]);
    });
    await flush();
    // Exactly four 25 MiB files fit the 100 MiB cap, and the readback
    // still dispatches the complete submission (#485 completeness).
    for (const { id } of big) expect(store.records.has(id)).toBe(true);
    const saved = JSON.parse(sessionStorage.getItem(TEXT_KEY)!);
    expect(saved.held).toEqual([
      {
        key: 'key-1',
        conversationId: 'conv-a',
        input: 'Big request',
        attachmentIds: ['big-1', 'big-2', 'big-3', 'big-4'],
        attachmentCount: 4,
      },
    ]);

    // The cap is full now: a newer tiny draft file is skipped, never saved.
    const fresh = renderHook(() => useChatDraft('conv-b'));
    const tiny = [{ id: 'tiny', file: new File(['tiny payload'], 'tiny.txt') }];
    act(() => {
      fresh.result.current.setInput('Small follow-up');
      fresh.result.current.setPendingAttachments(tiny);
    });
    await flush();
    expect(store.records.has('tiny')).toBe(false);

    // The outcome is known: the held submission is accepted and released.
    act(() => acceptSubmission('key-1'));
    expect(heldSubmission('key-1')).toBeUndefined();
    await waitFor(() => expect([...store.records.keys()]).toEqual(['tiny']));
    // The freed capacity was retried for the newer file in the same pass.
    expect(getChatDraft('conv-b').pendingAttachments.map((a) => a.id)).toEqual([
      'tiny',
    ]);

    // Simulated reload: the new file comes back from the store, readable.
    cleanup();
    await act(() => reloadChatDraftsForTests());
    await act(() => heldSubmissionsReady());
    const restored = renderHook(() => useChatDraft('conv-b'));
    await waitFor(() =>
      expect(
        restored.result.current.pendingAttachments.map((a) => a.id),
      ).toEqual(['tiny']),
    );
    const recovered = restored.result.current.pendingAttachments[0].file;
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(recovered);
    });
    expect(text).toBe('tiny payload');
    expect(getChatDraft('conv-b').input).toBe('Small follow-up');
    expect(heldSubmission('key-1')).toBeUndefined();
  });

  it('cannot replay a held submission that lost its one file to the cap (#485 completeness)', async () => {
    const { result } = renderHook(() => useChatDraft('conv-a'));
    const big = bigFiles();
    act(() => {
      result.current.setInput('Big request');
      result.current.setPendingAttachments(big);
    });
    act(() => result.current.holdSubmission('key-1', 'Big request', big));

    // A second held submission loses the single file it was sent with.
    const fresh = renderHook(() => useChatDraft('conv-b'));
    const tiny = [{ id: 'tiny', file: new File(['tiny payload'], 'tiny.txt') }];
    act(() => {
      fresh.result.current.setInput('Follow up');
      fresh.result.current.setPendingAttachments(tiny);
    });
    act(() => fresh.result.current.holdSubmission('key-2', 'Follow up', tiny));
    await flush();
    expect(store.records.has('tiny')).toBe(false);

    // Reload before either key is resolved: the big files come back, the
    // tiny one is simply gone.
    cleanup();
    await act(() => reloadChatDraftsForTests());
    await act(() => heldSubmissionsReady());
    expect(heldSubmission('key-1')?.pendingAttachments).toHaveLength(4);
    expect(heldSubmission('key-2')?.attachmentCount).toBe(1);
    expect(heldSubmission('key-2')?.pendingAttachments).toHaveLength(0);
    expect(heldSubmissionComplete('key-2')).toBe(false);
    expect(heldResendStatus('key-2', 'Follow up', [])).toBe('incomplete');

    // Releasing the big submission frees space but cannot resurrect a file
    // that was never saved; replaying would be a different request.
    act(() => releaseSubmission('key-1'));
    await flush();
    expect(heldSubmission('key-1')).toBeUndefined();
    expect(heldSubmissionComplete('key-2')).toBe(false);
    expect(heldResendStatus('key-2', 'Follow up', [])).toBe('incomplete');
  });
});
