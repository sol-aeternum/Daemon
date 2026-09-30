import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import MarkdownMessage from '../components/MarkdownMessage';
import type { ChatEvent } from '../lib/events';
import { buildMessageCitationSources } from '../lib/messageSources';

describe('buildMessageCitationSources', () => {
  it('extracts sources from search and fetch results only', () => {
    const events: ChatEvent[] = [
      {
        type: 'tool_call',
        name: 'web_search',
        arguments: {},
        tool_call_id: 'a',
      },
      {
        type: 'tool_result',
        name: 'web_search',
        result: JSON.stringify({
          results: [
            {
              title: 'MDN',
              url: 'https://developer.mozilla.org/en-US/docs/Web/HTTP/Authentication',
            },
            {
              title: 'dup',
              url: 'https://DEVELOPER.mozilla.org/en-US/docs/Web/HTTP/Authentication',
            },
            { title: 'bad', url: 'javascript:alert(1)' },
            { title: 'bad2', url: 'not a url' },
          ],
        }),
        tool_call_id: 'a',
      },
      {
        type: 'tool_result',
        name: 'web_fetch',
        result: JSON.stringify({
          error: 'fetch failed',
          url: 'https://guessed.example.com/should-not-appear',
        }),
        tool_call_id: 'b',
      },
      {
        type: 'tool_result',
        name: 'web_fetch',
        result: JSON.stringify({
          snapshot_id: 's',
          url: '/relative',
          final_url: 'https://final.example.com/page',
          title: 'Final',
        }),
        tool_call_id: 'c',
      },
    ];
    const sources = buildMessageCitationSources(events);
    expect(sources).toHaveLength(2);
    expect(sources[0].domain).toBe('developer.mozilla.org');
    expect(sources[1].domain).toBe('final.example.com');
  });

  it('never crashes on malformed results', () => {
    const events: ChatEvent[] = [
      { type: 'tool_result', name: 'web_search', result: 'not json' },
      { type: 'tool_result', name: 'web_search', result: 12345 },
      { type: 'tool_result', name: 'web_search', result: '[1,2,3]' },
    ];
    expect(buildMessageCitationSources(events)).toEqual([]);
  });

  it('retains both actually returned URLs after a redirect', () => {
    expect(
      buildMessageCitationSources([
        {
          type: 'tool_result',
          name: 'web_fetch',
          result: {
            url: 'https://original.example/page',
            final_url: 'https://final.example/page',
            content: 'Read content',
          },
        },
      ]).map((source) => source.url),
    ).toEqual(['https://final.example/page', 'https://original.example/page']);
  });
});

describe('inline citation pills (MarkdownMessage)', () => {
  const sources = [
    {
      url: 'https://developer.mozilla.org/en-US/docs/Web/HTTP/Authentication',
      domain: 'developer.mozilla.org',
      title: 'MDN HTTP auth',
    },
  ];
  const content =
    'Read the [HTTP auth guide](https://developer.mozilla.org/en-US/docs/Web/HTTP/Authentication) and an [unrelated link](https://example.com/x).';

  it('restyles only links matching returned sources; ordinary links remain', () => {
    render(React.createElement(MarkdownMessage, { content, sources }));
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(2);
    const pill = links[0];
    expect(pill.textContent).toContain('developer.mozilla.org');
    expect(pill.getAttribute('href')).toBe(
      'https://developer.mozilla.org/en-US/docs/Web/HTTP/Authentication',
    );
    // Second link is an ordinary link, not a pill.
    expect(links[1].textContent).toContain('unrelated link');
    expect(links[1].className || '').not.toContain('rounded-full');
  });

  it('matches same-page URLs ignoring hash only', () => {
    render(
      React.createElement(MarkdownMessage, {
        content:
          'See [details](https://developer.mozilla.org/en-US/docs/Web/HTTP/Authentication#section).',
        sources,
      }),
    );
    const pill = screen.getByRole('link');
    expect(pill.textContent).toContain('developer.mozilla.org');
    expect(pill.getAttribute('href')).toContain('#section');
  });

  it('does NOT conflate case-different paths or queries', () => {
    render(
      React.createElement(MarkdownMessage, {
        content:
          'See [different page](https://developer.mozilla.org/en-US/docs/web/http/authentication).',
        sources,
      }),
    );
    const pill = screen.getByRole('link');
    expect(pill.textContent).toContain('different page');
    expect(pill.textContent).not.toContain('developer.mozilla.org');
  });

  it('renders no citation pills when sources absent', () => {
    render(React.createElement(MarkdownMessage, { content }));
    const links = screen.getAllByRole('link');
    expect(links).toHaveLength(2);
    expect(links[0].textContent).toContain('HTTP auth guide');
  });
});
