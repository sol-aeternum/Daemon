import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import MarkdownMessage from '../components/MarkdownMessage';
import {
  getDaemonMessageText,
  normalizeDaemonMessage,
  splitDaemonMessageNotice,
} from '../lib/chatMessages';

const open = '```html:interactive';
const staticSource =
  '  <h2>Quarterly totals</h2>\n<table><tr><td>42</td></tr></table>\n';
const dynamicSource =
  '\r\n  <div id="dashboard"></div>\r\n' +
  '  <style>#dashboard { color: red }</style>\r\n' +
  '  <script>document.querySelector("#dashboard").textContent = "Generated using JavaScript.";</script>\r\n' +
  '  <input type="range" min="1" max="5">\r\n\r\n';

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('inline HTML presentation', () => {
  it('waits until streaming finishes, even if the fence has closed', () => {
    const view = render(
      <MarkdownMessage content={`${open}\n${staticSource}`} streaming />,
    );
    expect(screen.getByText('Receiving HTML…')).toBeTruthy();
    expect(screen.queryByTitle('Static HTML preview')).toBeNull();
    view.rerender(
      <MarkdownMessage content={`${open}\n${staticSource}\`\`\``} streaming />,
    );
    expect(screen.getByText('Receiving HTML…')).toBeTruthy();
    expect(screen.queryByTitle('Static HTML preview')).toBeNull();
    view.rerender(
      <MarkdownMessage content={`${open}\n${staticSource}\`\`\``} />,
    );
    const frame = screen.getByTitle('Static HTML preview');
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts');
    expect(frame.getAttribute('srcdoc')).toContain('<h2>Quarterly totals</h2>');
  });

  it('shows an unclosed fence and copies the exact source, not highlighted text or normalized prose', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal(
      'navigator',
      Object.assign(Object.create(navigator), { clipboard: { writeText } }),
    );
    render(<MarkdownMessage content={`${open}\r\n${dynamicSource}`} />);
    expect(screen.getByText(/code block was not closed/)).toBeTruthy();
    const preview = screen.getByRole('region', { name: 'HTML preview' });
    expect(preview.querySelector('pre code')?.textContent).toBe(dynamicSource);
    fireEvent.click(within(preview).getByText('View source'));
    fireEvent.click(
      within(preview).getByRole('button', { name: 'Copy source' }),
    );
    expect(writeText).toHaveBeenCalledWith(dynamicSource);
    await screen.findByText('Copied');
    expect(screen.queryByTitle('Static HTML preview')).toBeNull();
  });

  it('keeps complete but unsupported HTML as original source, without mounting an iframe', () => {
    render(<MarkdownMessage content={`${open}\r\n${dynamicSource}\`\`\``} />);
    expect(
      screen.getByText(/uses unsupported features or has no visible content/),
    ).toBeTruthy();
    expect(
      screen.getByRole('region', { name: 'HTML preview' }).querySelector('code')
        ?.textContent,
    ).toBe(dynamicSource);
    expect(screen.queryByTitle('Static HTML preview')).toBeNull();
  });

  it('does not mistake empty sanitized markup for a usable preview', () => {
    render(
      <MarkdownMessage
        content={`${open}\n<div> &nbsp; </div><svg><rect width="0" height="0"></rect></svg>\n\`\`\``}
      />,
    );
    expect(screen.getByText(/no visible content/)).toBeTruthy();
    expect(screen.queryByTitle('Static HTML preview')).toBeNull();
  });

  it('keeps source available when load fires but the preview does not start', () => {
    vi.useFakeTimers();
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    const advanceWatchdog = () =>
      act(() => {
        vi.advanceTimersByTime(8000);
      });
    render(<MarkdownMessage content={`${open}\n${staticSource}\`\`\``} />);
    const frame = screen.getByTitle('Static HTML preview');
    fireEvent.load(frame);
    advanceWatchdog();
    expect(screen.queryByRole('alert')).toBeNull();
    hidden.mockReturnValue(false);
    fireEvent(document, new Event('visibilitychange'));
    advanceWatchdog();
    expect(screen.getByRole('alert').textContent).toContain(
      'HTML preview failed',
    );
    expect(screen.getByRole('button', { name: 'Copy source' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    const replacement = screen.getByTitle('Static HTML preview');
    expect(replacement).not.toBe(frame);
    fireEvent(
      window,
      new MessageEvent('message', {
        source: (replacement as HTMLIFrameElement).contentWindow,
        data: { type: 'artifact-height', height: 340 },
      }),
    );
    advanceWatchdog();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(replacement.style.height).toBe('340px');
  });

  it('keeps ordinary HTML fences source-only and does not mistake a wrong closing fence for completion', () => {
    const view = render(
      <MarkdownMessage content={`\`\`\`html\n${staticSource}\`\`\``} />,
    );
    expect(screen.queryByRole('region', { name: 'HTML preview' })).toBeNull();
    expect(view.container.querySelector('pre code')?.textContent).toContain(
      '<h2>',
    );
    view.rerender(
      <MarkdownMessage content={`~~~~html:interactive\n${staticSource}~~~`} />,
    );
    expect(screen.getByText(/code block was not closed/)).toBeTruthy();
    view.rerender(
      <MarkdownMessage content={`${open}\n${staticSource}    \`\`\``} />,
    );
    expect(screen.getByText(/code block was not closed/)).toBeTruthy();
  });

  it('distinguishes nested closing fences from a fence-looking source line', () => {
    const body = '<p>Quote source</p>\n    ```\n';
    const view = render(
      <MarkdownMessage
        content={`> ${open}\n> <p>Quote source</p>\n>     \`\`\`\n> \`\`\``}
      />,
    );
    expect(screen.getByTitle('Static HTML preview')).toBeTruthy();
    expect(
      screen.getByRole('region', { name: 'HTML preview' }).querySelector('code')
        ?.textContent,
    ).toBe(body);
    view.rerender(
      <MarkdownMessage
        content={`> ${open}\n> <p>Quote source</p>\n>     \`\`\``}
      />,
    );
    expect(screen.queryByTitle('Static HTML preview')).toBeNull();
    expect(screen.getByText(/code block was not closed/)).toBeTruthy();
  });

  it('keeps a saved cancellation notice outside the original unclosed source', () => {
    const original = `${open}\n${staticSource}`;
    const saved = normalizeDaemonMessage({
      role: 'assistant',
      content: original,
      status: 'cancelled',
    })!;
    const result = splitDaemonMessageNotice(saved, getDaemonMessageText(saved));
    expect(result.source).toBe(original);
    expect(result.notice).toMatch(/stopped/i);
  });
});
