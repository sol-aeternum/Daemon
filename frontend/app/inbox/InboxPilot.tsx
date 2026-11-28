'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useAuth } from '@/components/AuthProvider';
import { useAuthGeneration } from '@/hooks/useAuthGeneration';
import { ensureAuthHeader, getAuthGeneration } from '@/lib/auth';

const TOPICS = ['X', 'Y'] as const;
type Topic = (typeof TOPICS)[number];

const PROMPT = 'Do a deep dive on X.';
const PROMPTS: Record<Topic, string> = {
  X: PROMPT,
  Y: 'Do a deep dive on Y.',
};
const FOLLOWUP = 'How’s that going?';
const QUESTION_LIMIT = 512;
const TOPIC_FOLLOWUPS: Record<Topic, string> = {
  X: 'How’s X going?',
  Y: 'How’s Y going?',
};
const COPY = {
  empty: 'No research request yet.',
  queued: 'Last saved as queued.',
  running: 'Last saved as running.',
  completed: 'Your report is ready.',
  cancelled: 'The request was cancelled.',
} as const;
const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;

type SingleSnapshot =
  | { kind: 'single'; state: Exclude<keyof typeof COPY, 'completed'> }
  | { kind: 'single'; state: 'completed'; conversation_id: string };
type SlotState =
  | { state: 'empty'; can_start: Topic[] }
  | { state: 'missing'; topic: Topic; can_start: Topic[] }
  | {
      state: 'ambiguous';
      choices: Topic[];
      can_start: Topic[];
    }
  | {
      state: 'queued' | 'running' | 'cancelled';
      topic: Topic;
      can_start: Topic[];
    }
  | {
      state: 'completed';
      topic: Topic;
      conversation_id: string;
      can_start: Topic[];
    };
type MultiSnapshot = SlotState & { kind: 'multi' };
type ResearchSnapshot = SlotState & {
  kind: 'research';
  questions: string[];
};
type Snapshot = SingleSnapshot | MultiSnapshot | ResearchSnapshot;
type RequestOptions = {
  key?: string;
  topic?: Topic;
  requireMulti?: boolean;
  question?: string;
};

function isTopic(value: unknown): value is Topic {
  return value === 'X' || value === 'Y';
}

function trimQuestion(text: string): string {
  // Match Python str.strip() on the sealed question, including its four C0 separators.
  return text.replace(
    /^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu,
    '',
  );
}

function validQuestion(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    trimQuestion(value) === value &&
    !value.includes('\0') &&
    !/[\uD800-\uDFFF]/u.test(value) &&
    [...value].length <= QUESTION_LIMIT
  );
}

function parseTopics(value: unknown): Topic[] {
  if (!Array.isArray(value)) throw Error('Invalid inbox state');
  const topics: Topic[] = [];
  for (const item of value) {
    if (!isTopic(item) || topics.includes(item))
      throw Error('Invalid inbox state');
    topics.push(item);
  }
  return topics;
}

function parseSnapshot(value: unknown, selectedTopic?: Topic): Snapshot {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw Error('Invalid inbox state');
  const record = value as Record<string, unknown>;
  if ('research_questions' in record) {
    const original = parseSlot(record, selectedTopic);
    const questions = record.research_questions;
    if (
      !Array.isArray(questions) ||
      questions.length > 2 ||
      new Set(questions).size !== questions.length ||
      !questions.every(validQuestion) ||
      (original.state === 'empty' && questions.length !== 0) ||
      (original.state === 'ambiguous' && questions.length !== 2) ||
      original.can_start.some((t) => t !== TOPICS[questions.length])
    )
      throw Error('Invalid research inbox');
    if ('topic' in original) {
      const exists = TOPICS.indexOf(original.topic) < questions.length;
      if (exists !== (original.state !== 'missing'))
        throw Error('Invalid research topic');
    }
    return { ...original, kind: 'research', questions };
  }
  if ('can_start' in record || 'topic' in record || 'choices' in record) {
    return { ...parseSlot(record, selectedTopic), kind: 'multi' };
  }
  return parseSingle(record);
}

function parseSingle(record: Record<string, unknown>): SingleSnapshot {
  const { state } = record;
  if (
    state === 'empty' ||
    state === 'queued' ||
    state === 'running' ||
    state === 'cancelled'
  ) {
    return { kind: 'single', state };
  }
  if (
    state === 'completed' &&
    typeof record.conversation_id === 'string' &&
    UUID.test(record.conversation_id)
  ) {
    return {
      kind: 'single',
      state,
      conversation_id: record.conversation_id,
    };
  }
  throw Error('Invalid inbox state');
}

