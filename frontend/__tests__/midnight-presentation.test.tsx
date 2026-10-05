import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { WelcomeScreen } from '@/components/WelcomeScreen';
import { ChatInputBar } from '@/components/ChatInputBar';
import { CopyResponseButton } from '@/components/CopyResponseButton';
import MemoryFilters from '@/components/settings/memory/MemoryFilters';
import {
  createSuggestionsApiStub,
  readyViewWithRows,
} from './home-suggestions-test-utils';

const useHomeSuggestionsMock = vi.fn();
vi.mock('@/hooks/useHomeSuggestions', () => ({
  useHomeSuggestions: (...args: unknown[]) =>
    useHomeSuggestionsMock(...(args as [])),
}));
beforeEach(() => {
  useHomeSuggestionsMock.mockImplementation(() => createSuggestionsApiStub());
});

vi.mock('@/components/ModelSelector', () => ({ ModelSelector: () => null }));
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it('pauses suggestion rows while typing and keeps the draft untouched', () => {
  useHomeSuggestionsMock.mockImplementation(() =>
    createSuggestionsApiStub({ view: readyViewWithRows() }),
  );
  render(<WelcomeScreen input="  existing draft  " />);
  // With unrelated unsent text, ready rows pause; nothing is submitted.
  expect(screen.getByTestId('paused-while-typing-note')).toBeTruthy();
  expect(screen.queryAllByTestId(/^suggestion-row/)).toHaveLength(0);
  expect(screen.queryByRole('button', { name: /Start chat/ })).toBeNull();
  expect(screen.queryByRole('button', { name: 'Preview' })).toBeNull();
});

it('shows ready contextual rows without Start chat, Preview or Why controls, and dismiss/undo is session-local', () => {
  const stub = createSuggestionsApiStub({ view: readyViewWithRows() });
  useHomeSuggestionsMock.mockImplementation(() => stub);
  const select = vi.fn();
  render(<WelcomeScreen onSuggestionSelect={select} />);
  const rows = screen.getAllByTestId(/^suggestion-row/);
  expect(rows).toHaveLength(2);
  expect(screen.queryByTestId('paused-while-typing-note')).toBeNull();
  expect(screen.queryByText(/Start chat/)).toBeNull();
  expect(screen.queryByText('Preview')).toBeNull();
  expect(screen.queryByText(/^Why\b/)).toBeNull();
  fireEvent.click(
    screen.getAllByRole('button', { name: /Dismiss suggestion/ })[0],
  );
  expect(stub.dismiss).toHaveBeenCalledWith('s1');
  fireEvent.click(screen.getByTestId('undo-dismiss'));
  expect(stub.undoDismiss).toHaveBeenCalledWith('s1');
});

it('does not submit IME composition and announces blocked send while streaming', () => {
  const submit = vi.fn();
  const props = {
    input: 'draft',
    onInputChange: vi.fn(),
    onSubmit: submit,
    onStop: vi.fn(),
    selectedModel: 'auto',
    onSelectModel: vi.fn(),
    isRecording: false,
    isConnecting: false,
    startRecording: vi.fn(),
    stopRecording: vi.fn(),
  };
  const view = render(<ChatInputBar {...props} isLoading={false} />);
  fireEvent.keyDown(screen.getByRole('textbox'), {
    key: 'Enter',
    isComposing: true,
  });
  expect(submit).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  expect(submit).toHaveBeenCalledTimes(1);
  view.rerender(<ChatInputBar {...props} isLoading />);
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  expect(submit).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('status').textContent).toContain(
    'your draft is kept',
  );
  expect(
    screen
      .getByRole('button', { name: /Voice input is unavailable/ })
      .hasAttribute('disabled'),
  ).toBe(true);
});

it('copies only the supplied response and visibly confirms success', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    get: () => ({ writeText: copy }),
  });
  render(<CopyResponseButton content={'Answer\n```js\n42\n```'} />);
  fireEvent.click(screen.getByRole('button', { name: 'Copy response' }));
  await screen.findByText('Copied');
  expect(copy).toHaveBeenCalledWith('Answer\n```js\n42\n```');
});

it('selects manual fallback, traps Tab, and returns focus after Escape', async () => {
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    get: () => ({ writeText: vi.fn().mockRejectedValue(new Error('denied')) }),
  });
  render(<CopyResponseButton content="Response fixture" />);
  const trigger = screen.getByRole('button', { name: 'Copy response' });
  fireEvent.click(trigger);
  const text = (await screen.findByRole('textbox', {
    name: 'Response to copy',
  })) as HTMLTextAreaElement;
  expect(document.activeElement).toBe(text);
  expect(text.selectionEnd - text.selectionStart).toBe(text.value.length);
  fireEvent.keyDown(text, { key: 'Tab', shiftKey: true });
  expect(document.activeElement).toBe(
    screen.getByRole('button', { name: 'Close' }),
  );
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

it('keeps memory filter payloads and announces selected chips', async () => {
  vi.useFakeTimers();
  const change = vi.fn();
  render(<MemoryFilters onFilterChange={change} />);
  fireEvent.change(screen.getByRole('textbox', { name: 'Search memories' }), {
    target: { value: '  project  ' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Fact' }));
  await act(async () => vi.advanceTimersByTime(500));
  expect(change).toHaveBeenLastCalledWith({
    search: 'project',
    category: 'fact',
    status: 'active',
  });
  expect(
    screen.getByRole('button', { name: 'Fact' }).getAttribute('aria-pressed'),
  ).toBe('true');
});
