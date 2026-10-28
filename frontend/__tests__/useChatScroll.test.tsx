import React, { useLayoutEffect } from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatScroll } from '../hooks/useChatScroll';

let resize: () => void;
let contentHeight = 2000;
const scrollTo = vi.fn(function (this: HTMLElement, options: ScrollToOptions) {
  this.scrollTop = Math.min(options.top ?? 0, contentHeight - 500);
});
const messages = ['one'];

function Harness({
  conversationId = 'one',
  items = messages,
  loading = false,
}: {
  conversationId?: string;
  items?: string[];
  loading?: boolean;
}) {
  const {
    scrollContainerRef,
    messagesEndRef,
    isScrolledUp,
    onScroll,
    jumpToLatest,
    preservePrepend,
  } = useChatScroll({ conversationId, messages: items, isLoading: loading });
  useLayoutEffect(() => {
    const container = scrollContainerRef.current!;
    Object.defineProperties(container, {
      scrollHeight: { configurable: true, get: () => contentHeight },
      clientHeight: { configurable: true, value: 500 },
      scrollTo: { configurable: true, value: scrollTo },
    });
  }, [scrollContainerRef]);
  return (
    <>
      <main data-testid="scroll" ref={scrollContainerRef} onScroll={onScroll}>
        <div>
          {items.map((id) => (
            <article key={id} data-message-id={id}>
              {id}
            </article>
          ))}
          <div ref={messagesEndRef} />
        </div>
      </main>
      <span>{isScrolledUp ? 'Reading history' : 'Following'}</span>
      <button onClick={jumpToLatest}>Jump</button>
      <button onClick={preservePrepend}>Older</button>
    </>
  );
}

beforeEach(() => {
  contentHeight = 2000;
  scrollTo.mockClear();
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
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useChatScroll', () => {
  it('preserves a real message anchor through prepend and delayed media without bottom-follow', () => {
    let offsets: Record<string, number> = { one: 300, two: 600, three: 900 };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: HTMLElement) {
        const top = this.dataset.messageId
          ? offsets[this.dataset.messageId] -
            screen.getByTestId('scroll').scrollTop
          : 0;
        return { top, bottom: top + 200, height: 200 } as DOMRect;
      },
    );
    const view = render(<Harness items={['one', 'two', 'three']} />);
    const container = screen.getByTestId('scroll');
    container.scrollTop = 400;
    fireEvent.scroll(container);
    fireEvent.click(screen.getByRole('button', { name: 'Older' }));
    scrollTo.mockClear();
    offsets = { older: 100, one: 600, two: 900, three: 1200 };
    contentHeight = 2300;
    view.rerender(<Harness items={['older', 'one', 'two', 'three']} />);
    expect(container.scrollTop).toBe(700);
    offsets = { older: 100, one: 750, two: 1050, three: 1350 };
    contentHeight = 2450;
    act(() => resize());
    expect(container.scrollTop).toBe(850);
    expect(scrollTo).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Jump' }));
    expect(container.scrollTop).toBe(1950);
  });

  it('uses a surviving successor if the anchor is deleted and releases on explicit user scroll', () => {
    let offsets: Record<string, number> = { one: 300, two: 600 };
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(
      function (this: HTMLElement) {
        const top = this.dataset.messageId
          ? offsets[this.dataset.messageId] -
            screen.getByTestId('scroll').scrollTop
          : 0;
        return { top, bottom: top + 200, height: 200 } as DOMRect;
      },
    );
    const view = render(<Harness items={['one', 'two']} />);
    const container = screen.getByTestId('scroll');
    container.scrollTop = 400;
    fireEvent.scroll(container);
    fireEvent.click(screen.getByRole('button', { name: 'Older' }));
    offsets = { older: 100, two: 900 };
    contentHeight = 2300;
    view.rerender(<Harness items={['older', 'two']} />);
    expect(container.scrollTop).toBe(700);
    fireEvent.wheel(container);
    container.scrollTop = 200;
    fireEvent.scroll(container);
    offsets.two += 150;
    contentHeight = 2450;
    act(() => resize());
    expect(container.scrollTop).toBe(200);
  });
  it('follows new content and delayed media when at the bottom', () => {
    const { rerender } = render(<Harness />);
    expect(screen.getByTestId('scroll').scrollTop).toBe(1500);
    contentHeight = 2200;
    rerender(<Harness items={['one', 'streaming']} loading />);
    expect(screen.getByTestId('scroll').scrollTop).toBe(1700);
    contentHeight = 2500;
    act(() => resize());
    expect(screen.getByTestId('scroll').scrollTop).toBe(2000);
  });

  it('preserves reading position when streaming starts and resumes after jumping', () => {
    const { rerender } = render(<Harness />);
    const container = screen.getByTestId('scroll');
    container.scrollTop = 400;
    fireEvent.scroll(container);
    expect(screen.getByText('Reading history')).toBeTruthy();
    contentHeight = 2400;
    rerender(<Harness items={['one', 'reply']} loading />);
    act(() => resize());
    expect(container.scrollTop).toBe(400);
    fireEvent.click(screen.getByRole('button', { name: 'Jump' }));
    expect(container.scrollTop).toBe(1900);
    expect(screen.getByText('Following')).toBeTruthy();
    contentHeight = 2600;
    rerender(<Harness items={['one', 'longer reply']} loading />);
    expect(container.scrollTop).toBe(2100);
  });

  it('resumes following when manually scrolled to the bottom', () => {
    render(<Harness loading />);
    const container = screen.getByTestId('scroll');
    container.scrollTop = 100;
    fireEvent.scroll(container);
    container.scrollTop = 1500;
    fireEvent.scroll(container);
    contentHeight = 2400;
    act(() => resize());
    expect(container.scrollTop).toBe(1900);
    expect(screen.getByText('Following')).toBeTruthy();
  });

  it('opens another conversation at its latest message', () => {
    const { rerender } = render(<Harness />);
    const container = screen.getByTestId('scroll');
    container.scrollTop = 100;
    fireEvent.scroll(container);
    rerender(<Harness conversationId="two" items={['other conversation']} />);
    expect(container.scrollTop).toBe(1500);
    expect(screen.getByText('Following')).toBeTruthy();
  });
});