function parseSlot(
  record: Record<string, unknown>,
  selectedTopic?: Topic,
): SlotState {
  const can_start = parseTopics(record.can_start);
  const { state } = record;
  if (state === 'ambiguous') {
    const choices = parseTopics(record.choices);
    if (choices.length !== 2 || can_start.length !== 0)
      throw Error('Invalid inbox state');
    if ('topic' in record || 'conversation_id' in record)
      throw Error('Invalid inbox state');
    return { state, choices, can_start: [] };
  }
  if (state === 'missing') {
    if (
      !selectedTopic ||
      'topic' in record ||
      'choices' in record ||
      'conversation_id' in record
    ) {
      throw Error('Invalid inbox state');
    }
    return { state, topic: selectedTopic, can_start };
  }
  if (state === 'empty') {
    if ('topic' in record || 'choices' in record || 'conversation_id' in record)
      throw Error('Invalid inbox state');
    return { state, can_start };
  }
  if (
    state !== 'queued' &&
    state !== 'running' &&
    state !== 'completed' &&
    state !== 'cancelled'
  ) {
    throw Error('Invalid inbox state');
  }
  if ('choices' in record || !isTopic(record.topic))
    throw Error('Invalid inbox state');
  const topic = record.topic;
  if (
    (selectedTopic !== undefined && topic !== selectedTopic) ||
    can_start.includes(topic) ||
    (state !== 'completed' && can_start.length !== 0)
  )
    throw Error('Invalid inbox state');
  if (state === 'completed') {
    if (
      typeof record.conversation_id !== 'string' ||
      !UUID.test(record.conversation_id)
    ) {
      throw Error('Invalid inbox state');
    }
    return {
      state,
      topic,
      conversation_id: record.conversation_id,
      can_start,
    };
  }
  if ('conversation_id' in record) throw Error('Invalid inbox state');
  return { state, topic, can_start };
}

function statusText(snapshot: Snapshot, acknowledged: boolean): string {
  if (snapshot.kind === 'research') {
    if (acknowledged) return 'Accepted.';
    if (snapshot.state === 'ambiguous') return 'Which question do you mean?';
    if (snapshot.state === 'missing') return 'No matching research request.';
    return COPY[snapshot.state];
  }
  if (snapshot.kind === 'multi') {
    if (acknowledged && 'topic' in snapshot)
      return `${snapshot.topic}: Accepted.`;
    if (snapshot.state === 'empty') return COPY.empty;
    if (snapshot.state === 'missing')
      return `No ${snapshot.topic} research request yet.`;
    if (snapshot.state === 'ambiguous') return 'Do you mean research X or Y?';
    return `${snapshot.topic}: ${COPY[snapshot.state]}`;
  }
  return acknowledged ? 'Accepted.' : COPY[snapshot.state];
}

function reportId(snapshot: Snapshot): string | null {
  return snapshot.state === 'completed' ? snapshot.conversation_id : null;
}

function canStart(
  snapshot: Snapshot,
  topic: Topic,
  keys: Partial<Record<Topic, string>>,
): boolean {
  if (keys[topic]) return false;
  return snapshot.kind === 'multi'
    ? snapshot.can_start.includes(topic)
    : topic === 'X' && snapshot.state === 'empty';
}

function placeholderFor(snapshot: Snapshot | null): string {
  if (!snapshot) return FOLLOWUP;
  if (snapshot.kind === 'research') {
    if (snapshot.state === 'empty') return 'Ask a research question';
    if (snapshot.can_start.length) return 'Ask for status, or type Research: …';
    return FOLLOWUP;
  }
  if (snapshot.kind === 'single')
    return snapshot.state === 'empty' ? PROMPT : FOLLOWUP;
  if (snapshot.state === 'empty' || snapshot.state === 'missing') {
    const topic =
      snapshot.state === 'missing' ? snapshot.topic : snapshot.can_start[0];
    return topic && snapshot.can_start.includes(topic)
      ? PROMPTS[topic]
      : FOLLOWUP;
  }
  if (snapshot.state === 'ambiguous') return 'X or Y';
  const topic = snapshot.can_start[0];
  return topic ? PROMPTS[topic] : FOLLOWUP;
}

