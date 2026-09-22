import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CollapsibleMessage } from '../components/CollapsibleMessage';

describe('CollapsibleMessage', () => {
  it('unmounts older rich content until requested and can collapse it again', () => {
    render(
      <CollapsibleMessage
        messageId="old"
        title="Daemon · message 1"
        preview="Summary"
        collapsible
      >
        <button>Download artifact</button>
      </CollapsibleMessage>,
    );
    expect(screen.getByText('Summary')).toBeTruthy();
    expect(screen.queryByText('Download artifact')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
    expect(screen.getByText('Download artifact')).toBeTruthy();
    expect(
      screen
        .getByRole('button', { name: 'Show less' })
        .getAttribute('aria-expanded'),
    ).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
    expect(screen.queryByText('Download artifact')).toBeNull();
  });

  it('keeps recent messages expanded and collapses them when they age out', () => {
    const message = (collapsible: boolean) => (
      <CollapsibleMessage
        messageId="recent"
        title="You · message 2"
        preview="Preview"
        collapsible={collapsible}
      >
        <p>Complete response</p>
      </CollapsibleMessage>
    );
    const { rerender } = render(message(false));
    expect(screen.getByText('Complete response')).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
    rerender(message(true));
    expect(screen.queryByText('Complete response')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
    rerender(message(true));
    expect(screen.getByText('Complete response')).toBeTruthy();
  });

  it('provides a usable preview for messages containing only tool output', () => {
    render(
      <CollapsibleMessage
        messageId="tools"
        title="Daemon · message 3"
        preview=""
        collapsible
      >
        <p>Generated image</p>
      </CollapsibleMessage>,
    );
    expect(screen.getByText('Tool activity or attachments')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
    expect(screen.getByText('Generated image')).toBeTruthy();
  });
});
