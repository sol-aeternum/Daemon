import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SuggestionSourceContext } from '../components/SuggestionSourceContext';

describe('inspectable bound source data', () => {
  it('shows persisted excerpts as text, not executable HTML or prompt instructions', () => {
    const { container } = render(
      <SuggestionSourceContext
        context={{
          sources: [
            {
              conversation_id: 'source',
              title: 'Release plan',
              messages: [
                { id: 'm', role: 'user', content: '<script>secret()</script>' },
              ],
            },
          ],
        }}
      />,
    );
    expect(screen.getByText('Context used · Release plan')).toBeTruthy();
    expect(screen.getByText('<script>secret()</script>')).toBeTruthy();
    expect(container.querySelector('script')).toBeNull();
  });

  it('does not render missing or encrypted/unusable context', () => {
    const { container } = render(
      <SuggestionSourceContext context="ciphertext" />,
    );
    expect(container.childElementCount).toBe(0);
  });
});
