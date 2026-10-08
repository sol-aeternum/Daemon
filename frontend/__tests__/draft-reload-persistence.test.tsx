import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatDraft } from '../hooks/useChatDraft';
import * as auth from '../lib/auth';
import { getChatDraft, reloadChatDraftsForTests } from '../lib/chatDrafts';
import {
  ATTACHMENT_TTL_MS,
  MAX_PERSISTED_FILE_BYTES,
  setAttachmentStoreForTests,
  type AttachmentStore,
  type PersistedAttachment,
} from '../lib/draftPersistence';

const TEXT_KEY = 'daemon:chat-drafts:v1';
const EPOCH_KEY = 'daemon:chat-drafts:auth-epoch';

class MemoryAttachmentStore implements AttachmentStore {
  records = new Map<string, { file: File; savedAt: number }>();
  gate: Promise<void> | null = null;
  async put({ id, file }: PersistedAttachment) {
    this.records.set(id, { file, savedAt: Date.now() });
  }
  async getMany(ids: string[]) {
    if (this.gate) await this.gate;
    return ids.flatMap((id) => {
      const record = this.records.get(id);
      return record ? [{ id, file: record.file }] : [];
    });
  }
  async delete(ids: string[]) {
    for (const id of ids) this.records.delete(id);
  }
  async clear() {
    this.records.clear();
  }
  async deleteSavedBefore(cutoff: number) {
    for (const [id, record] of this.records) {
      if (record.savedAt < cutoff) this.records.delete(id);
    }
  }
}

let store: MemoryAttachmentStore;

function installStorage(name: 'localStorage' | 'sessionStorage'): Storage {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, String(value));
    },
  };
  for (const target of [globalThis, window]) {
    Object.defineProperty(target, name, { configurable: true, value: storage });
  }
  return storage;
}

const flush = () => act(async () => new Promise((r) => setTimeout(r, 0)));

async function reload() {
  cleanup();
  await act(() => reloadChatDraftsForTests());
  await flush();
}

beforeEach(() => {
  installStorage('sessionStorage');
  installStorage('localStorage');
  store = new MemoryAttachmentStore();
  setAttachmentStoreForTests(store);
  auth.clearLocalAuthState();
  auth.setAccessToken('test-account', Date.now() + 120_000);
});

afterEach(() => {
  cleanup();
  setAttachmentStoreForTests(undefined);
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe('chat drafts survive a reload of the same tab', () => {
  it('restores unsent text per conversation, including the new-chat draft', async () => {
    const chat = renderHook(() => useChatDraft('chat'));
    act(() => chat.result.current.setInput('half-written question'));
    const fresh = renderHook(() => useChatDraft(null));
    act(() => fresh.result.current.setInput('new chat idea'));

    await reload();

    expect(renderHook(() => useChatDraft('chat')).result.current.input).toBe(
      'half-written question',
    );
    expect(renderHook(() => useChatDraft(null)).result.current.input).toBe(
      'new chat idea',
    );
  });

  it('restores attachment files with their name, type and contents', async () => {
    const hook = renderHook(() => useChatDraft('chat'));
    const file = new File(['hello world'], 'notes.txt', { type: 'text/plain' });
    act(() => hook.result.current.setPendingAttachments([{ id: 'a1', file }]));
    await flush();
    expect(store.records.has('a1')).toBe(true);

    await reload();

    const restored = renderHook(() => useChatDraft('chat'));
    await waitFor(() =>
      expect(restored.result.current.pendingAttachments).toHaveLength(1),
    );
    const [attachment] = restored.result.current.pendingAttachments;
    expect(attachment.id).toBe('a1');
    expect(attachment.file.name).toBe('notes.txt');
    expect(attachment.file.type).toBe('text/plain');
    const text = await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.readAsText(attachment.file);
    });
    expect(text).toBe('hello world');
  });

  it('keeps text typed before the attachment restore finishes', async () => {
    const hook = renderHook(() => useChatDraft('chat'));
    act(() => {
      hook.result.current.setInput('draft');
      hook.result.current.setPendingAttachments([
        { id: 'a1', file: new File(['x'], 'x.txt') },
      ]);
    });
    await flush();
    let open!: () => void;
    store.gate = new Promise((resolve) => (open = resolve));

    await reload();
    const restored = renderHook(() => useChatDraft('chat'));
    act(() => restored.result.current.setInput('draft, continued'));
    store.gate = null;
    await act(async () => open());
    await flush();

    expect(restored.result.current.input).toBe('draft, continued');
    expect(restored.result.current.pendingAttachments.map((a) => a.id)).toEqual(
      ['a1'],
    );
  });

  it('does not resurrect attachments the user removed before the restore finished', async () => {
    const hook = renderHook(() => useChatDraft('chat'));
    act(() =>
      hook.result.current.setPendingAttachments([
        { id: 'a1', file: new File(['x'], 'x.txt') },
      ]),
    );
    await flush();
    let open!: () => void;
    store.gate = new Promise((resolve) => (open = resolve));

    await reload();
    const restored = renderHook(() => useChatDraft('chat'));
    const replacement = new File(['y'], 'y.txt');
    act(() =>
      restored.result.current.setPendingAttachments([
        { id: 'b1', file: replacement },
      ]),
    );
    store.gate = null;
    await act(async () => open());
    await flush();

    expect(restored.result.current.pendingAttachments.map((a) => a.id)).toEqual(
      ['b1'],
    );
    expect(store.records.has('a1')).toBe(false);
  });

  it('removes the saved draft once it is submitted or explicitly reset', async () => {
    const hook = renderHook(() => useChatDraft('chat'));
    const attachments = [{ id: 'a1', file: new File(['x'], 'x.txt') }];
    act(() => {
      hook.result.current.setInput('send me');
      hook.result.current.setPendingAttachments(attachments);
    });
    await flush();
    act(() => hook.result.current.clearSubmittedDraft('send me', attachments));
    await flush();
    expect(sessionStorage.getItem(TEXT_KEY)).toBeNull();
    expect(store.records.size).toBe(0);

    act(() => hook.result.current.setInput('discard me'));
    act(() => hook.result.current.resetDraft());
    await reload();
    expect(getChatDraft('chat').input).toBe('');
  });

  it('keeps an oversized file in memory but does not copy it to storage', async () => {
    const hook = renderHook(() => useChatDraft('chat'));
    const big = new File(['x'], 'big.bin');
    Object.defineProperty(big, 'size', { value: MAX_PERSISTED_FILE_BYTES + 1 });
    act(() => {
      hook.result.current.setInput('with big file');
      hook.result.current.setPendingAttachments([{ id: 'big', file: big }]);
    });
    await flush();
    expect(hook.result.current.pendingAttachments).toHaveLength(1);
    expect(store.records.size).toBe(0);

    await reload();
    const restored = renderHook(() => useChatDraft('chat'));
    expect(restored.result.current.input).toBe('with big file');
    expect(restored.result.current.pendingAttachments).toHaveLength(0);
  });

  it('expires stored attachment files a day after they were last saved', async () => {
    const hook = renderHook(() => useChatDraft('chat'));
    act(() =>
      hook.result.current.setPendingAttachments([
        { id: 'old', file: new File(['x'], 'x.txt') },
      ]),
    );
    await flush();
    store.records.get('old')!.savedAt = Date.now() - ATTACHMENT_TTL_MS - 1;

    await reload();
    const restored = renderHook(() => useChatDraft('chat'));
    expect(restored.result.current.pendingAttachments).toHaveLength(0);
    expect(store.records.size).toBe(0);
  });

  it('saves only conversation keys, text and attachment IDs as text', () => {
    const hook = renderHook(() => useChatDraft('chat'));
    act(() => {
      hook.result.current.setInput('plain');
      hook.result.current.setPendingAttachments([
        { id: 'a1', file: new File(['secret contents'], 'private.txt') },
      ]);
    });
    const saved = JSON.parse(sessionStorage.getItem(TEXT_KEY)!);
    expect(Object.keys(saved).sort()).toEqual([
      'drafts',
      'epoch',
      'held',
      'heldMarks',
      'v',
    ]);
    expect(saved.drafts).toEqual([
      { conversationId: 'chat', input: 'plain', attachmentIds: ['a1'] },
    ]);
    expect(JSON.stringify(saved)).not.toContain('secret contents');
    expect(JSON.stringify(saved)).not.toContain('test-account');
  });

  it('ignores and removes malformed saved drafts', async () => {
    sessionStorage.setItem(
      TEXT_KEY,
      '{"v":1,"epoch":"x","drafts":[{"input":7}]}',
    );
    await reload();
    expect(getChatDraft('chat').input).toBe('');
    expect(sessionStorage.getItem(TEXT_KEY)).toBeNull();
  });
});

