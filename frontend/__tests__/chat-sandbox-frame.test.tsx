import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import MarkdownMessage from '../components/MarkdownMessage';
import { SandboxedHtmlFrame } from '../components/chat/SandboxedHtmlFrame';
import { GET as frameDocument } from '../app/api/previews/html/route';
import {
  HTML_PREVIEW_CONTENT_HEIGHT_TYPE,
  HTML_PREVIEW_FRAME_PATH,
  HTML_PREVIEW_HEIGHT_TYPE,
  HTML_PREVIEW_MAX_HEIGHT,
  HTML_PREVIEW_MESSAGE_TYPE,
  readHtmlPreviewHeight,
} from '../lib/htmlPreviewFrame';
import {
  downloadTextFile,
  slugifyFilename,
  textFileMime,
  textFileName,
} from '../lib/textDownload';

const page =
  '<!doctype html>\n<html><head><title>Sales Dashboard</title></head>' +
  '<body><input type="range"><script>document.body.dataset.ran = "1";</script></body></html>';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('text downloads', () => {
  it('builds safe, readable filenames per kind', () => {
    expect(slugifyFilename('  Q3 Sales — Café Report! ')).toBe(
      'q3-sales-cafe-report',
    );
    expect(slugifyFilename('../../etc/passwd')).toBe('etc-passwd');
    expect(slugifyFilename('???')).toBe('download');
    expect(textFileName('Dashboard', 'html')).toBe('dashboard.html');
    expect(textFileName('Dashboard', 'html', { version: 3 })).toBe(
      'dashboard-v3.html',
    );
    expect(textFileName('Solver', 'code', { language: 'Python' })).toBe(
      'solver.py',
    );
    expect(textFileName('Solver', 'code', { language: 'brainfuck' })).toBe(
      'solver.txt',
    );
    expect(textFileMime('svg')).toBe('image/svg+xml;charset=utf-8');
  });

  it('downloads through a detached anchor and revokes the object URL', () => {
    vi.useFakeTimers();
    const create = vi.fn(() => 'blob:download');
    const revoke = vi.fn();
    vi.stubGlobal(
      'URL',
      Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }),
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        expect(this.download).toBe('page.html');
        expect(this.href).toBe('blob:download');
      });
    downloadTextFile('<p>x</p>', 'page.html', textFileMime('html'));
    expect(click).toHaveBeenCalledOnce();
    expect(document.querySelector('a[download]')).toBeNull();
    vi.runAllTimers();
    expect(revoke).toHaveBeenCalledWith('blob:download');
    vi.useRealTimers();
  });
});

describe('preview frame protocol', () => {
  it('accepts only bounded, well-formed height messages', () => {
    expect(
      readHtmlPreviewHeight({ type: HTML_PREVIEW_HEIGHT_TYPE, height: 321.2 }),
    ).toBe(322);
    expect(
      readHtmlPreviewHeight({ type: HTML_PREVIEW_HEIGHT_TYPE, height: 1e9 }),
    ).toBe(HTML_PREVIEW_MAX_HEIGHT);
    for (const bad of [
      null,
      'height',
      { type: 'artifact-height', height: 10 },
      { type: HTML_PREVIEW_HEIGHT_TYPE, height: -1 },
      { type: HTML_PREVIEW_HEIGHT_TYPE, height: Number.NaN },
      { type: HTML_PREVIEW_HEIGHT_TYPE, height: '10' },
    ]) {
      expect(readHtmlPreviewHeight(bad)).toBeNull();
    }
  });

  it('serves a frame document that relays only clamped content heights', async () => {
    const response = frameDocument();
    expect(response.headers.get('cache-control')).toBe('no-store');
    const html = await response.text();
    expect(html).toContain('sandbox="allow-scripts"');
    expect(html).not.toContain('allow-same-origin');
    expect(html).toContain(JSON.stringify(HTML_PREVIEW_CONTENT_HEIGHT_TYPE));
    expect(html).toContain(
      `Math.min(Math.ceil(payload.height), ${HTML_PREVIEW_MAX_HEIGHT})`,
    );
    expect(html).toContain('event.source === previewFrame.contentWindow');
    expect(html).toContain('event.source !== window.parent');
  });
});

describe('SandboxedHtmlFrame', () => {
  it('loads the isolated frame route and posts the content to it', () => {
    render(<SandboxedHtmlFrame content={page} title="Sales Dashboard" />);
    const frame = screen.getByTitle('Sales Dashboard') as HTMLIFrameElement;
    expect(frame.getAttribute('src')).toBe(HTML_PREVIEW_FRAME_PATH);
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame.hasAttribute('srcdoc')).toBe(false);
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    fireEvent.load(frame);
    expect(post).toHaveBeenCalledWith(
      expect.objectContaining({
        type: HTML_PREVIEW_MESSAGE_TYPE,
        content: page,
        title: 'Sales Dashboard',
      }),
      '*',
    );
  });

  it('sizes itself only from height messages sent by its own frame', () => {
    render(<SandboxedHtmlFrame content={page} title="Sized" autoHeight />);
    const frame = screen.getByTitle('Sized') as HTMLIFrameElement;
    expect(frame.style.height).toBe('240px');
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: HTML_PREVIEW_HEIGHT_TYPE, height: 900 },
          source: window,
        }),
      );
    });
    expect(frame.style.height).toBe('240px');
    act(() => {
      window.dispatchEvent(
        new MessageEvent('message', {
          data: { type: HTML_PREVIEW_HEIGHT_TYPE, height: 900 },
          source: frame.contentWindow,
        }),
      );
    });
    expect(frame.style.height).toBe('900px');
  });
});

describe('ordinary html and svg fences', () => {
  it('keeps html source-only until the user runs it in the sandbox', () => {
    render(<MarkdownMessage content={`\`\`\`html\n${page}\n\`\`\``} />);
    expect(screen.queryByTitle('Sales Dashboard')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Run in sandbox' }));
    const frame = screen.getByTitle('Sales Dashboard');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(
      screen.getByText(/isolated from Daemon with no network access/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(screen.queryByTitle('Sales Dashboard')).toBeNull();
  });

  it('cannot run or download while the answer is still streaming', () => {
    render(<MarkdownMessage content={`\`\`\`html\n${page}`} streaming />);
    expect(
      (
        screen.getByRole('button', {
          name: 'Run in sandbox',
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole('button', { name: 'Download' }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it('offers download but not execution for svg', () => {
    const create = vi.fn(() => 'blob:svg');
    vi.stubGlobal(
      'URL',
      Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() }),
    );
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        expect(this.download).toBe('image.svg');
      });
    render(
      <MarkdownMessage
        content={'```svg\n<svg viewBox="0 0 1 1"><circle r="1"/></svg>\n```'}
      />,
    );
    expect(screen.queryByRole('button', { name: 'Run in sandbox' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect(click).toHaveBeenCalledOnce();
  });

  it('leaves other fences untouched', () => {
    render(<MarkdownMessage content={'```python\nprint(1)\n```'} />);
    expect(screen.queryByRole('button', { name: 'Download' })).toBeNull();
  });
});
