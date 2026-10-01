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

it('appends an ordinary starter without trimming or replacing existing work', () => {
  const setInput = vi.fn();
  render(<WelcomeScreen input="  existing draft  " setInput={setInput} />);
  fireEvent.click(screen.getByRole('button', { name: /^Find/ }));
  expect(setInput).toHaveBeenCalledWith(
    '  existing draft  \nSearch the web for…',
  );
  expect(screen.queryByRole('button', { name: /Create Image/ })).toBeNull();
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
