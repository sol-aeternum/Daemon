import { vi } from 'vitest';
import type { HomeSuggestion } from '@/lib/homeSuggestions';
import type { HomeSuggestionsViewState } from '@/lib/homeSuggestions';

export const ROW_A: HomeSuggestion = {
  id: 's1',
  summary: 'Summarise the plasma physics thread',
  prompt:
    'Continue from my last plasma physics conversation: summarise where we left off and list the open questions.',
  source: { conversationId: 'c1', title: 'Plasma physics deep dive' },
  expiresAt: new Date(Date.now() + 3600_000).toISOString(),
};

export const ROW_B: HomeSuggestion = {
  id: 's2',
  summary: 'Land the context-start screen plan',
  prompt:
    'Pick up the context-start screen planning conversation and draft the remaining implementation checklist.',
  source: { conversationId: 'c2', title: 'Context-start screen planning' },
  expiresAt: new Date(Date.now() + 3600_000).toISOString(),
};

export function readyViewWithRows(rows: HomeSuggestion[] = [ROW_A, ROW_B]) {
  const view: HomeSuggestionsViewState = {
    status: 'ready',
    suggestions: rows,
    message: null,
  };
  return view;
}

export function baseReadyView(): HomeSuggestionsViewState {
  return { status: 'ready', suggestions: [], message: null };
}

/**
 * A complete deterministic stub of useHomeSuggestions for component tests;
 * every action is a vi.fn so a single test can assert a caller's intent.
 */
export function createSuggestionsApiStub(
  overrides: Partial<ReturnType<typeof createBaseStub>> = {},
) {
  return {
    ...createBaseStub(),
    ...overrides,
  };
}

export function createBaseStub() {
  return {
    view: {
      status: 'disabled',
      suggestions: [],
      message: null,
    } as HomeSuggestionsViewState,
    allSuggestions: [] as HomeSuggestion[],
    dismissedIds: new Set<string>(),
    hiddenAll: false,
    dismissAll: vi.fn(),
    restoreAll: vi.fn(),
    undoDismiss: vi.fn((id: string) => id),
    dismiss: vi.fn((id: string) => id),
    load: vi.fn(async () => undefined),
    refresh: vi.fn(async () => undefined),
    enable: vi.fn(async () => true),
    disable: vi.fn(async () => true),
  };
}
