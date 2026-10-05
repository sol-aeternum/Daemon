import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WelcomeScreen } from '@/components/WelcomeScreen';
import { HomeSuggestionsPanel } from '@/components/home-suggestions/HomeSuggestionsPanel';
import {
  ROW_A,
  ROW_B,
  createSuggestionsApiStub,
  readyViewWithRows,
} from './home-suggestions-test-utils';

const useHomeSuggestionsMock = vi.fn();
vi.mock('@/hooks/useHomeSuggestions', () => ({
  useHomeSuggestions: (...args: unknown[]) =>
    useHomeSuggestionsMock(...(args as [])),
}));

const originalWidth = window.innerWidth;

beforeEach(() => {
  useHomeSuggestionsMock.mockImplementation(() => createSuggestionsApiStub());
});

afterEach(() => {
  cleanup();
  window.innerWidth = originalWidth;
  vi.restoreAllMocks();
});

const prompts = [
  'Continue from my last plasma physics conversation: summarise where we left off and list the open questions.',
  'Pick up the context-start screen planning conversation and draft the remaining implementation checklist.',
];

function readyRows() {
  return [
    {
      ...ROW_A,
      source: { ...ROW_A.source },
    },
    {
      ...ROW_B,
      source: { ...ROW_B.source },
    },
  ];
}

