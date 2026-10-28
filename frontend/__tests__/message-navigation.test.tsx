import React, { useLayoutEffect } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CollapsibleMessage } from '../components/CollapsibleMessage';
import { useChatScroll } from '../hooks/useChatScroll';

let resize: () => void;
let frameId = 0;
let generation = 0;
const frames = new Map<number, FrameRequestCallback>();
const items = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth'];

function flushFrames() {
  act(() => {
    const pending = [...frames.values()];
    frames.clear();
    pending.forEach((callback) => callback(0));
  });
}

function Harness({
  conversation = 'one',
  messages = items,
  stream = '',
  growth = 0,
}: {
  conversation?: string;
  messages?: string[];
  stream?: string;
  growth?: number;
}) {
  const {
    scrollContainerRef,
    messagesEndRef,
    onScroll,
    scrollToMessage,
    jumpToLatest,
    isScrolledUp,
    preservePrepend,
  } = useChatScroll({
    conversationId: conversation,
    messages,
    isLoading: !!stream,
    authGeneration: generation,
  });
  useLayoutEffect(() => {
    const main = scrollContainerRef.current!;
    const articles = () => [...main.querySelectorAll<HTMLElement>('article')];
    const height = (element: HTMLElement) =>
      (element.querySelector('[hidden]') ? 100 : 300) +
      (element === articles()[0] ? growth : 0);
    vi.spyOn(main, 'getBoundingClientRect').mockReturnValue({
      top: 0,
    } as DOMRect);
    Object.defineProperties(main, {
      scrollHeight: {
        configurable: true,
        get: () =>
          articles().reduce((sum, element) => sum + height(element), 0) +
          stream.length,
      },
      clientHeight: { configurable: true, value: 400 },
      scrollTo: {
        configurable: true,
        value: ({ top }: ScrollToOptions) => {
          main.scrollTop = Math.max(
            0,
            Math.min(top || 0, main.scrollHeight - 400),
          );
        },
      },
    });
    for (const element of articles()) {
      element.getBoundingClientRect = () => {
        const before = articles().slice(0, articles().indexOf(element));
        const top =
          before.reduce((sum, item) => sum + height(item), 0) - main.scrollTop;
        return { top, bottom: top + height(element) } as DOMRect;
      };
    }
  });
  return (
    <>
      <main ref={scrollContainerRef} onScroll={onScroll} data-testid="messages">
        <div>
          {messages.map((id) => (
            <CollapsibleMessage
              key={`${conversation}-${id}`}
              messageId={id}
              title={id}
              preview="Preview"
              collapsible
            >
              <p>Detail {id}</p>
            </CollapsibleMessage>
          ))}
          <div>{stream}</div>
          <div ref={messagesEndRef} />
        </div>
      </main>
      <button onClick={() => scrollToMessage('second')}>Select second</button>
      <button onClick={() => scrollToMessage('fourth')}>Select fourth</button>
      <button onClick={jumpToLatest}>Latest</button>
      <button onClick={preservePrepend}>Preserve prepend</button>
      <span>{isScrolledUp ? 'Reading' : 'Following'}</span>
    </>
  );
}