function unsupportedHint(snapshot: Snapshot | null): string {
  return snapshot?.kind === 'multi'
    ? `Try “Do a deep dive on X.”, “Do a deep dive on Y.”, “X”, “Y”, or “${FOLLOWUP}”`
    : `Try “${PROMPT}” or “${FOLLOWUP}”`;
}

async function fetchSnapshot(
  endpoint: string,
  generation: number,
  signal: AbortSignal,
  options: RequestOptions,
) {
  const { key, topic } = options;
  const requireMulti = options.requireMulti ?? (!key && topic !== undefined);
  if (key && topic === undefined) throw Error('Invalid inbox request');
  const current = () => {
    if (signal.aborted || getAuthGeneration() !== generation)
      throw Error('Inbox request superseded');
  };
  current();
  const authorization = await ensureAuthHeader();
  current();
  if (!authorization) throw Error('Inbox authorization unavailable');
  const url =
    !key && topic !== undefined
      ? `${endpoint}?topic=${encodeURIComponent(topic)}`
      : endpoint;
  const body =
    key && topic !== undefined
      ? JSON.stringify({ text: options.question ?? PROMPTS[topic] })
      : undefined;
  const response = await fetch(url, {
    method: key ? 'POST' : 'GET',
    headers: key
      ? {
          Authorization: authorization,
          'Content-Type': 'application/json',
          'Idempotency-Key': key,
        }
      : { Authorization: authorization },
    ...(body !== undefined ? { body } : {}),
    cache: 'no-store',
    credentials: 'omit',
    redirect: 'error',
    signal,
  });
  if (!response.ok) throw Error('Unverified inbox response');
  const snapshot = parseSnapshot(await response.json(), topic);
  if (requireMulti && snapshot.kind === 'single')
    throw Error('Unverified inbox response');
  if (key) {
    if (
      snapshot.state === 'empty' ||
      snapshot.state === 'missing' ||
      snapshot.state === 'ambiguous'
    ) {
      throw Error('Unverified acceptance');
    }
    if (
      options.question !== undefined &&
      (topic === undefined ||
        snapshot.kind !== 'research' ||
        snapshot.topic !== topic ||
        snapshot.questions[TOPICS.indexOf(topic)] !== options.question)
    )
      throw Error('Unverified acceptance');
  } else if (topic !== undefined) {
    if (
      snapshot.kind === 'single' ||
      snapshot.state === 'empty' ||
      snapshot.state === 'ambiguous'
    ) {
      throw Error('Unverified inbox response');
    }
  } else if (snapshot.state === 'missing') {
    throw Error('Unverified inbox response');
  }
  return snapshot;
}