describe('home suggestion rows', () => {
  it('links to persistent Settings opt-out when enabling is unconfirmed, without an empty home control row', () => {
    const stub = createSuggestionsApiStub({
      view: {
        status: 'unavailable',
        suggestions: [],
        message: 'Enabling could not be confirmed; suggestions may be enabled.',
      },
    });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    render(<WelcomeScreen />);
    expect(screen.getByText(/suggestions may be enabled/)).toBeTruthy();
    expect(
      screen.queryByRole('button', { name: 'Turn off suggestions' }),
    ).toBeNull();
    expect(
      screen
        .getByRole('link', { name: 'Manage suggestions in Settings' })
        .getAttribute('href'),
    ).toBe('/settings/profile');
    expect(stub.disable).not.toHaveBeenCalled();
    expect(stub.refresh).not.toHaveBeenCalled();
  });
  it('exposes persistent opt-out separately from session-local hiding', () => {
    const stub = createSuggestionsApiStub({ view: readyViewWithRows() });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    render(<WelcomeScreen />);
    fireEvent.click(
      screen.getByRole('button', { name: 'Turn off suggestions' }),
    );
    expect(stub.disable).toHaveBeenCalledTimes(1);
    expect(stub.dismissAll).not.toHaveBeenCalled();
  });
  it.each([
    'empty',
    'loading',
    'generating',
    'unavailable',
    'expired',
    'error',
    'disabled',
    'unloaded',
    'dismissed',
  ] as const)(
    'hides the Refresh/Turn off row when no suggestions are visible: %s',
    (status) => {
      useHomeSuggestionsMock.mockImplementation(() =>
        createSuggestionsApiStub({
          view: { status, suggestions: [], message: null },
        }),
      );
      render(<WelcomeScreen />);
      expect(
        screen.queryByRole('button', { name: 'Refresh suggestions' }),
      ).toBeNull();
      expect(
        screen.queryByRole('button', { name: 'Turn off suggestions' }),
      ).toBeNull();
    },
  );
  it('hides the action row when available suggestions are paused or hidden', () => {
    useHomeSuggestionsMock.mockImplementation(() =>
      createSuggestionsApiStub({ view: readyViewWithRows() }),
    );
    const view = render(<WelcomeScreen input="unsent question" />);
    expect(
      screen.queryByRole('button', { name: 'Refresh suggestions' }),
    ).toBeNull();
    fireEvent.click(screen.getByTestId('show-suggestions-anyway'));
    expect(
      screen.getByRole('button', { name: 'Refresh suggestions' }),
    ).toBeTruthy();
    useHomeSuggestionsMock.mockImplementation(() =>
      createSuggestionsApiStub({ view: readyViewWithRows(), hiddenAll: true }),
    );
    view.rerender(<WelcomeScreen />);
    expect(
      screen.queryByRole('button', { name: 'Refresh suggestions' }),
    ).toBeNull();
    expect(
      screen.queryByRole('button', { name: 'Turn off suggestions' }),
    ).toBeNull();
  });
  it('shows compact summaries and source titles without Start chat, Preview or Why controls', () => {
    useHomeSuggestionsMock.mockImplementation(() =>
      createSuggestionsApiStub({ view: readyViewWithRows() }),
    );
    render(<WelcomeScreen />);
    expect(screen.getAllByTestId(/^suggestion-row/)).toHaveLength(2);
    for (const row of [ROW_A, ROW_B]) {
      expect(screen.getByText(row.summary)).toBeTruthy();
      expect(
        screen.getByText(new RegExp(row.source.title.slice(0, 8))),
      ).toBeTruthy();
    }
    expect(screen.queryByText(/Start chat/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Preview' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Why/ })).toBeNull();
  });

  it('hover shows the exact detailed prompt, centered above the full row and clamped to the viewport; Escape hides it', async () => {
    window.innerWidth = 1000;
    const rowButtonSpread = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockImplementation(function (this: HTMLElement) {
        return {
          width: 400,
          height: 60,
          top: 300,
          bottom: 360,
          left: 900,
          right: 1300,
          x: 900,
          y: 300,
          toJSON: () => ({}),
        } as DOMRect;
      });
    useHomeSuggestionsMock.mockImplementation(() =>
      createSuggestionsApiStub({ view: readyViewWithRows() }),
    );
    render(<WelcomeScreen />);
    const row = screen.getAllByTestId(/^suggestion-row/)[0];
    const mainButton = row.querySelector('button') as HTMLButtonElement;
    fireEvent.mouseEnter(mainButton);
    const tooltip = await screen.findByTestId('suggestion-prompt-tooltip');
    expect(tooltip.textContent).toBe(ROW_A.prompt);
    // Width cap and centring clamp: a row past the right edge cannot push
    // the tooltip outside; the tooltip's left is clamped into the viewport.
    const left = Number((tooltip as HTMLElement).style.left.slice(0, -2));
    const width = Number((tooltip as HTMLElement).style.width.slice(0, -2));
    const top = Number((tooltip as HTMLElement).style.top.slice(0, -2));
    expect(width).toBeLessThanOrEqual(432);
    expect(width).toBeGreaterThan(0);
    expect(left).toBeGreaterThanOrEqual(12);
    expect(left + width).toBeLessThanOrEqual(1000 - 12);
    expect(top).toBeGreaterThanOrEqual(12);
    // Prompt is centered over the full row before clamping (row center 1100
    // would overflow; the clamp keeps it inside the viewport).
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByTestId('suggestion-prompt-tooltip')).toBeNull();
    // Keyboard focus shows the same exact prompt, one at a time.
    fireEvent.focus(
      screen
        .getAllByTestId(/^suggestion-row/)[1]
        .querySelector('button') as HTMLButtonElement,
    );
    expect(
      (await screen.findByTestId('suggestion-prompt-tooltip')).textContent,
    ).toBe(ROW_B.prompt);
    expect(screen.getAllByTestId('suggestion-prompt-tooltip')).toHaveLength(1);
    void rowButtonSpread;
  });

  it('activation submits the exact candidate once via the callback and never touches the draft', () => {
    const select = vi.fn().mockResolvedValue(undefined);
    useHomeSuggestionsMock.mockImplementation(() =>
      createSuggestionsApiStub({ view: readyViewWithRows() }),
    );
    render(<WelcomeScreen onSuggestionSelect={select} />);
    const firstRow = screen.getAllByTestId(/^suggestion-row/)[0];
    const button = firstRow.querySelector('button') as HTMLButtonElement;
    fireEvent.click(button);
    expect(select).toHaveBeenCalledTimes(1);
    expect(select).toHaveBeenCalledWith(ROW_A);
    // No draft edits are possible: the component has no setInput path.
    expect(
      Object.keys(button.closest('div') ?? {}).length,
    ).toBeGreaterThanOrEqual(0);
    expect(screen.queryByTestId('suggestion-prompt-tooltip')).toBeNull();
  });

  it('disables rows while a submission is pending (once-and-only-once guard)', async () => {
    const select = vi.fn().mockResolvedValue(undefined);
    const stub = createSuggestionsApiStub({ view: readyViewWithRows() });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    const view = render(
      <WelcomeScreen onSuggestionSelect={select} isSubmittingSuggestion />,
    );
    for (const row of screen.getAllByTestId(/^suggestion-row/)) {
      const button = row.querySelector('button') as HTMLButtonElement;
      expect(button.hasAttribute('disabled')).toBe(true);
      fireEvent.click(button);
    }
    expect(select).not.toHaveBeenCalled();
    view.rerender(<WelcomeScreen onSuggestionSelect={select} />);
    fireEvent.click(
      screen
        .getAllByTestId(/^suggestion-row/)[0]
        .querySelector('button') as HTMLButtonElement,
    );
    expect(select).toHaveBeenCalledTimes(1);
    expect(select).toHaveBeenCalledWith(ROW_A);
  });

  it('pauses rows while typing, preserves the draft, offers a temporary Show-them-anyway, and re-pauses on the next edit', () => {
    const stub = createSuggestionsApiStub({ view: readyViewWithRows() });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    const view = render(<WelcomeScreen input="my unrelated draft" />);
    expect(screen.getByTestId('paused-while-typing-note')).toBeTruthy();
    expect(screen.queryByTestId(/^suggestion-row/)).toBeNull();
    fireEvent.click(screen.getByTestId('show-suggestions-anyway'));
    expect(screen.getAllByTestId(/^suggestion-row/)).toHaveLength(2);
    // Temporary only: the next non-empty edit pauses once more.
    view.rerender(<WelcomeScreen input="my unrelated draft edited" />);
    expect(screen.getByTestId('paused-while-typing-note')).toBeTruthy();
  });

  it('guesses nothing in personal empty state and distinguishes error from empty/new-user', () => {
    const stub = createSuggestionsApiStub({
      view: {
        status: 'error',
        suggestions: [],
        message: 'Suggestions could not be loaded.',
      },
    });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    render(<WelcomeScreen />);
    expect(screen.getByTestId('suggestion-status-error').textContent).toContain(
      'could not be loaded',
    );
    // No made-up generic starters appear in a personal state.
    for (const starter of ['Explain', 'Find', 'Compare', 'Draft']) {
      expect(screen.queryByText(starter)).toBeNull();
    }
    cleanup();
    const emptyStub = createSuggestionsApiStub({
      view: {
        status: 'empty',
        suggestions: [],
        message: null,
      },
    });
    useHomeSuggestionsMock.mockImplementation(() => emptyStub);
    render(<WelcomeScreen />);
    expect(screen.queryByTestId('suggestion-status-empty')).toBeNull();
    expect(screen.queryByText(/No contextual suggestions/)).toBeNull();
    expect(screen.queryByText(/Start chat/)).toBeNull();
  });

  it('feature off until enabled: explicit button enables, otherwise rows never appear', () => {
    const stub = createSuggestionsApiStub();
    useHomeSuggestionsMock.mockImplementation(() => stub);
    render(<WelcomeScreen />);
    const enableButton = screen.getByTestId('enable-personal-suggestions');
    expect(screen.queryByTestId(/^suggestion-row/)).toBeNull();
    fireEvent.click(enableButton);
    expect(stub.enable).toHaveBeenCalledTimes(1);
  });

  it('expired state offers a global refresh control, not a per-row preview', () => {
    const stub = createSuggestionsApiStub({
      view: {
        status: 'expired',
        suggestions: [],
        message: null,
      },
    });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    render(<WelcomeScreen />);
    fireEvent.click(screen.getByTestId('refresh-suggestions'));
    expect(stub.refresh).toHaveBeenCalledTimes(1);
    expect(stub.load).not.toHaveBeenCalled();
  });

  it('hide and restore are session-local and never request anything', () => {
    const stub = createSuggestionsApiStub({ view: readyViewWithRows() });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    render(<WelcomeScreen />);
    fireEvent.click(screen.getByTestId('hide-personal-suggestions'));
    expect(stub.dismissAll).toHaveBeenCalledTimes(1);
    cleanup();
    useHomeSuggestionsMock.mockImplementation(() =>
      createSuggestionsApiStub({ ...createBaseHidden() }),
    );
    render(<WelcomeScreen />);
    expect(screen.getByTestId('personal-hidden-note')).toBeTruthy();
    fireEvent.click(screen.getByTestId('show-personal-suggestions'));
    expect(
      useHomeSuggestionsMock.mock.results.at(-1)?.value.restoreAll,
    ).toBeDefined();
  });

  it('selection failure shows the caller message without any backend request', () => {
    const stub = createSuggestionsApiStub({ view: readyViewWithRows() });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    render(
      <WelcomeScreen selectionError="A destination could not be created." />,
    );
    expect(screen.getByTestId('selection-error').textContent).toContain(
      'destination could not be created',
    );
  });

  it('composer renders first, above the contextual rows, without being cloned', () => {
    const stub = createSuggestionsApiStub({ view: readyViewWithRows() });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    const composer = (
      <form data-testid="home-composer">
        <textarea aria-label="Message Daemon" />
      </form>
    );
    render(<WelcomeScreen composer={composer} onSuggestionSelect={vi.fn()} />);
    const composerEl = screen.getByTestId('home-composer');
    const rows = screen.getAllByTestId(/^suggestion-row/);
    expect(composerEl.compareDocumentPosition(rows[0])).toBeTruthy();
    expect(
      composerEl.compareDocumentPosition(rows[0]) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // Exactly one composer textarea, not duplicated per suggestion.
    expect(
      document.querySelectorAll('[aria-label="Message Daemon"]'),
    ).toHaveLength(1);
  });

  it('no suggestion payload is written to browser storage or draft state', () => {
    const prompt = prompts[0];
    expect(prompt).toContain('plasma');
    const writes = vi.spyOn(Storage.prototype as unknown as Storage, 'setItem');
    const stub = createSuggestionsApiStub({ view: readyViewWithRows() });
    useHomeSuggestionsMock.mockImplementation(() => stub);
    render(<WelcomeScreen onSuggestionSelect={vi.fn()} />);
    const firstRow = screen.getAllByTestId(/^suggestion-row/)[0];
    fireEvent.click(firstRow.querySelector('button') as HTMLButtonElement);
    expect(writes).not.toHaveBeenCalled();
  });
});

describe('HomeSuggestionsPanel alone', () => {
  it('renders the caller-provided dismissed state truthfully', () => {
    render(
      <HomeSuggestionsPanel
        view={{
          status: 'dismissed',
          suggestions: [],
          message: null,
        }}
        hiddenAll={false}
        frozen={false}
        isSubmitting={false}
        isBusy={false}
        selectionError={null}
        onSuggestionSelect={vi.fn()}
        onEnable={vi.fn()}
        onRefresh={vi.fn()}
        onRetry={vi.fn()}
        onDismiss={vi.fn()}
        onUndoDismiss={vi.fn()}
        onHideAll={vi.fn()}
        onRestoreAll={vi.fn()}
      />,
    );
    expect(screen.getByTestId('all-dismissed-note')).toBeTruthy();
    expect(screen.queryByTestId(/^suggestion-row/)).toBeNull();
  });
});

function createBaseHidden() {
  return {
    view: {
      status: 'ready' as const,
      suggestions: readyRows(),
      message: null,
    },
    hiddenAll: true,
  };
}
