import { describe, expect, it } from 'vitest';
import {
  dropExpiredSuggestions,
  isHomeSuggestionExpired,
  parseHomeSuggestion,
  parseHomeSuggestionsBody,
  parseHomeSuggestionsRefreshBody,
} from '../lib/homeSuggestions';

const FUTURE = new Date(Date.now() + 60_000).toISOString();
const PAST = new Date(Date.now() - 60_000).toISOString();

const WIRE_ROW = {
  id: 's1',
  summary: ' Summarise the thread ',
  prompt: 'Continue my plasma conversation.',
  source: { conversation_id: 'c1', title: 'Plasma thread' },
  expires_at: FUTURE,
};

describe('parseHomeSuggestion', () => {
  it('trims summary and maps wire source fields to the view shape', () => {
    const parsed = parseHomeSuggestion(WIRE_ROW);
    expect(parsed).toMatchObject({
      id: 's1',
      summary: 'Summarise the thread',
      prompt: 'Continue my plasma conversation.',
      source: { conversationId: 'c1', title: 'Plasma thread' },
    });
  });

  it('rejects malformed, incomplete or oversized rows, never trusting them', () => {
    expect(parseHomeSuggestion(null)).toBeNull();
    expect(parseHomeSuggestion({ ...WIRE_ROW, id: '' })).toBeNull();
    expect(parseHomeSuggestion({ ...WIRE_ROW, prompt: 5 })).toBeNull();
    expect(
      parseHomeSuggestion({ ...WIRE_ROW, source: { title: 'x' } }),
    ).toBeNull();
    expect(
      parseHomeSuggestion({ ...WIRE_ROW, expires_at: 'not-a-date' }),
    ).toBeNull();
    expect(
      parseHomeSuggestion({ ...WIRE_ROW, summary: 'a'.repeat(601) }),
    ).toBeNull();
    expect(
      parseHomeSuggestion({ ...WIRE_ROW, prompt: 'a'.repeat(20001) }),
    ).toBeNull();
  });
});

describe('parseHomeSuggestionsBody', () => {
  it('accepts a valid body and caps rows at three', () => {
    const parsed = parseHomeSuggestionsBody({
      enabled: true,
      status: 'ready',
      suggestions: [WIRE_ROW, WIRE_ROW, WIRE_ROW, { ...WIRE_ROW, id: 's4' }],
    });
    expect(parsed?.status).toBe('ready');
    expect(parsed?.enabled).toBe(true);
    expect(parsed?.suggestions).toHaveLength(3);
  });

  it('fails closed on an unrecognised status, non-boolean enabled or non-array list', () => {
    const base = {
      enabled: true,
      status: 'ready',
      suggestions: [WIRE_ROW],
    };
    expect(parseHomeSuggestionsBody({ ...base, status: 'maybe' })).toBeNull();
    expect(parseHomeSuggestionsBody({ ...base, enabled: 'yes' })).toBeNull();
    expect(
      parseHomeSuggestionsBody({ ...base, suggestions: WIRE_ROW }),
    ).toBeNull();
    expect(parseHomeSuggestionsBody(null)).toBeNull();
    expect(parseHomeSuggestionsBody('[]')).toBeNull();
  });

  it('keeps working when individual rows are malformed', () => {
    const parsed = parseHomeSuggestionsBody({
      enabled: true,
      status: 'ready',
      suggestions: [{ nope: true }, WIRE_ROW],
    });
    expect(parsed?.suggestions).toHaveLength(1);
  });

  it('passes safe messages through only when non-empty strings', () => {
    expect(
      parseHomeSuggestionsBody({
        ...{ enabled: true, status: 'ready', suggestions: [] },
        message: 'Cache busy',
      })?.message,
    ).toBe('Cache busy');
    expect(
      parseHomeSuggestionsBody({
        ...{ enabled: true, status: 'ready', suggestions: [] },
        message: 3,
      })?.message,
    ).toBeNull();
  });
});

describe('refresh parsing', () => {
  it('accepts only known refresh statuses', () => {
    expect(parseHomeSuggestionsRefreshBody({ status: 'queued' })?.status).toBe(
      'queued',
    );
    expect(
      parseHomeSuggestionsRefreshBody({ status: 'unchanged' })?.status,
    ).toBe('unchanged');
    expect(parseHomeSuggestionsRefreshBody({ status: 'weird' })).toBeNull();
    expect(parseHomeSuggestionsRefreshBody(undefined)).toBeNull();
  });
});

describe('expiry helpers', () => {
  const row = {
    ...parseHomeSuggestion({ ...WIRE_ROW, expires_at: PAST }),
  } as NonNullable<ReturnType<typeof parseHomeSuggestion>>;

  it('marks passed expiry strictly and filters expired rows out', () => {
    expect(isHomeSuggestionExpired(row)).toBe(true);
    expect(isHomeSuggestionExpired({ ...row, expiresAt: FUTURE })).toBe(false);
    expect(
      isHomeSuggestionExpired({ ...row, expiresAt: 'garbage' } as never),
    ).toBe(true);
    expect(
      dropExpiredSuggestions([row, { ...row, expiresAt: FUTURE }]),
    ).toHaveLength(1);
  });
});