export function InboxPilot({ endpoint }: { endpoint: string }) {
  const generation = useAuthGeneration();
  const { isAuthenticated } = useAuth();
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-lg flex-col gap-4 px-4 py-6">
      <h1 className="text-xl font-semibold">Inbox</h1>
      {isAuthenticated ? (
        <InboxSession
          key={generation}
          endpoint={endpoint}
          generation={generation}
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
}: {
  endpoint: string;
  generation: number;
}) {
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<
    | { kind: 'checking' | 'unavailable' }
    | { kind: 'saved'; snapshot: Snapshot; acknowledged: boolean }
  >({ kind: 'checking' });
  const [canAsk, setCanAsk] = useState(false);
  const [hint, setHint] = useState('');
  const work = useRef<AbortController | null>(null);
  const startKeys = useRef<Partial<Record<Topic, string>>>({});
  const mode = useRef<Snapshot['kind'] | null>(null);

  const request = useCallback(
    (options: RequestOptions = {}) => {
      if (work.current || getAuthGeneration() !== generation) return;
      const controller = new AbortController();
      work.current = controller;
      const deadline = setTimeout(() => controller.abort(), 12_000);
      const ownsView = () =>
        work.current === controller && getAuthGeneration() === generation;
      void fetchSnapshot(endpoint, generation, controller.signal, {
        ...options,
        requireMulti: mode.current === 'multi' || options.requireMulti,
      })
        .then(
          (snapshot) => {
            if (!ownsView()) return;
            if (
              controller.signal.aborted ||
              (mode.current !== null && mode.current !== snapshot.kind)
            ) {
              setStatus({ kind: 'unavailable' });
              return;
            }
            mode.current = snapshot.kind;
            setCanAsk(true);
            setStatus({
              kind: 'saved',
              snapshot,
              acknowledged: options.key !== undefined,
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
    [endpoint, generation],
  );

  useEffect(() => {
    request();
    return () => {
      const controller = work.current;
      work.current = null;
      controller?.abort();
    };
  }, [request]);

  function begin(options: RequestOptions) {
    setInput('');
    setHint('');
    setStatus({ kind: 'checking' });
    request(options);
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (work.current || getAuthGeneration() !== generation) return;
    const text = input.trim().replaceAll("'", '’');
    const saved = status.kind === 'saved' ? status.snapshot : null;
    if (mode.current === 'research') {
      const raw = trimQuestion(input);
      if (/^(how[’']?s (that|it) going|any updates?|status)\??$/i.test(raw)) {
        begin({});
        return;
      }
      const research = saved?.kind === 'research' ? saved : null;
      const selected = research?.questions.indexOf(raw) ?? -1;
      if (selected >= 0) {
        begin({ topic: TOPICS[selected] });
        return;
      }
      const question = /^research:/i.test(raw)
        ? trimQuestion(raw.slice('Research:'.length))
        : research?.state === 'empty'
          ? raw
          : '';
      const next = research?.can_start[0];
      if (question && research?.questions.includes(question)) {
        setHint(
          'This question already exists. Send its text to check its status.',
        );
        return;
      }
      if (question && !validQuestion(question)) {
        setHint(`Use a text question of up to ${QUESTION_LIMIT} characters.`);
        return;
      }
      if (!next || startKeys.current[next] || !question) {
        setHint(
          next
            ? 'To start another question, type “Research:” followed by your question.'
            : `Ask “${FOLLOWUP}” to check the saved request.`,
        );
        return;
      }
      const key = crypto.randomUUID();
      startKeys.current[next] = key;
      begin({ key, topic: next, question });
      return;
    }
    const startTopic = TOPICS.find((topic) => PROMPTS[topic] === text);
    if (startTopic !== undefined) {
      if (!saved || !canStart(saved, startTopic, startKeys.current)) {
        setHint(`Ask “${FOLLOWUP}” to check the saved request.`);
        return;
      }
      // Keep each topic's key before fetch. An uncertain response never
      // generates a replacement or auto-resends; the next supported message
      // reads only, and the retained key is never replayed.
      const key = crypto.randomUUID();
      startKeys.current[startTopic] = key;
      begin({
        key,
        topic: startTopic,
        requireMulti: saved.kind === 'multi',
      });
      return;
    }
    const topic = isTopic(text)
      ? text
      : TOPICS.find((item) => TOPIC_FOLLOWUPS[item] === text);
    if (
      (topic === undefined && text !== FOLLOWUP) ||
      (topic !== undefined && mode.current !== 'multi')
    ) {
      setHint(unsupportedHint(saved));
      return;
    }
    begin(topic === undefined ? {} : { topic });
  }

  const saved = status.kind === 'saved' ? status.snapshot : null;
  const report = saved ? reportId(saved) : null;
  return (
    <>
      <div
        role="status"
        className="min-h-20 rounded-lg border border-[var(--color-border)] p-4"
      >
        {status.kind === 'checking' && <p>Checking saved status…</p>}
        {status.kind === 'unavailable' && <p>Status unavailable</p>}
        {status.kind === 'saved' && (
          <p>{statusText(status.snapshot, status.acknowledged)}</p>
        )}
        {saved?.kind === 'research' &&
          'topic' in saved &&
          saved.state !== 'missing' && (
            <p className="break-words">
              {saved.questions[TOPICS.indexOf(saved.topic)]}
            </p>
          )}
        {saved?.kind === 'research' && saved.state === 'ambiguous' && (
          <ul>
            {saved.questions.map((question, index) => (
              <li key={question}>
                <button
                  className="min-h-11 break-words text-left underline"
                  onClick={() => begin({ topic: TOPICS[index] })}
                >
                  {question}
                </button>
              </li>
            ))}
          </ul>
        )}
        {report && (
          <Link
            href={`/?id=${report}`}
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
              placeholder={placeholderFor(saved)}
              maxLength={
                saved?.kind === 'research'
                  ? 'Research: '.length + 2 * QUESTION_LIMIT
                  : 128
              }
              value={input}
              onChange={(event) => setInput(event.target.value)}
            />
            <button
              type="submit"
              aria-label="Send message"
              disabled={
                status.kind === 'checking' ||
                !(saved?.kind === 'research'
                  ? trimQuestion(input)
                  : input.trim())
              }
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
