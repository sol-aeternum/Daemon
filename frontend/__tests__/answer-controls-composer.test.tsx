import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { ChatInputBar } from '../components/ChatInputBar';
import {
  DEFAULT_ANSWER_CONTROLS,
  type AnswerControls,
} from '../lib/answerControls';

vi.mock('../components/ModelSelector', () => ({ ModelSelector: () => null }));
afterEach(cleanup);

it('offers independent accessible per-turn Balanced + Auto controls, including Brief + Research', () => {
  const submitted = vi.fn();
  function Composer() {
    const [controls, setControls] = useState<AnswerControls>(
      DEFAULT_ANSWER_CONTROLS,
    );
    return (
      <ChatInputBar
        selectedModel="auto"
        onSelectModel={vi.fn()}
        controls={controls}
        onControlsChange={setControls}
        input="Question"
        onInputChange={vi.fn()}
        onSubmit={() => submitted(controls)}
        isLoading={false}
        onStop={vi.fn()}
        isRecording={false}
        isConnecting={false}
        startRecording={vi.fn()}
        stopRecording={vi.fn()}
      />
    );
  }
  render(<Composer />);
  expect(
    (
      screen.getByRole('combobox', {
        name: 'Answer detail',
      }) as HTMLSelectElement
    ).value,
  ).toBe('balanced');
  expect(
    (
      screen.getByRole('combobox', {
        name: 'Research effort',
      }) as HTMLSelectElement
    ).value,
  ).toBe('auto');
  fireEvent.change(screen.getByRole('combobox', { name: 'Answer detail' }), {
    target: { value: 'brief' },
  });
  fireEvent.change(screen.getByRole('combobox', { name: 'Research effort' }), {
    target: { value: 'research' },
  });
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
  expect(submitted).toHaveBeenCalledWith({
    answer_detail: 'brief',
    research_effort: 'research',
  });
});
