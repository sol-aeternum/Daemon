import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { CollapsibleMessage } from '../components/CollapsibleMessage';

function contentOf(text: string) {
  const element = screen.getByText(text).closest('[id]');
  if (!element) throw new Error(`No content region for ${text}`);
  return element;
}

describe('CollapsibleMessage', () => {
  describe('with hidden="until-found" support', () => {
    // jsdom has no find-in-page; expose the feature-detection hook.
    beforeEach(() => {
      Object.defineProperty(HTMLElement.prototype, 'onbeforematch', {
        configurable: true,
        value: null,
      });
    });
    afterEach(() => {
      Reflect.deleteProperty(HTMLElement.prototype, 'onbeforematch');
    });

    it('keeps collapsed content in the DOM, hidden until found', () => {
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
      const content = contentOf('Download artifact');
      expect(content.getAttribute('hidden')).toBe('until-found');
      fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
      expect(content.hasAttribute('hidden')).toBe(false);
      expect(
        screen
          .getByRole('button', { name: 'Show less' })
          .getAttribute('aria-expanded'),
      ).toBe('true');
      fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
      expect(content.getAttribute('hidden')).toBe('until-found');
    });

    it('expands when browser find matches collapsed text', () => {
      render(
        <CollapsibleMessage
          messageId="found"
          title="You · message 2"
          preview="Preview"
          collapsible
        >
          <p>Searchable detail</p>
        </CollapsibleMessage>,
      );
      const content = contentOf('Searchable detail');
      act(() => {
        content.dispatchEvent(new Event('beforematch'));
      });
      expect(content.hasAttribute('hidden')).toBe(false);
      expect(screen.getByRole('button', { name: 'Show less' })).toBeTruthy();
    });

    it('keeps short or recent messages expanded without a toggle', () => {
      render(
        <CollapsibleMessage
          messageId="short"
          title="You · message 3"
          preview="Short"
          collapsible={false}
        >
          <p>Complete response</p>
        </CollapsibleMessage>,
      );
      expect(contentOf('Complete response').hasAttribute('hidden')).toBe(false);
      expect(screen.queryByRole('button')).toBeNull();
    });
  });

  it('never collapses where hidden="until-found" is unsupported', () => {
    render(
      <CollapsibleMessage
        messageId="fallback"
        title="Daemon · message 4"
        preview="Summary"
        collapsible
      >
        <p>Always visible</p>
      </CollapsibleMessage>,
    );
    expect(contentOf('Always visible').hasAttribute('hidden')).toBe(false);
    expect(screen.queryByRole('button')).toBeNull();
  });
});
