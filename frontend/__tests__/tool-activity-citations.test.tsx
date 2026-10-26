import React, { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import MarkdownMessage from '../components/MarkdownMessage';
import type { ChatEvent } from '../lib/events';
import { buildMessageCitationSources } from '../lib/messageSources';
import MarkdownRenderer from '../src/components/MarkdownRenderer';
import { ModelSelector } from '../components/ModelSelector';
import { CollapsibleMessage } from '../components/CollapsibleMessage';

const parseWork = vi.hoisted(() => ({ inputs: [] as string[] }));
// Delegate the entire real library implementation/output. The installed sync
// Markdown entry point calls processor.parse for each invocation; no fake AST,
// renderer or memo implementation stands in for the expensive operation.
vi.mock('react-markdown', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-markdown')>();
  return {
    ...actual,
    default: (props: Parameters<typeof actual.default>[0]) => {
      parseWork.inputs.push(String(props.children));
      return actual.default(props);
    },
  };
});

beforeEach(() => {
  parseWork.inputs = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      Response.json({
        auto: { id: 'auto', name: 'Auto', tagline: 'Automatic', icon: 'zap' },
        featured: [
          {
            id: 'fixture-model',
            name: 'Fixture model',
            tagline: 'Fictional',
            badges: [],
          },
        ],
      }),
    ),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('stable Markdown work discriminator', () => {
  const body =
    'Stable **answer** and [returned source](https://source.example/page).';
  const events: ChatEvent[] = [
    {
      type: 'tool_result',
      name: 'web_search',
      result: {
        results: [{ url: 'https://source.example/page', title: 'Source' }],
      },
    },
  ];

  function ParentState() {
    const [draft, setDraft] = useState('');
    const [model, setModel] = useState('auto');
    const [details, setDetails] = useState(false);
    const [tools, setTools] = useState(true);
    // This is the actual helper used inside ChatContent's message map: a fresh
    // source array/object list each render, not manually stable fixture identity.
    const sources = buildMessageCitationSources(events);
    return (
      <>
        <input
          aria-label="Composer state"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <ModelSelector selected={model} onSelect={setModel} />
        <button onClick={() => setDetails((v) => !v)}>Toggle details</button>
        <button onClick={() => setTools((v) => !v)}>Toggle tool state</button>
        <output aria-label="Parent state">
          {model}/{details ? 'details' : 'closed'}/{tools ? 'tools' : 'hidden'}
        </output>
        <MarkdownMessage content={body} sources={sources} />
      </>
    );
  }

  it('does not repeat stable parsing for unrelated parent state; a real local menu is a negative control', async () => {
    render(<ParentState />);
    const initial = parseWork.inputs.length;
    expect(initial).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: 'Model: Auto' }));
    await screen.findByRole('button', { name: /Fixture model/ });
    expect(
      parseWork.inputs,
      'ModelSelector local open state does not rerender the parent',
    ).toHaveLength(initial);
    fireEvent.click(screen.getByRole('button', { name: /Fixture model/ }));
    const afterModel = parseWork.inputs.length;
    fireEvent.change(screen.getByRole('textbox', { name: 'Composer state' }), {
      target: { value: 'new draft' },
    });
    const afterComposer = parseWork.inputs.length;
    fireEvent.click(screen.getByRole('button', { name: 'Toggle details' }));
    const afterDetails = parseWork.inputs.length;
    fireEvent.click(screen.getByRole('button', { name: 'Toggle tool state' }));
    console.info(
      'STABLE-WORK-DISCRIMINATOR',
      JSON.stringify({
        initial,
        afterModel,
        afterComposer,
        afterDetails,
        afterTools: parseWork.inputs.length,
      }),
    );
    expect(screen.getByLabelText('Parent state').textContent).toBe(
      'fixture-model/details/hidden',
    );
    expect(
      (
        screen.getByRole('textbox', {
          name: 'Composer state',
        }) as HTMLInputElement
      ).value,
    ).toBe('new draft');
    expect(screen.getByRole('link', { name: /source.example/ })).toBeTruthy();
    expect(parseWork.inputs).toHaveLength(initial);
  });

  it('streams and edits text without freezing the body or links', () => {
    const view = render(<MarkdownMessage content="First partial" />);
    const initial = parseWork.inputs.length;
    view.rerender(<MarkdownMessage content="First partial **completed**" />);
    expect(screen.getByText('completed').tagName).toBe('STRONG');
    view.rerender(
      <MarkdownMessage content="Edited [link](https://edit.example/)" />,
    );
    expect(
      screen.getByRole('link', { name: 'link' }).getAttribute('href'),
    ).toBe('https://edit.example/');
    expect(screen.queryByText('completed')).toBeNull();
    expect(parseWork.inputs.length).toBeGreaterThan(initial);
  });

  it('same text still updates all source metadata, ordering and provenance removal', () => {
    const source = {
      url: 'https://source.example/page',
      domain: 'first.example',
      title: 'First',
    };
    const view = render(<MarkdownMessage content={body} sources={[source]} />);
    expect(screen.getByRole('link').textContent).toContain('first.example');
    let count = parseWork.inputs.length;
    view.rerender(
      <MarkdownMessage
        content={body}
        sources={[{ ...source, domain: 'updated.example' }]}
      />,
    );
    expect(screen.getByRole('link').textContent).toContain('updated.example');
    expect(parseWork.inputs.length).toBeGreaterThan(count);
    count = parseWork.inputs.length;
    view.rerender(
      <MarkdownMessage
        content={body}
        sources={[
          { ...source, domain: 'updated.example', title: 'Updated metadata' },
        ]}
      />,
    );
    expect(
      parseWork.inputs.length,
      'title metadata is an input, even if not currently visible',
    ).toBeGreaterThan(count);
    view.rerender(
      <MarkdownMessage
        content={body}
        sources={[{ ...source, domain: 'winner.example' }, source]}
      />,
    );
    expect(screen.getByRole('link').textContent).toContain('winner.example');
    view.rerender(
      <MarkdownMessage
        content={body}
        sources={[source, { ...source, domain: 'winner.example' }]}
      />,
    );
    expect(screen.getByRole('link').textContent).toContain('first.example');
    view.rerender(<MarkdownMessage content={body} sources={[]} />);
    expect(screen.getByRole('link').textContent).toBe('returned source');
    expect(screen.getByRole('link').getAttribute('rel')).toBe(
      'noopener noreferrer',
    );
  });

  it('fresh equal data skips work while wrapper style and per-mount lifetime remain live', () => {
    const source = {
      url: 'https://source.example/page',
      domain: 'source.example',
      title: 'Source',
    };
    const view = render(
      <MarkdownRenderer content={body} sources={[source]} compact />,
    );
    const initial = parseWork.inputs.length;
    view.rerender(
      <MarkdownRenderer
        content={body}
        sources={[{ ...source }]}
        className="changed-wrapper"
      />,
    );
    expect(view.container.firstElementChild?.className).toContain(
      'changed-wrapper',
    );
    expect(view.container.firstElementChild?.className).not.toContain(
      'prose-sm',
    );
    expect(view.container.textContent).toContain('Stable answer');
    expect(parseWork.inputs).toHaveLength(initial);
    view.unmount();
    render(<MarkdownRenderer content={body} sources={[{ ...source }]} />);
    expect(
      parseWork.inputs.length,
      'cache must not outlive its mounted subtree',
    ).toBeGreaterThan(initial);
  });
});