beforeEach(() => {
  generation++;
  frames.clear();
  Object.defineProperty(HTMLElement.prototype, 'onbeforematch', {
    configurable: true,
    value: null,
  });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    frames.set(++frameId, callback);
    return frameId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => frames.delete(id));
  vi.stubGlobal(
    'ResizeObserver',
    class {
      constructor(callback: () => void) {
        resize = callback;
      }
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  Reflect.deleteProperty(HTMLElement.prototype, 'onbeforematch');
  vi.unstubAllGlobals();
});

describe('stable-ID message navigation and scroll coordination', () => {
  it('gives contents placement precedence over prepend before and after delayed media', () => {
    const { rerender } = render(<Harness />);
    const main = screen.getByTestId('messages');
    fireEvent.click(screen.getByText('Preserve prepend'));
    rerender(<Harness messages={['older', ...items]} />);
    act(() => resize());
    fireEvent.click(screen.getByText('Select fourth'));
    const before = main.scrollTop;
    act(() => resize());
    expect(main.scrollTop).toBe(before);
    // A late LoadOlder handler cannot supersede an explicit target either.
    fireEvent.click(screen.getByText('Preserve prepend'));
    flushFrames();
    const fourth = main.querySelector<HTMLElement>(
      '[data-message-id="fourth"]',
    )!;
    expect(fourth.getBoundingClientRect().top).toBe(16);
    rerender(<Harness messages={['older', ...items]} growth={180} />);
    act(() => resize());
    expect(fourth.getBoundingClientRect().top).toBe(16);
    expect(document.activeElement).toBe(fourth);
  });

  it.each(['find', 'latest', 'wheel', 'touchstart', 'pointerdown', 'keydown'])(
    'revokes queued contents placement for explicit %s even after callback delivery',
    (command) => {
      render(<Harness />);
      const main = screen.getByTestId('messages');
      fireEvent.click(screen.getByText('Select second'));
      const stale = [...frames.values()][0];
      if (command === 'find') {
        const content = main.querySelector(
          '[data-message-id="fourth"] [data-message-content]',
        )!;
        act(() =>
          content.dispatchEvent(new Event('beforematch', { bubbles: true })),
        );
      } else if (command === 'latest')
        fireEvent.click(screen.getByText('Latest'));
      else fireEvent(main, new Event(command, { bubbles: true }));
      const top = main.scrollTop;
      const focused = document.activeElement;
      act(() => stale(0));
      expect(main.scrollTop).toBe(top);
      expect(document.activeElement).toBe(focused);
      expect(frames.size).toBe(0);
    },
  );

  it.each(['A-B-A', 'auth', 'unmount'])(
    'invalidates delivered frames across %s without clearing a newer frame',
    (transition) => {
      const view = render(<Harness />);
      const main = screen.getByTestId('messages');
      fireEvent.click(screen.getByText('Select second'));
      const stale = [...frames.values()][0];
      if (transition === 'unmount') {
        view.unmount();
        const top = main.scrollTop;
        act(() => stale(0));
        expect(main.scrollTop).toBe(top);
        expect(frames.size).toBe(0);
        return;
      }
      if (transition === 'auth') generation++;
      else view.rerender(<Harness conversation="two" />);
      view.rerender(<Harness />);
      if (transition === 'auth')
        expect(screen.getByText('Following')).toBeTruthy();
      fireEvent.click(screen.getByText('Select fourth'));
      const top = main.scrollTop;
      const nextFrame = [...frames.keys()][0];
      act(() => stale(0));
      expect(main.scrollTop).toBe(top);
      expect(frames.has(nextFrame)).toBe(true);
      // Resize must still recognize the NEW pending operation.
      act(() => resize());
      expect(main.scrollTop).toBe(top);
      flushFrames();
      expect(document.activeElement?.getAttribute('data-message-id')).toBe(
        'fourth',
      );
    },
  );

  it('revokes a saved reading restoration when a newer contents command is queued', () => {
    const view = render(<Harness />);
    fireEvent.click(screen.getByText('Select fourth'));
    flushFrames();
    fireEvent.scroll(screen.getByTestId('messages'));
    view.rerender(<Harness conversation="two" />);
    view.rerender(<Harness />);
    const savedRestore = [...frames.values()][0];
    expect(savedRestore).toBeDefined();
    fireEvent.click(screen.getByText('Select second'));
    const main = screen.getByTestId('messages');
    const top = main.scrollTop;
    act(() => savedRestore(0));
    expect(main.scrollTop).toBe(top);
    expect(frames.size).toBe(1);
    flushFrames();
    expect(document.activeElement?.getAttribute('data-message-id')).toBe(
      'second',
    );
  });

  it.each(['find', 'latest', 'wheel'])(
    'revokes saved reading restoration for %s',
    (command) => {
      const view = render(<Harness />);
      fireEvent.click(screen.getByText('Select fourth'));
      flushFrames();
      fireEvent.scroll(screen.getByTestId('messages'));
      view.rerender(<Harness conversation="two" />);
      view.rerender(<Harness />);
      const stale = [...frames.values()][0];
      expect(stale).toBeDefined();
      const main = screen.getByTestId('messages');
      if (command === 'find')
        act(() => main.dispatchEvent(new Event('beforematch')));
      else if (command === 'latest')
        fireEvent.click(screen.getByText('Latest'));
      else fireEvent.wheel(main);
      const top = main.scrollTop;
      act(() => stale(0));
      act(() => resize());
      expect(main.scrollTop).toBe(top);
      expect(frames.size).toBe(0);
    },
  );

  it('retains committed navigation authority through StrictMode replay', () => {
    render(
      <React.StrictMode>
        <Harness />
      </React.StrictMode>,
    );
    fireEvent.click(screen.getByText('Select second'));
    flushFrames();
    expect(document.activeElement?.getAttribute('data-message-id')).toBe(
      'second',
    );
  });

  it('pauses before expanding, focuses after layout, and preserves streaming until Latest', () => {
    const { rerender } = render(<Harness />);
    const main = screen.getByTestId('messages');
    fireEvent.click(screen.getByText('Select second'));
    const second = main.querySelector<HTMLElement>(
      '[data-message-id="second"]',
    )!;
    expect(second.querySelector('[hidden]')).toBeNull();
    const beforeFrame = main.scrollTop;
    act(() => resize());
    expect(main.scrollTop).toBe(beforeFrame);
    flushFrames();
    expect(main.scrollTop).toBe(84);
    expect(document.activeElement).toBe(second);
    fireEvent.scroll(main);
    rerender(<Harness stream={'Stream '.repeat(100)} />);
    act(() => resize());
    expect(main.scrollTop).toBe(84);
    expect(screen.getByText('Reading')).toBeTruthy();
    fireEvent.click(screen.getByText('Latest'));
    expect(main.scrollTop).toBe(main.scrollHeight - 400);
    expect(screen.getByText('Following')).toBeTruthy();
  });

  it('keeps the same reading anchor when older messages are prepended', () => {
    const { rerender } = render(<Harness />);
    fireEvent.click(screen.getByText('Select fourth'));
    flushFrames();
    const main = screen.getByTestId('messages');
    fireEvent.scroll(main);
    const article = main.querySelector<HTMLElement>(
      '[data-message-id="fourth"]',
    )!;
    const top = article.getBoundingClientRect().top;
    rerender(<Harness messages={['older', ...items]} />);
    act(() => resize());
    expect(article.getBoundingClientRect().top).toBe(top);
    expect(main.scrollTop).toBe(384);
  });

  it('restores a stable message anchor across conversations with different collapsed heights', () => {
    const { rerender } = render(<Harness />);
    fireEvent.click(screen.getByText('Select second'));
    flushFrames();
    fireEvent.click(screen.getByText('Select fourth'));
    flushFrames();
    const main = screen.getByTestId('messages');
    fireEvent.scroll(main);
    expect(main.scrollTop).toBe(484);
    rerender(<Harness conversation="two" messages={['other']} />);
    rerender(<Harness messages={[]} />);
    rerender(<Harness />);
    flushFrames();
    const fourth = main.querySelector<HTMLElement>(
      '[data-message-id="fourth"]',
    )!;
    expect(fourth.querySelector('[hidden]')).toBeNull();
    expect(fourth.getBoundingClientRect().top).toBe(16);
    expect(screen.getByText('Reading')).toBeTruthy();
  });

  it('cancels pending navigation on conversation switch and leaves browser find in control', () => {
    const { rerender } = render(<Harness />);
    fireEvent.click(screen.getByText('Select second'));
    rerender(<Harness conversation="two" />);
    flushFrames();
    const main = screen.getByTestId('messages');
    expect(document.activeElement?.getAttribute('data-message-id')).not.toBe(
      'second',
    );
    const content = main.querySelector(
      '[data-message-id="second"] [data-message-content]',
    )!;
    act(() => content.dispatchEvent(new Event('beforematch')));
    expect(content.hasAttribute('hidden')).toBe(false);
    const top = main.scrollTop;
    act(() => resize());
    expect(main.scrollTop).toBe(top);
  });
});
