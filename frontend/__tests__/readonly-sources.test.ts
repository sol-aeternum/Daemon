// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  APP,
  SafetyError,
  allowedSourcesRequest,
  matchesSourcesResponse,
  sourcesRefusal,
  isLocalScriptEvent,
  isExtensionScriptRequest,
  inspectReadOnlySources,
  main,
} from '../scripts/authenticated-browser.mjs';

const id = '11111111-1111-4111-8111-111111111111';
const other = '22222222-2222-4222-8222-222222222222';
const path = `/conversations/${id}/web-snapshots`;
const response = (url: string, status = 200, method = 'GET') => ({
  url: () => url,
  status: () => status,
  request: () => ({ method: () => method }),
});

function fakePage(
  options = { rows: 0, titles: 3, status: 200, ready: true, labels: true },
) {
  let selected = -1;
  let current = APP;
  let pending: {
    predicate: (value: ReturnType<typeof response>) => boolean;
    resolve: (value: ReturnType<typeof response>) => void;
  } | null = null;
  const events = new Map<string, (value?: unknown) => void>();
  const ids = [id, other, '33333333-3333-4333-8333-333333333333'];
  function emit(url: string, status = 200) {
    const value = response(url, status);
    if (pending?.predicate(value)) {
      pending.resolve(value);
      pending = null;
    }
  }
  const clicks: string[] = [];
  function locator(kind: string, index = -1) {
    return {
      count: async () =>
        kind === 'titles'
          ? options.titles
          : kind === 'rows' || kind === 'controls'
            ? options.rows
            : options.labels
              ? options.rows
              : 0,
      nth: (position: number) => locator(kind, position),
      first: () => locator(kind),
      or: () => locator(kind),
      filter: () => locator('labels'),
      locator: (selector: string) =>
        locator(
          selector === 'p.text-xs' || selector === 'p' ? 'labels' : 'controls',
        ),
      waitFor: async () => {},
      isVisible: async () => options.ready,
      click: async () => {
        clicks.push(kind);
        if (kind === 'titles') {
          selected = index;
          current = `${APP}/?id=${ids[index]}`;
          emit(`http://localhost:8000/conversations/${ids[index]}`);
        }
        if (kind === 'open')
          emit(
            `http://localhost:8000/conversations/${ids[selected]}/web-snapshots`,
            options.status,
          );
      },
    };
  }
  const page = {
    clicks,
    events,
    on: (event: string, callback: (value?: unknown) => void) =>
      events.set(event, callback),
    off: (event: string) => events.delete(event),
    close: vi.fn(async () => {}),
    route: vi.fn(async (..._args: unknown[]) => {}),
    setDefaultTimeout: vi.fn(),
    setViewportSize: vi.fn(async () => {}),
    url: () => current,
    goto: vi.fn(async () => emit('http://localhost:8000/conversations')),
    waitForURL: async (predicate: (url: URL) => boolean) => {
      expect(predicate(new URL(current))).toBe(true);
    },
    waitForResponse: (
      predicate: (value: ReturnType<typeof response>) => boolean,
    ) =>
      new Promise<ReturnType<typeof response>>((resolve) => {
        pending = { predicate, resolve };
      }),
    locator: () => locator('titles'),
    getByRole: (_role: string, { name }: { name: string }) =>
      name === 'Retained sources'
        ? {
            waitFor: async () => {},
            locator: (selector: string) =>
              locator(selector === '[data-snapshot-id]' ? 'rows' : 'labels'),
            getByText: () => locator('empty'),
          }
        : locator(
            name === 'Open conversation details'
              ? 'open'
              : name === 'Close conversation details'
                ? 'close'
                : 'composer',
          ),
    getByText: () => locator('empty-list'),
  };
  return page;
}

afterEach(() => vi.useRealTimers());