describe('a reload never restores another sign-in’s draft', () => {
  async function saveDraft() {
    const hook = renderHook(() => useChatDraft('chat'));
    act(() => {
      hook.result.current.setInput('private draft');
      hook.result.current.setPendingAttachments([
        { id: 'a1', file: new File(['x'], 'x.txt') },
      ]);
    });
    await flush();
  }

  it('sign-out discards saved text and files', async () => {
    await saveDraft();
    act(() => auth.clearLocalAuthState());
    await flush();
    expect(sessionStorage.getItem(TEXT_KEY)).toBeNull();
    expect(store.records.size).toBe(0);
    await reload();
    expect(getChatDraft('chat').input).toBe('');
  });

  it('a new sign-in discards drafts saved before it', async () => {
    await saveDraft();
    act(() => auth.setAccessToken('another-account', Date.now() + 120_000));
    await reload();
    expect(getChatDraft('chat').input).toBe('');
    expect(store.records.size).toBe(0);
  });

  it('discards a tab record saved before a sign-in in another tab', async () => {
    await saveDraft();
    const record = sessionStorage.getItem(TEXT_KEY);
    expect(record).not.toBeNull();
    // Another tab signed in/out: the shared epoch changed after this save.
    localStorage.setItem(EPOCH_KEY, 'rotated-elsewhere');
    await reload();
    expect(getChatDraft('chat').input).toBe('');
    expect(sessionStorage.getItem(TEXT_KEY)).toBeNull();
  });

  it('another tab’s token refresh still clears this tab’s draft and its saved copy', async () => {
    await saveDraft();
    const other = new BroadcastChannel('daemon-auth');
    other.postMessage({ type: 'refreshed', tabId: 'other-tab' });
    await waitFor(() => expect(getChatDraft('chat').input).toBe(''));
    other.close();
    await flush();
    expect(sessionStorage.getItem(TEXT_KEY)).toBeNull();
    expect(store.records.size).toBe(0);
  });

  it('own token refresh keeps the saved draft', async () => {
    await saveDraft();
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({ access_token: 'rotated', expires_in: 60 }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    );
    auth.setAccessToken('expired', 0);
    const hook = renderHook(() => useChatDraft('chat'));
    act(() => hook.result.current.setInput('private draft'));
    await act(async () => {
      await auth.refreshAccessToken();
    });
    await reload();
    expect(getChatDraft('chat').input).toBe('private draft');
  });
});
