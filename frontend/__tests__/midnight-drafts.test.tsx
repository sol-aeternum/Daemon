import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode } from 'react';
import { useChatDraft } from '../hooks/useChatDraft';
import * as auth from '../lib/auth';
import {
  getChatDraft,
  openChatDraft,
  setChatDraftInput,
} from '../lib/chatDrafts';

beforeEach(() => {
  auth.clearLocalAuthState();
  auth.setAccessToken('test-account', Date.now() + 120_000);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('conversation drafts in tab memory', () => {
  it('never renders a prior-account draft under the new synchronous generation', () => {
    const renders: Array<{ generation: number; input: string }> = [];
    const hook = renderHook(() => {
      const draft = useChatDraft('chat');
      renders.push({
        generation: auth.getAuthGeneration(),
        input: draft.input,
      });
      return draft;
    });
    act(() => hook.result.current.setInput('prior-account private text'));
    const prior = auth.getAuthGeneration();
    act(() => auth.setAccessToken('new-account', Date.now() + 120_000));
    expect(
      renders.filter((render) => render.generation > prior).length,
    ).toBeGreaterThan(0);
    expect(
      renders
        .filter((render) => render.generation > prior)
        .every((render) => render.input === ''),
    ).toBe(true);
  });

  it('scrubs store entry references retained by revoked handles on logout', () => {
    const hook = renderHook(() => useChatDraft('chat'));
    act(() =>
      hook.result.current.setPendingAttachments([
        { id: 'x', file: new File(['private'], 'x.txt') },
      ]),
    );
    const handle = openChatDraft('chat', auth.getAuthGeneration());
    act(() => auth.clearLocalAuthState());
    expect(handle.entry.snapshot.input).toBe('');
    expect(handle.entry.snapshot.pendingAttachments).toEqual([]);
  });

  it('same-tab token refresh retains text, attachment arrays and File references', async () => {
    auth.setAccessToken('expired', 0);
    vi.stubGlobal('navigator', {
      locks: { request: async (_name: string, run: () => unknown) => run() },
    });
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ access_token: 'rotated' })),
        ),
    );
    const hook = renderHook(() => useChatDraft('chat'));
    const file = new File(['private'], 'private.txt');
    act(() => {
      hook.result.current.setInput('unfinished');
      hook.result.current.setPendingAttachments([{ id: 'x', file }]);
    });
    const attachments = hook.result.current.pendingAttachments;
    await act(async () => {
      expect((await auth.refreshAccessToken()).success).toBe(true);
    });
    expect(hook.result.current.input).toBe('unfinished');
    expect(hook.result.current.pendingAttachments).toBe(attachments);
    expect(hook.result.current.pendingAttachments[0].file).toBe(file);
  });

  it.each(['refreshed', 'cleared'] as const)(
    'remote %s discards drafts synchronously',
    (type) => {
      const hook = renderHook(() => useChatDraft('chat'));
      act(() => hook.result.current.setInput('private'));
      const oldSetter = hook.result.current.setInput;
      const channel = auth._getChannel()!;
      const spy = vi.spyOn(channel, 'addEventListener');
      const unsubscribe = auth.listenForAuthEvents(() => {});
      const handler = spy.mock.calls[0][1] as (event: MessageEvent) => void;
      act(() => {
        handler(
          new MessageEvent('message', { data: { type, tabId: 'other-tab' } }),
        );
        expect(getChatDraft('chat').input).toBe('');
        oldSetter('late old-account response');
      });
      expect(hook.result.current.input).toBe('');
      expect(auth.getAccessToken()).toBe(
        type === 'cleared' ? null : 'test-account',
      );
      unsubscribe();
    },
  );

  it('restores text and exact File references after settings-style route unmount/remount', () => {
    const file = new File(['private'], 'notes.txt', { type: 'text/plain' });
    const first = renderHook(() => useChatDraft('chat-a'));
    act(() => {
      first.result.current.setInput('unfinished');
      first.result.current.setPendingAttachments([{ id: 'attachment', file }]);
    });
    const attachments = first.result.current.pendingAttachments;
    const late = first.result.current.setInput;
    first.unmount();
    late('unmounted async result');
    const second = renderHook(() => useChatDraft('chat-a'));
    expect(second.result.current.input).toBe('unfinished');
    expect(second.result.current.pendingAttachments).toBe(attachments);
    expect(second.result.current.pendingAttachments[0].file).toBe(file);
  });

  it('separates conversations and rejects closures from a switched-away mount, including switch back', () => {
    const hook = renderHook(({ id }) => useChatDraft(id), {
      initialProps: { id: 'a' },
    });
    act(() => hook.result.current.setInput('draft a'));
    const oldSetter = hook.result.current.setInput;
    hook.rerender({ id: 'b' });
    expect(hook.result.current.input).toBe('');
    act(() => {
      hook.result.current.setInput('draft b');
      oldSetter('late a');
    });
    expect(hook.result.current.input).toBe('draft b');
    hook.rerender({ id: 'a' });
    act(() => oldSetter('resurrected'));
    expect(hook.result.current.input).toBe('draft a');
  });

  it.each(['logout', 'login', 'replacement'] as const)(
    'clears all drafts synchronously on %s and rejects late writes before React rerenders',
    (change) => {
      const hook = renderHook(() => useChatDraft('chat'));
      act(() => hook.result.current.setInput('private draft'));
      const late = hook.result.current.setInput;
      act(() => {
        if (change === 'logout') auth.clearAuthState();
        else auth.setAccessToken(change, Date.now() + 120_000);
        expect(getChatDraft('chat').input).toBe('');
        late('old-account completion');
        expect(getChatDraft('chat').input).toBe('');
      });
      expect(hook.result.current.input).toBe('');
      act(() => hook.result.current.setInput('new lifetime'));
      expect(hook.result.current.input).toBe('new lifetime');
    },
  );

  it('preserves functional setter semantics and conditionally clears each submitted field', () => {
    const hook = renderHook(() => useChatDraft('chat'));
    const file = new File(['x'], 'x.txt');
    act(() => {
      hook.result.current.setInput((previous) => `${previous}a`);
      hook.result.current.setInput((previous) => `${previous}b`);
      hook.result.current.setPendingAttachments((previous) => [
        ...previous,
        { id: 'x', file },
      ]);
    });
    expect(hook.result.current.input).toBe('ab');
    const submitted = hook.result.current;
    act(() => hook.result.current.setInput('newer'));
    act(() =>
      submitted.clearSubmittedDraft(
        submitted.input,
        submitted.pendingAttachments,
      ),
    );
    expect(hook.result.current.input).toBe('newer');
    expect(hook.result.current.pendingAttachments).toEqual([]);
    const next = hook.result.current;
    act(() => hook.result.current.setPendingAttachments([{ id: 'x', file }]));
    act(() => next.clearSubmittedDraft(next.input, next.pendingAttachments));
    expect(hook.result.current.input).toBe('');
    expect(hook.result.current.pendingAttachments[0].file).toBe(file);
  });

  it('explicit New Chat reset revokes outstanding closures without preventing fresh editing', () => {
    const hook = renderHook(() => useChatDraft(null));
    act(() => hook.result.current.setInput('old'));
    const old = hook.result.current;
    act(() => {
      old.resetDraft();
      old.setInput('late completion');
      old.setPendingAttachments([{ id: 'late', file: new File(['x'], 'x') }]);
    });
    expect(hook.result.current.input).toBe('');
    expect(hook.result.current.pendingAttachments).toEqual([]);
    act(() => hook.result.current.setInput('new'));
    expect(hook.result.current.input).toBe('new');
  });

  it('transfers null/new draft to its assigned ID without dropping text or File objects', () => {
    const hook = renderHook(
      ({ id }: { id: string | null }) => useChatDraft(id),
      { initialProps: { id: null as string | null } },
    );
    const file = new File(['x'], 'x.txt');
    act(() => {
      hook.result.current.setInput('next in-stream draft');
      hook.result.current.setPendingAttachments([{ id: 'x', file }]);
    });
    const oldSetter = hook.result.current.setInput;
    act(() =>
      expect(hook.result.current.transferToConversation('assigned')).toBe(true),
    );
    hook.rerender({ id: 'assigned' });
    act(() => oldSetter('late null draft'));
    expect(hook.result.current.input).toBe('next in-stream draft');
    expect(hook.result.current.pendingAttachments[0].file).toBe(file);
    expect(getChatDraft(null).input).toBe('');
  });

  it('does not overwrite another conversation draft or transfer an existing ID', () => {
    const existing = renderHook(() => useChatDraft('assigned'));
    act(() => existing.result.current.setInput('existing'));
    const fresh = renderHook(() => useChatDraft(null));
    act(() => fresh.result.current.setInput('new'));
    act(() =>
      expect(fresh.result.current.transferToConversation('assigned')).toBe(
        false,
      ),
    );
    act(() =>
      expect(existing.result.current.transferToConversation('other')).toBe(
        false,
      ),
    );
    expect(existing.result.current.input).toBe('existing');
    expect(fresh.result.current.input).toBe('new');
  });

  it('submission receipts follow ID promotion but preserve newer edits and reject another conversation/reset', () => {
    const hook = renderHook(
      ({ id }: { id: string | null }) => useChatDraft(id),
      {
        initialProps: { id: null as string | null },
      },
    );
    act(() => hook.result.current.setInput('submitted'));
    const receipt = hook.result.current.captureSubmission(
      hook.result.current.input,
      hook.result.current.pendingAttachments,
    );
    act(() => hook.result.current.transferToConversation('assigned'));
    hook.rerender({ id: 'assigned' });
    act(() => hook.result.current.clearSubmission(receipt));
    expect(hook.result.current.input).toBe('');
    act(() => hook.result.current.setInput('submitted'));
    const later = hook.result.current.captureSubmission(
      hook.result.current.input,
      hook.result.current.pendingAttachments,
    );
    act(() => hook.result.current.setInput('newer'));
    act(() => hook.result.current.clearSubmission(later));
    expect(hook.result.current.input).toBe('newer');
    hook.rerender({ id: 'another' });
    act(() => hook.result.current.setInput('submitted'));
    act(() => hook.result.current.clearSubmission(later));
    expect(hook.result.current.input).toBe('submitted');
    hook.rerender({ id: 'assigned' });
    act(() => hook.result.current.resetDraft());
    act(() => hook.result.current.setInput('submitted'));
    act(() => hook.result.current.clearSubmission(later));
    expect(hook.result.current.input).toBe('submitted');
  });

  it('does not silently discard unfinished drafts when other conversations open', () => {
    const generation = auth.getAuthGeneration();
    const oldest = openChatDraft('oldest', generation);
    setChatDraftInput(oldest, 'old');
    for (let index = 0; index < 40; index += 1) {
      setChatDraftInput(
        openChatDraft(`chat-${index}`, generation),
        String(index),
      );
    }
    expect(getChatDraft('oldest').input).toBe('old');
  });

  it('rechecks auth after functional updater execution', () => {
    const hook = renderHook(() => useChatDraft('chat'));
    act(() =>
      hook.result.current.setInput(() => {
        auth.clearLocalAuthState();
        return 'resurrect';
      }),
    );
    expect(hook.result.current.input).toBe('');
    expect(getChatDraft('chat').input).toBe('');
  });

  it('survives React StrictMode effect replay', () => {
    const hook = renderHook(() => useChatDraft('chat'), {
      wrapper: StrictMode,
    });
    act(() => hook.result.current.setInput('retained'));
    expect(hook.result.current.input).toBe('retained');
  });
});