describe('bounded read-only Sources diagnostic', () => {
  it('permits only non-navigation installed-extension GET scripts at the route layer', () => {
    const request = (
      url: string,
      method = 'GET',
      type = 'script',
      navigation = false,
    ) => ({
      url: () => url,
      method: () => method,
      resourceType: () => type,
      isNavigationRequest: () => navigation,
    });
    expect(
      isExtensionScriptRequest(request('chrome-extension://fixture/script.js')),
    ).toBe(true);
    for (const url of [
      'https://example.org/script.js',
      'data:text/javascript,void 0',
      'blob:http://localhost:3000/fixture',
      'file:///private',
      'chrome://resources/script.js',
      'chrome-untrusted://fixture/script.js',
      'chrome-extension://user:password@fixture/script.js',
    ])
      expect(isExtensionScriptRequest(request(url))).toBe(false);
    for (const variant of [
      request('chrome-extension://fixture/script.js', 'POST'),
      request('chrome-extension://fixture/script.js', 'GET', 'image'),
      request('chrome-extension://fixture/script.js', 'GET', 'script', true),
    ])
      expect(isExtensionScriptRequest(variant)).toBe(false);
  });
  it('separates browser-local script events from HTTP network requests and navigation', () => {
    const event = (url: string, type = 'script', method = 'GET') => ({
      url: () => url,
      resourceType: () => type,
      method: () => method,
    });
    for (const url of [
      'data:text/javascript,void 0',
      'blob:http://localhost:3000/fixture',
      'chrome-extension://fixture/content.js',
      'chrome://resources/fixture.js',
    ]) {
      expect(isLocalScriptEvent(event(url))).toBe(true);
      expect(isLocalScriptEvent(event(url, 'document'))).toBe(false);
      expect(isLocalScriptEvent(event(url, 'script', 'POST'))).toBe(false);
      expect(allowedSourcesRequest('GET', url)).toBe(false);
    }
    expect(
      isLocalScriptEvent(event('https://accounts.google.com/gsi/client')),
    ).toBe(false);
    expect(
      isLocalScriptEvent(event('https://example.org/image', 'image')),
    ).toBe(false);
    expect(isLocalScriptEvent(event('file:///private'))).toBe(false);
  });
  it('distinguishes external images from auth scripts without URL secrets or payloads', () => {
    expect(
      sourcesRefusal(
        'GET',
        'https://user:password@example.org/image?token=secret#secret',
        'image',
      ),
    ).toBe('GET off-origin request (image; https://example.org)');
    expect(
      sourcesRefusal(
        'GET',
        'https://accounts.google.com/gsi/client?secret=value',
        'script',
      ),
    ).toBe('GET off-origin request (script; https://accounts.google.com)');
    expect(sourcesRefusal('GET', 'data:text/plain,secret', 'image')).toBe(
      'GET off-origin request (image; data:)',
    );
    expect(
      sourcesRefusal('GET', 'blob:https://example.org/secret', 'private-type'),
    ).toBe('GET off-origin request (unknown; blob:)');
  });
  it('reports request method/route names without credentials or identifiers', () => {
    expect(
      sourcesRefusal('GET', `${APP}/apple-touch-icon.png?token=private`),
    ).toBe('GET frontend /apple-touch-icon.png');
    expect(
      sourcesRefusal('OPTIONS', `http://localhost:8000${path}?secret=private`),
    ).toBe('OPTIONS backend /conversations/:redacted/web-snapshots');
    for (const value of [
      `${APP}/api/v1/auth/private?code=secret`,
      `http://user:password@localhost:8000/token/private`,
      'https://private.example.org/private?token=secret',
    ]) {
      expect(sourcesRefusal('POST', value)).not.toMatch(
        /secret|password|user:|\?token|\.org\/private|auth\/private|token\/private/,
      );
    }
    expect(sourcesRefusal('private custom method', 'invalid')).toBe(
      'OTHER invalid request URL',
    );
  });
  it.each([
    ['GET', 'http://localhost:8000/conversations'],
    ['GET', `http://localhost:8000${path}?limit=20&offset=0`],
    ['GET', `${APP}/_next/static/app.js`],
    ['GET', `${APP}/sw.js`],
    ['HEAD', `${APP}/_next/static/app.js`],
    ['POST', `${APP}/api/v1/auth/refresh`],
    ['POST', 'http://localhost:8000/refresh'],
  ])('allows only approved passive request %s', (method, url) =>
    expect(allowedSourcesRequest(method, url)).toBe(true),
  );

  it.each([
    ['DELETE', `http://localhost:8000${path}/${other}`],
    ['GET', `http://localhost:8000${path}/${other}/export`],
    ['PATCH', 'http://localhost:8000/users/me/settings'],
    ['PUT', `http://localhost:8000/conversations/${id}`],
    ['POST', `${APP}/api/chat`],
    ['GET', 'http://localhost:8000/chat/stream'],
    ['GET', `${APP}/generated-files/private.pdf`],
    ['GET', 'https://example.org/original'],
    ['POST', `${APP}/api/v1/auth/logout`],
    ['GET', 'http://user:password@localhost:8000/conversations'],
    ['GET', 'invalid'],
    ['HEAD', `http://localhost:8000${path}`],
  ])(
    'denies mutations, export/download and unrelated requests %s',
    (method, url) => expect(allowedSourcesRequest(method, url)).toBe(false),
  );

  it('correlates collection GET by exact conversation and origin, never an export', () => {
    expect(
      matchesSourcesResponse(response(`http://localhost:8000${path}`), id),
    ).toBe(true);
    for (const value of [
      response(`http://localhost:8000${path}`),
      response(`https://foreign.test${path}`),
      response(`http://localhost:8000${path}/${other}/export`),
      response(`http://localhost:8000${path}`, 200, 'DELETE'),
    ]) {
      expect(matchesSourcesResponse(value, other)).toBe(false);
    }
  });

  it('checks at most three empty conversations without forbidden actions or private output', async () => {
    const page = fakePage({
      rows: 0,
      titles: 5,
      status: 200,
      ready: true,
      labels: true,
    });
    const result = await inspectReadOnlySources(page);
    expect(result).toMatchObject({
      conversationsChecked: 3,
      emptyConversations: 3,
      retainedRowsObserved: 0,
      exportsPerformed: 0,
      removalsPerformed: 0,
    });
    expect(page.clicks).toEqual([
      'titles',
      'open',
      'close',
      'titles',
      'open',
      'close',
      'titles',
      'open',
      'close',
    ]);
    expect(JSON.stringify(result)).not.toMatch(
      /11111111|22222222|localhost|Bearer/,
    );
    expect(page.events.size).toBe(0);
  });

  it('records nonempty metadata controls by counts without activating them', async () => {
    const result = await inspectReadOnlySources(
      fakePage({ rows: 2, titles: 1, status: 200, ready: true, labels: true }),
    );
    expect(result).toMatchObject({
      conversationsChecked: 1,
      nonemptyConversations: 1,
      retainedRowsObserved: 2,
      paginationExercised: false,
    });
  });

  it('does not claim retained metadata when there are no existing conversations', async () => {
    const page = fakePage({
      rows: 0,
      titles: 0,
      status: 200,
      ready: true,
      labels: true,
    });
    const result = await inspectReadOnlySources(page);
    expect(result).toMatchObject({
      conversationsChecked: 0,
      metadataLabelsAndControls: null,
    });
    expect(page.clicks).toEqual([]);
  });

  it('keeps the diagnostic running for a local-script notification while preserving the route deny guard', async () => {
    const page = fakePage({
      rows: 0,
      titles: 0,
      status: 200,
      ready: true,
      labels: true,
    });
    const goto = page.goto;
    const request = {
      method: () => 'GET',
      url: () => 'data:text/javascript,void 0',
      resourceType: () => 'script',
    };
    page.goto = vi.fn(async () => {
      page.events.get('request')?.(request);
      await goto();
    });
    await expect(inspectReadOnlySources(page)).resolves.toMatchObject({
      authenticated: true,
      conversationsChecked: 0,
    });
    expect(page.close).not.toHaveBeenCalled();
    const route = page.route.mock.calls[0][1] as (
      value: unknown,
    ) => Promise<void>;
    const abort = vi.fn(async () => {});
    const fallback = vi.fn(async () => {});
    await route({
      request: () => ({
        ...request,
        url: () => 'chrome-extension://fixture/script.js',
        isNavigationRequest: () => false,
      }),
      abort,
      fallback,
    });
    expect(fallback).toHaveBeenCalledOnce();
    expect(abort).not.toHaveBeenCalled();
    expect(page.close).not.toHaveBeenCalled();
    fallback.mockClear();
    await route({ request: () => request, abort, fallback });
    expect(abort).toHaveBeenCalledOnce();
    expect(fallback).not.toHaveBeenCalled();
    expect(page.close).toHaveBeenCalledOnce();
  });

  it.each([401, 404, 503])(
    'fails on Sources status %s without retrying',
    async (status) => {
      const page = fakePage({
        rows: 0,
        titles: 1,
        status,
        ready: true,
        labels: true,
      });
      await expect(inspectReadOnlySources(page)).rejects.toThrow(
        'Retained Sources list was unavailable',
      );
      expect(page.clicks).toEqual(['titles', 'open']);
      expect(page.events.size).toBe(0);
    },
  );

  it('rejects non-ready and incomplete metadata states', async () => {
    await expect(
      inspectReadOnlySources(
        fakePage({
          rows: 0,
          titles: 1,
          status: 200,
          ready: false,
          labels: true,
        }),
      ),
    ).rejects.toThrow('truthful ready state');
    await expect(
      inspectReadOnlySources(
        fakePage({
          rows: 2,
          titles: 1,
          status: 200,
          ready: true,
          labels: false,
        }),
      ),
    ).rejects.toThrow('metadata or controls');
    await expect(
      inspectReadOnlySources(
        fakePage({
          rows: 21,
          titles: 1,
          status: 200,
          ready: true,
          labels: true,
        }),
      ),
    ).rejects.toThrow('exceeded its bound');
  });

  it('denies a prohibited routed request and closes only the diagnostic page', async () => {
    const page = fakePage();
    page.goto = vi.fn(async () => {
      const callback = page.route.mock.calls[0][1] as (route: {
        request: () => { method: () => string; url: () => string };
        abort: () => Promise<void>;
        fallback: () => Promise<void>;
      }) => Promise<void>;
      const abort = vi.fn(async () => {});
      const forward = vi.fn(async () => {});
      await callback({
        request: () => ({
          method: () => 'DELETE',
          url: () => `http://localhost:8000${path}`,
        }),
        abort,
        fallback: forward,
      });
      expect(abort).toHaveBeenCalledOnce();
      expect(forward).not.toHaveBeenCalled();
      throw new Error('private raw details');
    });
    await expect(inspectReadOnlySources(page)).rejects.toThrow(
      'guard stopped unexpected activity',
    );
    expect(page.close).toHaveBeenCalled();
  });

  it('closes the owned page when its shorter deadline expires', async () => {
    vi.useFakeTimers();
    const page = fakePage();
    page.goto = vi.fn(() => new Promise<void>(() => {}));
    const checked = expect(inspectReadOnlySources(page)).rejects.toThrow(
      'timed out',
    );
    await vi.advanceTimersByTimeAsync(90000);
    await checked;
    expect(page.close).toHaveBeenCalledOnce();
    expect(page.events.size).toBe(0);
  });

  it('consumes the losing workflow rejection after timeout closes a real-style pending operation', async () => {
    vi.useFakeTimers();
    const page = fakePage();
    let rejectNavigation: (reason: Error) => void = () => {};
    page.goto = vi.fn(
      () =>
        new Promise<void>((_resolve, reject) => {
          rejectNavigation = reject;
        }),
    );
    page.close.mockImplementation(async () => {
      rejectNavigation(new Error('TargetClosedError with private URL'));
    });
    const checked = expect(inspectReadOnlySources(page)).rejects.toThrow(
      'timed out',
    );
    await vi.advanceTimersByTimeAsync(90000);
    await checked;
    await vi.runAllTimersAsync();
    expect(page.close).toHaveBeenCalledOnce();
    expect(page.events.size).toBe(0);
  });

  it.each(['request', 'download', 'popup'])(
    'stops unexpected %s without exposing private details',
    async (event) => {
      const page = fakePage();
      const popup = { close: vi.fn(async () => {}) };
      page.goto = vi.fn(async () => {
        page.events.get(event)?.(
          event === 'request'
            ? { method: () => 'POST', url: () => `${APP}/api/chat` }
            : event === 'popup'
              ? popup
              : undefined,
        );
        throw new Error('Private conversation content must not escape.');
      });
      await expect(inspectReadOnlySources(page)).rejects.toThrow(
        'guard stopped unexpected activity',
      );
      expect(page.close).toHaveBeenCalled();
      if (event === 'popup') expect(popup.close).toHaveBeenCalledOnce();
      expect(page.events.size).toBe(0);
    },
  );

  it('requires explicit readiness and refuses broader command flags before attachment', async () => {
    await expect(main(['sources'])).rejects.toThrow(SafetyError);
    await expect(
      main(['sources', '--owner-ready', '--export']),
    ).rejects.toThrow(SafetyError);
  });
});
