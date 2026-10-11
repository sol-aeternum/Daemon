import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { usePathname } from '../e2e/cache-fixture-navigation';

const originalPush = history.pushState;
const originalReplace = history.replaceState;
afterEach(() => {
  cleanup();
  history.pushState = originalPush;
  history.replaceState = originalReplace;
  originalReplace.call(history, null, '', '/');
  vi.restoreAllMocks();
});

function Path({ name = 'path' }: { name?: string }) {
  return <output data-testid={name}>{usePathname()}</output>;
}

describe('bare cache fixture pathname primitive', () => {
  it('reads the real initial URL and follows push/replace without auth simulation', () => {
    history.replaceState(null, '', '/auth?cache-fixture');
    const view = render(<Path />);
    expect(screen.getByTestId('path').textContent).toBe('/auth');
    act(() => history.pushState({ fictional: 1 }, '', '/settings/devices?x=1'));
    expect(screen.getByTestId('path').textContent).toBe('/settings/devices');
    expect(history.state).toEqual({ fictional: 1 });
    act(() => history.replaceState(null, '', '/setup#ignored'));
    expect(screen.getByTestId('path').textContent).toBe('/setup');
    view.unmount();
    expect(history.pushState).toBe(originalPush);
    expect(history.replaceState).toBe(originalReplace);
  });

  it('observes popstate and preserves remaining subscribers until final cleanup', () => {
    const first = render(<Path name="first" />);
    const second = render(<Path name="second" />);
    act(() => {
      originalReplace.call(history, null, '', '/auth');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(screen.getByTestId('first').textContent).toBe('/auth');
    expect(screen.getByTestId('second').textContent).toBe('/auth');
    first.unmount();
    act(() => history.replaceState(null, '', '/'));
    expect(screen.getByTestId('second').textContent).toBe('/');
    second.unmount();
    expect(history.replaceState).toBe(originalReplace);
  });

  it('retains URL validation failures and does not replace later history owners', () => {
    const view = render(<Path />);
    expect(() =>
      history.pushState(null, '', 'https://foreign.invalid/'),
    ).toThrow();
    expect(screen.getByTestId('path').textContent).toBe('/');
    const laterPush = vi.fn();
    history.pushState = laterPush;
    view.unmount();
    expect(history.pushState).toBe(laterPush);
  });
});
