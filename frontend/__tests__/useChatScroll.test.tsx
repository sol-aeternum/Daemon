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
          <div ref={messagesEndRef} />
        </div>
      </main>
      <span>{isScrolledUp ? 'Reading history' : 'Following'}</span>
      <button onClick={jumpToLatest}>Jump</button>
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
afterEach(() => vi.unstubAllGlobals());

describe('useChatScroll', () => {
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
