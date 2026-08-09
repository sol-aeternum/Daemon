import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChatInputBar } from '../components/ChatInputBar';

vi.mock('../components/ModelSelector', () => ({
  ModelSelector: () => <div data-testid="model-selector" />,
}));
vi.mock('../components/MicButton', () => ({
  MicButton: () => <button type="button">Microphone</button>,
}));

const baseProps = {
  selectedModel: 'auto',
  onSelectModel: vi.fn(),
  isRecording: false,
  isConnecting: false,
  startRecording: vi.fn(async () => {}),
  stopRecording: vi.fn(),
  input: 'hello',
  onInputChange: vi.fn(),
  onSubmit: vi.fn(),
  onStop: vi.fn(),
};

describe('ChatInputBar generation controls', () => {
  it('renders Send while idle and Stop while loading', () => {
    const onStop = vi.fn();
    const { rerender } = render(
      <ChatInputBar {...baseProps} onStop={onStop} isLoading={false} />,
    );

    expect(
      screen.queryByRole('button', { name: 'Send message' }),
    ).not.toBeNull();
    expect(
      screen.queryByRole('button', { name: 'Stop generating' }),
    ).toBeNull();

    rerender(<ChatInputBar {...baseProps} onStop={onStop} isLoading />);

    expect(screen.queryByRole('button', { name: 'Send message' })).toBeNull();
    const stopButton = screen.getByRole('button', { name: 'Stop generating' });
    fireEvent.click(stopButton);
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Esc').tagName).toBe('KBD');
  });
});
