import React, { useRef } from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ConversationContents,
  type ContentsEntry,
} from '../components/ConversationContents';
import { useStopShortcut } from '../hooks/useStopShortcut';

let desktop = true;
let frameId = 0;
const frames = new Map<number, FrameRequestCallback>();
const resizeCallbacks = new Set<() => void>();
const originalShowModal = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  'showModal',
);
const originalClose = Object.getOwnPropertyDescriptor(
  HTMLDialogElement.prototype,
  'close',
);
const entries = [
  { id: 'first', label: 'Same first line' },
  { id: 'second', label: 'Same first line' },
];

function flushFrames() {
  act(() => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((callback) => callback(0));
  });
}

function Harness({
  items = entries,
  conversation = 'one',
  onNavigate = vi.fn(),
  onStop = vi.fn(),
  renderSpy,
}: {
  items?: ContentsEntry[];
  conversation?: string;
  onNavigate?: (id: string) => void;
  onStop?: () => void;
  renderSpy?: () => void;
}) {
  const ref = useRef<HTMLElement>(null);
  useStopShortcut({ active: true, onStop });
  renderSpy?.();
  return (
    <>
      <main ref={ref} data-testid="messages">
        <div>
          {items.map((entry, index) => (
            <article
              key={entry.id}
              data-message-id={entry.id}
              ref={(element) => {
                if (!element) return;
                element.getBoundingClientRect = () =>
                  ({
                    top: index * 100 - (ref.current?.scrollTop || 0),
                  }) as DOMRect;
              }}
            >
              {entry.id}
            </article>
          ))}
        </div>
      </main>
      <ConversationContents
        key={conversation}
        entries={items}
        scrollContainerRef={ref}
        onNavigate={onNavigate}
        mobile={!desktop}
      />
    </>
  );
}

beforeEach(() => {
  desktop = true;
  frames.clear();
  resizeCallbacks.clear();
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  vi.stubGlobal('matchMedia', () => ({
    matches: desktop,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
  vi.stubGlobal(
    'ResizeObserver',
    class {
      callback: () => void;
      constructor(callback: () => void) {
        this.callback = callback;
        resizeCallbacks.add(callback);
      }
      observe() {}
      disconnect() {
        resizeCallbacks.delete(this.callback);
      }
    },
  );
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: vi.fn(function (this: HTMLDialogElement) {
      this.setAttribute('open', '');
      this.querySelector('button')?.focus();
    }),
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: vi.fn(function (this: HTMLDialogElement) {
      this.removeAttribute('open');
    }),
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  if (originalShowModal)
    Object.defineProperty(
      HTMLDialogElement.prototype,
      'showModal',
      originalShowModal,
    );
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'showModal');
  if (originalClose)
    Object.defineProperty(HTMLDialogElement.prototype, 'close', originalClose);
  else Reflect.deleteProperty(HTMLDialogElement.prototype, 'close');
});

describe('Conversation contents', () => {
  it('is present for even one loaded user prompt, absent for none', () => {
    const { rerender } = render(<Harness items={[entries[0]]} />);
    expect(
      screen.getByRole('navigation', { name: 'Conversation contents' }),
    ).toBeTruthy();
    expect(screen.getByText('Loaded portion only · 1 prompt')).toBeTruthy();
    rerender(<Harness items={[]} />);
    expect(screen.queryByRole('navigation')).toBeNull();
  });

  it('navigates duplicate labels by stable IDs and renumbers on prepend', () => {
    const navigate = vi.fn();
    const { rerender } = render(<Harness onNavigate={navigate} />);
    let buttons = within(screen.getByRole('navigation')).getAllByRole('button');
    fireEvent.click(buttons[1]);
    expect(navigate).toHaveBeenLastCalledWith('second');
    const original = buttons[1];
    rerender(
      <Harness
        items={[{ id: 'older', label: 'Older' }, ...entries]}
        onNavigate={navigate}
      />,
    );
    buttons = within(screen.getByRole('navigation')).getAllByRole('button');
    expect(buttons[2]).toBe(original);
    expect(buttons[2].textContent).toBe('3.Same first line');
    fireEvent.click(buttons[2]);
    expect(navigate).toHaveBeenLastCalledWith('second');
  });

  it('updates only its local current position, not the page, on scroll', () => {
    const renderSpy = vi.fn();
    const { rerender } = render(<Harness renderSpy={renderSpy} />);
    flushFrames();
    const buttons = within(screen.getByRole('navigation')).getAllByRole(
      'button',
    );
    expect(buttons[0].getAttribute('aria-current')).toBe('location');
    const renders = renderSpy.mock.calls.length;
    const main = screen.getByTestId('messages');
    main.scrollTop = 110;
    fireEvent.scroll(main);
    flushFrames();
    expect(buttons[1].getAttribute('aria-current')).toBe('location');
    expect(renderSpy).toHaveBeenCalledTimes(renders);
    const observers = resizeCallbacks.size;
    // A streamed assistant update gives the contents equivalent prompt entries.
    rerender(
      <Harness
        items={entries.map((entry) => ({ ...entry }))}
        renderSpy={renderSpy}
      />,
    );
    expect(resizeCallbacks.size).toBe(observers);
    expect(buttons[1].getAttribute('aria-current')).toBe('location');
    rerender(
      <Harness
        conversation="two"
        items={[{ id: 'different', label: 'Another conversation' }]}
      />,
    );
    flushFrames();
    expect(screen.queryByText('Same first line')).toBeNull();
    expect(screen.getByRole('button').dataset.contentsMessageId).toBe(
      'different',
    );
  });

  it('supports Arrow keys and Home/End without selecting until activated', () => {
    const navigate = vi.fn();
    render(<Harness onNavigate={navigate} />);
    const buttons = within(screen.getByRole('navigation')).getAllByRole(
      'button',
    );
    buttons[0].focus();
    fireEvent.keyDown(buttons[0], { key: 'ArrowDown' });
    expect(document.activeElement).toBe(buttons[1]);
    fireEvent.keyDown(buttons[1], { key: 'Home' });
    expect(document.activeElement).toBe(buttons[0]);
    fireEvent.keyDown(buttons[0], { key: 'End' });
    expect(document.activeElement).toBe(buttons[1]);
    expect(navigate).not.toHaveBeenCalled();
  });

  it('opens a native mobile dialog, owns Escape, restores focus and navigates after closing', () => {
    desktop = false;
    const stop = vi.fn();
    const navigate = vi.fn(() =>
      expect(screen.queryByRole('dialog')).toBeNull(),
    );
    const { rerender } = render(
      <Harness onStop={stop} onNavigate={navigate} />,
    );
    const opener = screen.getByRole('button', {
      name: 'Open conversation contents',
    });
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole('dialog', { name: 'Contents' })).toBeTruthy();
    expect(document.activeElement).toBe(
      screen.getByRole('button', { name: 'Close conversation contents' }),
    );
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(opener);
    expect(stop).not.toHaveBeenCalled();
    fireEvent.click(opener);
    const shows = vi.mocked(HTMLDialogElement.prototype.showModal).mock.calls
      .length;
    rerender(
      <Harness
        items={entries.map((entry) => ({ ...entry }))}
        onStop={stop}
        onNavigate={navigate}
      />,
    );
    expect(HTMLDialogElement.prototype.showModal).toHaveBeenCalledTimes(shows);
    fireEvent.click(
      within(screen.getByRole('navigation')).getAllByRole('button')[1],
    );
    expect(navigate).toHaveBeenCalledWith('second');
    expect(document.activeElement).toBe(opener);
  });
});
