'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useAuth } from '@/components/AuthProvider';
import { useAuthGeneration } from '@/hooks/useAuthGeneration';
import { getAuthGeneration } from '@/lib/auth';

const PROMPT = 'Do a deep dive on X.';
const FOLLOWUP = 'How’s that going?';
const COPY = {
  empty: 'No research request yet.',
  queued: 'Last saved as queued.',
  running: 'Last saved as running.',
  completed: 'Your report is ready.',
  cancelled: 'The request was cancelled.',
} as const;
type Snapshot =
  | { state: Exclude<keyof typeof COPY, 'completed'> }
  | { state: 'completed'; conversation_id: string };

function parseSnapshot(value: unknown): Snapshot {
  if (!value || typeof value !== 'object' || !('state' in value))
    throw Error('Invalid inbox state');
  const { state } = value;
  if (
    state === 'empty' ||
    state === 'queued' ||
    state === 'running' ||
    state === 'cancelled'
  ) {
    return { state };
  }
  if (
    state === 'completed' &&
    'conversation_id' in value &&
    typeof value.conversation_id === 'string' &&
    /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value.conversation_id)
  ) {
    return { state, conversation_id: value.conversation_id };
  }
  throw Error('Invalid inbox state');
}

async function fetchSnapshot(
  endpoint: string,
  authorization: string,
  signal: AbortSignal,
  key?: string,
) {
  const response = await fetch(endpoint, {
    method: key ? 'POST' : 'GET',
    headers: key
      ? {
          Authorization: authorization,
          'Content-Type': 'application/json',
          'Idempotency-Key': key,
        }
      : { Authorization: authorization },
    ...(key ? { body: JSON.stringify({ text: PROMPT }) } : {}),
    cache: 'no-store',
    credentials: 'omit',
    redirect: 'error',
    signal,
  });
  if (!response.ok) throw Error('Unverified inbox response');
  const snapshot = parseSnapshot(await response.json());
  if (key && snapshot.state === 'empty') throw Error('Unverified acceptance');
  return snapshot;
}

export function InboxPilot({ endpoint }: { endpoint: string }) {
  const generation = useAuthGeneration();
  const { isAuthenticated, authHeader } = useAuth();
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-lg flex-col gap-4 px-4 py-6">
      <h1 className="text-xl font-semibold">Inbox</h1>
      {isAuthenticated && authHeader ? (
        <InboxSession
          key={generation}
          endpoint={endpoint}
          generation={generation}
          authorization={authHeader}
        />
      ) : (
        <p>Sign in to use the inbox.</p>
      )}
    </main>
  );
}

function InboxSession({
  endpoint,
  generation,
  authorization,
}: {
  endpoint: string;
  generation: number;
  authorization: string;
}) {
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<
    | { kind: 'checking' | 'unavailable' }
    | { kind: 'saved'; snapshot: Snapshot; acknowledged: boolean }
  >({ kind: 'checking' });
  const [canAsk, setCanAsk] = useState(false);
  const [hint, setHint] = useState('');
  const work = useRef<AbortController | null>(null);
  const startKey = useRef<string | null>(null);

  const request = useCallback(
    (key?: string) => {
      if (work.current || getAuthGeneration() !== generation) return;
      const controller = new AbortController();
      work.current = controller;
      const deadline = setTimeout(() => controller.abort(), 12_000);
      const ownsView = () =>
        work.current === controller && getAuthGeneration() === generation;
      void fetchSnapshot(endpoint, authorization, controller.signal, key)
        .then(
          (snapshot) => {
            if (!ownsView()) return;
            if (controller.signal.aborted) {
              setStatus({ kind: 'unavailable' });
              return;
            }
            setCanAsk(true);
            setStatus({
              kind: 'saved',
              snapshot,
              acknowledged: key !== undefined,
            });
          },
          () => {
            if (ownsView()) setStatus({ kind: 'unavailable' });
          },
        )
        .finally(() => {
          clearTimeout(deadline);
          if (work.current === controller) work.current = null;
        });
    },
    [authorization, endpoint, generation],
  );

  useEffect(() => {
    request();
    return () => {
      const controller = work.current;
      work.current = null;
      controller?.abort();
    };
  }, [request]);

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (work.current || getAuthGeneration() !== generation) return;
    const text = input.trim().replaceAll("'", '’');
    const starts = text === PROMPT;
    if (starts) {
      if (
        startKey.current !== null ||
        status.kind !== 'saved' ||
        status.snapshot.state !== 'empty'
      ) {
        setHint(`Ask “${FOLLOWUP}” to check the saved request.`);
        return;
      }
      // Keep the key before fetch. An uncertain response never generates a
      // replacement or auto-resends; the next supported message reads only.
      startKey.current = crypto.randomUUID();
    } else if (text !== FOLLOWUP) {
      setHint(`Try “${PROMPT}” or “${FOLLOWUP}”`);
      return;
    }
    setInput('');
    setHint('');
    setStatus({ kind: 'checking' });
    request(starts ? (startKey.current ?? undefined) : undefined);
  }

  const saved = status.kind === 'saved' ? status.snapshot : null;
  return (
    <>
      <div
        role="status"
        className="min-h-20 rounded-lg border border-[var(--color-border)] p-4"
      >
        {status.kind === 'checking' && <p>Checking saved status…</p>}
        {status.kind === 'unavailable' && <p>Status unavailable</p>}
        {status.kind === 'saved' && (
          <p>
            {status.acknowledged ? 'Accepted.' : COPY[status.snapshot.state]}
          </p>
        )}
        {saved?.state === 'completed' && (
          <Link
            href={`/?id=${saved.conversation_id}`}
            prefetch={false}
            className="inline-flex min-h-11 items-center underline"
          >
            Open report
          </Link>
        )}
      </div>
      {canAsk && (
        <form className="flex min-w-0 flex-col gap-2" onSubmit={send}>
          <label className="sr-only" htmlFor="inbox-pilot-message">
            Message Daemon
          </label>
          <div className="flex min-w-0 gap-2">
            <input
              id="inbox-pilot-message"
              className="min-h-11 min-w-0 flex-1 rounded-md border border-[var(--color-border)] bg-transparent px-3"
              placeholder={saved?.state === 'empty' ? PROMPT : FOLLOWUP}
              maxLength={128}
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
            <button
              type="submit"
              aria-label="Send message"
              disabled={status.kind === 'checking' || !input.trim()}
              className="min-h-11 shrink-0 rounded-md border border-[var(--color-border)] px-4 disabled:opacity-50"
            >
              Send
            </button>
          </div>
          {hint && <p className="text-sm">{hint}</p>}
        </form>
      )}
    </>
  );
}
