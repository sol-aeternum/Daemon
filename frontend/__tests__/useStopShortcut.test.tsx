import { cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useStopShortcut } from '../hooks/useStopShortcut';

function Harness({ active, onStop }: { active: boolean; onStop: () => void }) {
  useStopShortcut({ active, onStop });
  return null;
}

afterEach(() => cleanup());

describe('useStopShortcut', () => {
  it('stops an active generation on Escape', () => {
    const onStop = vi.fn();
    render(<Harness active onStop={onStop} />);

    fireEvent.keyDown(window, { key: 'Escape' });

    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('does nothing while generation is idle', () => {
    const onStop = vi.fn();
    render(<Harness active={false} onStop={onStop} />);

    fireEvent.keyDown(window, { key: 'Escape' });

    expect(onStop).not.toHaveBeenCalled();
  });

  it('leaves Escape to an open lightbox or dropdown', () => {
    const onStop = vi.fn();
    const { rerender } = render(<Harness active onStop={onStop} />);
    const blocker = document.createElement('div');
    blocker.setAttribute('data-stop-shortcut-block', 'true');
    document.body.appendChild(blocker);

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onStop).not.toHaveBeenCalled();

    blocker.remove();
    rerender(<Harness active onStop={onStop} />);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('ignores mounted blockers that are hidden', () => {
    const onStop = vi.fn();
    render(<Harness active onStop={onStop} />);
    const blocker = document.createElement('div');
    blocker.setAttribute('data-stop-shortcut-block', 'true');
    blocker.hidden = true;
    document.body.appendChild(blocker);

    fireEvent.keyDown(window, { key: 'Escape' });

    expect(onStop).toHaveBeenCalledTimes(1);
    blocker.remove();
  });

  it('ignores blockers hidden by an ancestor', () => {
    const onStop = vi.fn();
    render(<Harness active onStop={onStop} />);
    const hiddenParent = document.createElement('div');
    hiddenParent.style.display = 'none';
    const blocker = document.createElement('div');
    blocker.setAttribute('data-stop-shortcut-block', 'true');
    hiddenParent.appendChild(blocker);
    document.body.appendChild(hiddenParent);

    fireEvent.keyDown(window, { key: 'Escape' });

    expect(onStop).toHaveBeenCalledTimes(1);
    hiddenParent.remove();
  });
});
