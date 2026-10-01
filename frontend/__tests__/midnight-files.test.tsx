import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { FilePreview } from '@/src/components/FilePreview';
import { FileDownloadCard } from '@/components/FileDownloadCard';
import { getConversationOutputs } from '@/lib/conversationDetails';
import { ConversationDetails } from '@/components/ConversationDetails';
import { Blob as NodeBlob } from 'node:buffer';

const auth = vi.hoisted(() => vi.fn());
vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: auth,
  getAuthGeneration: () => 0,
  subscribeAuthGeneration: () => () => {},
}));
vi.mock('@/src/components/previews', () => ({
  CsvPreview: ({ content }: { content: string }) => <p>{content}</p>,
  HtmlPreview: ({ content }: { content: string }) => <p>{content}</p>,
  PdfPreview: ({ url }: { url: string }) => <p>{url}</p>,
  DocxPreview: () => <p>Document rendered</p>,
}));
vi.mock('@/src/components/MarkdownRenderer', () => ({
  default: ({ content }: { content: string }) => <p>{content}</p>,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}
const response = (text: string, status = 200) => new Response(text, { status });

beforeEach(() => {
  vi.stubGlobal('Blob', NodeBlob);
  auth.mockReset().mockResolvedValue('Bearer fixture');
  vi.stubGlobal('fetch', vi.fn());
  vi.stubGlobal(
    'URL',
    Object.assign(URL, {
      createObjectURL: vi.fn(() => 'blob:fixture'),
      revokeObjectURL: vi.fn(),
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('selected file lifecycle', () => {
  it('keeps a late body from replacing a newly selected output', async () => {
    const body = deferred<Blob>();
    vi.mocked(fetch)
      .mockResolvedValueOnce({
        ok: true,
        headers: new Headers(),
        blob: () => body.promise,
      } as Response)
      .mockResolvedValueOnce(response('new-file'));
    const view = render(
      <FilePreview
        fileUrl="/generated-files/old.csv"
        filename="old.csv"
        format="csv"
      />,
    );
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    view.rerender(
      <FilePreview
        fileUrl="/generated-files/new.csv"
        filename="new.csv"
        format="csv"
      />,
    );
    await screen.findByText('new-file');
    await act(async () => body.resolve(new Blob(['old-file'])));
    expect(screen.queryByText('old-file')).toBeNull();
    expect(screen.getByText('new-file')).toBeTruthy();
    expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toEqual({
      Authorization: 'Bearer fixture',
    });
  });
  it('does not fetch after selection is removed during auth refresh', async () => {
    const pending = deferred<string>();
    auth.mockReturnValue(pending.promise);
    const view = render(
      <FilePreview
        fileUrl="/generated-files/old.csv"
        filename="old.csv"
        format="csv"
      />,
    );
    view.unmount();
    await act(async () => pending.resolve('Bearer fixture'));
    expect(fetch).not.toHaveBeenCalled();
  });
  it('revokes protected PDF URLs on replacement and unmount', async () => {
    vi.mocked(fetch).mockImplementation(async () => response('pdf fixture'));
    const view = render(
      <FilePreview
        fileUrl="/generated-files/a.pdf"
        filename="a.pdf"
        format="pdf"
      />,
    );
    await screen.findByText('blob:fixture');
    view.rerender(
      <FilePreview
        fileUrl="/generated-files/b.pdf"
        filename="b.pdf"
        format="pdf"
      />,
    );
    await screen.findByText('blob:fixture');
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2);
  });
  it.each([401, 403, 404, 500])(
    'shows a truthful %i preview failure',
    async (status) => {
      vi.mocked(fetch).mockResolvedValue(response('', status));
      render(
        <FilePreview
          fileUrl="/generated-files/a.csv"
          filename="a.csv"
          format="csv"
        />,
      );
      const alert = await screen.findByRole('alert');
      expect(alert.textContent).toMatch(
        status === 404
          ? /unavailable \(missing or expired\)/
          : status < 404
            ? /access/
            : /500/,
      );
    },
  );
  it('bounds stalled body consumption and discards eventual data', async () => {
    vi.useFakeTimers();
    const body = deferred<Blob>();
    vi.mocked(fetch).mockResolvedValue({
      ok: true,
      headers: new Headers(),
      blob: () => body.promise,
    } as Response);
    render(
      <FilePreview
        fileUrl="/generated-files/a.csv"
        filename="a.csv"
        format="csv"
      />,
    );
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => vi.advanceTimersByTime(20001));
    expect(screen.getByRole('alert').textContent).toContain('timed out');
    await act(async () => body.resolve(new Blob(['late-file'])));
    expect(screen.queryByText('late-file')).toBeNull();
  });
  it('does not fetch unsupported or known oversized files', () => {
    const view = render(
      <FilePreview
        fileUrl="/generated-files/a.bin"
        filename="a.bin"
        format="bin"
      />,
    );
    expect(screen.getByText('Preview not available')).toBeTruthy();
    view.rerender(
      <FilePreview
        fileUrl="/generated-files/a.csv"
        filename="a.csv"
        format="csv"
        fileSize={6 * 1024 * 1024}
      />,
    );
    expect(screen.getByText('File too large to preview')).toBeTruthy();
    expect(fetch).not.toHaveBeenCalled();
  });
  it('rejects an oversized streamed body even without length metadata', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(new Uint8Array(5 * 1024 * 1024 + 1)),
    );
    render(
      <FilePreview
        fileUrl="/generated-files/a.csv"
        filename="a.csv"
        format="csv"
      />,
    );
    expect((await screen.findByRole('alert')).textContent).toContain(
      '5MB preview limit',
    );
  });
  it('reports a download rejection visibly without an unhandled promise', async () => {
    vi.mocked(fetch).mockResolvedValue(response('', 404));
    render(
      <FileDownloadCard fileUrl="/generated-files/a.csv" filename="a.csv" />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Download' }));
    expect((await screen.findByRole('alert')).textContent).toContain(
      'File unavailable',
    );
    expect(
      screen.getByRole('button', { name: 'Download' }).hasAttribute('disabled'),
    ).toBe(false);
  });
});

it('collects every successful output and excludes failed or external paths', () => {
  expect(
    getConversationOutputs([
      {
        type: 'tool_result',
        name: 'generate_document',
        result: { data: { file_url: '/generated-files/old.csv' } },
      },
      {
        type: 'tool_result',
        name: 'generate_document',
        result: { file_url: '/generated-files/new.csv' },
      },
      {
        type: 'tool_result',
        name: 'generate_document',
        result: { success: false, file_url: '/generated-files/failed.csv' },
      },
      {
        type: 'tool_result',
        name: 'generate_document',
        result: { file_url: 'https://external.example/file.csv' },
      },
    ]).map((output) => output.fileUrl),
  ).toEqual(['/generated-files/old.csv', '/generated-files/new.csv']);
});

it('keeps an unmatched stopped action unknown and supports arrow-key tabs', () => {
  const onTab = vi.fn();
  render(
    <ConversationDetails
      turns={[
        {
          id: 'a',
          running: false,
          stopped: true,
          events: [{ type: 'tool_call', name: 'web_fetch', arguments: {} }],
        },
      ]}
      tab="Activity"
      onTab={onTab}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      modal={false}
    />,
  );
  expect(screen.getByText('Outcome unknown')).toBeTruthy();
  expect(screen.queryByText('Running')).toBeNull();
  fireEvent.keyDown(screen.getByRole('tab', { name: 'Activity' }), {
    key: 'ArrowRight',
  });
  expect(onTab).toHaveBeenCalledWith('Sources');
});

it('closes desktop details with Escape from outside the panel without consuming a nested dialog Escape', () => {
  const close = vi.fn();
  render(
    <ConversationDetails
      turns={[]}
      tab="Sources"
      onTab={vi.fn()}
      onSelect={vi.fn()}
      onClose={close}
      modal={false}
    />,
  );
  fireEvent.keyDown(document.body, { key: 'Escape' });
  expect(close).toHaveBeenCalledTimes(1);
  const nested = document.createElement('dialog');
  nested.setAttribute('open', '');
  document.body.appendChild(nested);
  fireEvent.keyDown(nested, { key: 'Escape' });
  expect(close).toHaveBeenCalledTimes(1);
  nested.remove();
});

it('does not download an old file after its card unmounts during auth', async () => {
  const pending = deferred<string>();
  auth.mockReturnValue(pending.promise);
  const view = render(
    <FileDownloadCard fileUrl="/generated-files/old.csv" filename="old.csv" />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Download' }));
  view.unmount();
  await act(async () => pending.resolve('Bearer fixture'));
  expect(fetch).not.toHaveBeenCalled();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
});

it('does not attach account credentials to an external download URL', async () => {
  vi.mocked(fetch).mockResolvedValue(response('', 404));
  render(
    <FileDownloadCard
      fileUrl="https://example.org/file.csv"
      filename="file.csv"
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Download' }));
  await screen.findByRole('alert');
  expect(auth).not.toHaveBeenCalled();
  expect(
    new Headers(vi.mocked(fetch).mock.calls[0][1]?.headers).has(
      'Authorization',
    ),
  ).toBe(false);
});