describe('cached Markdown correctness boundaries', () => {
  const body =
    'Stable **answer** and [returned source](https://source.example/page).';
  it('snapshots metadata values rather than trusting a retained mutable array identity', () => {
    const sources = [
      { url: 'https://source.example/page', domain: 'original.example' },
    ];
    const view = render(<MarkdownMessage content={body} sources={sources} />);
    const initial = parseWork.inputs.length;
    sources[0].domain = 'changed.example';
    view.rerender(<MarkdownMessage content={body} sources={sources} />);
    expect(screen.getByRole('link').textContent).toContain('changed.example');
    expect(parseWork.inputs.length).toBeGreaterThan(initial);
  });

  it('keeps child-owned artifact callbacks/state live inside the cached parse subtree', () => {
    const content = '```html-interactive\n<div>Artifact body</div>\n```';
    const view = render(<MarkdownRenderer content={content} />);
    expect(view.container.querySelector('iframe')).not.toBeNull();
    const initial = parseWork.inputs.length;
    fireEvent.click(screen.getByRole('button', { name: 'Collapse artifact' }));
    expect(view.container.querySelector('iframe')).toBeNull();
    view.rerender(
      <MarkdownRenderer content={content} className="new-wrapper" />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Expand artifact' }));
    expect(view.container.querySelector('iframe')).not.toBeNull();
    expect(parseWork.inputs).toHaveLength(initial);
  });

  it('retains searchable hidden Markdown DOM and real beforematch/expansion behavior', () => {
    const prior = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'onbeforematch',
    );
    Object.defineProperty(HTMLElement.prototype, 'onbeforematch', {
      configurable: true,
      value: null,
    });
    try {
      const view = render(
        <CollapsibleMessage
          messageId="stable-hidden"
          title="Daemon message"
          preview="Preview"
          collapsible
        >
          <MarkdownMessage content="Full searchable **hidden answer**" />
        </CollapsibleMessage>,
      );
      const hidden = view.container.querySelector('[hidden="until-found"]')!;
      expect(hidden.textContent).toContain('Full searchable hidden answer');
      fireEvent(hidden, new Event('beforematch'));
      expect(hidden.hasAttribute('hidden')).toBe(false);
      fireEvent.click(screen.getByRole('button', { name: 'Show less' }));
      expect(hidden.getAttribute('hidden')).toBe('until-found');
      expect(hidden.textContent).toContain('Full searchable hidden answer');
    } finally {
      if (prior)
        Object.defineProperty(HTMLElement.prototype, 'onbeforematch', prior);
      else Reflect.deleteProperty(HTMLElement.prototype, 'onbeforematch');
    }
  });
});

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
