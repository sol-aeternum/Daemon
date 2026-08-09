import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { StopButton } from '../components/StopButton';

describe('StopButton', () => {
  it('renders an accessible stop control and invokes the callback', () => {
    const onStop = vi.fn();
    render(<StopButton onStop={onStop} />);

    const button = screen.getByRole('button', { name: 'Stop generating' });
    expect(button.className).toContain('h-11');
    expect(button.className).toContain('w-11');
    expect(button.className).toContain('bg-[var(--color-status-stop)]');
    expect(button.className).toContain('focus-visible:ring-2');

    fireEvent.click(button);
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
