import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  clearLegacySnapshotEntries,
  isSnapshotId,
  isWebSnapshotRequest,
  webSnapshotApiUrl,
} from '@/lib/webSnapshotPaths';
import { shouldUseGeneralRuntimeCache } from '@/lib/pwaCaching';

const conversation = '11111111-1111-4111-8111-111111111111';
const snapshot = '22222222-2222-4222-8222-222222222222';
const path = `/conversations/${conversation}/web-snapshots`;
const origin = 'https://daemon.test';
afterEach(() => vi.unstubAllEnvs());

describe('exact retained snapshot request boundaries', () => {
  it('matches relative collection and export queries, not arbitrary origins', () => {
    for (const suffix of [
      '',
      '/',
      '?offset=20&limit=20',
      `/${snapshot}/export`,
    ])
      expect(
        isWebSnapshotRequest(new URL(`${origin}${path}${suffix}`), origin, ''),
      ).toBe(true);
    expect(
      isWebSnapshotRequest(new URL(`https://other.test${path}`), origin, ''),
    ).toBe(false);
  });
  it('matches the configured origin and prefix, and no lookalike endpoints', () => {
    const base = 'https://api.test/daemon/';
    expect(
      isWebSnapshotRequest(
        new URL(`https://api.test/daemon${path}/${snapshot}/export`),
        origin,
        base,
      ),
    ).toBe(true);
    for (const url of [
      `https://api.test${path}`,
      `${origin}/assets/web-snapshots.png`,
      `https://evil.test/daemon${path}`,
      `https://api.test/daemonish${path}`,
      `https://api.test/daemon${path}-other`,
      `https://api.test/daemon${path}/${snapshot}`,
      `https://api.test/daemon${path}/invalid/export`,
    ])
      expect(isWebSnapshotRequest(new URL(url), origin, base)).toBe(false);
  });
  it('fails closed for credentialed or malformed configured bases', () => {
    for (const base of [
      'https://user:pass@api.test',
      'javascript:evil',
      'https://api.test?key=x',
      'https://api.test#fragment',
      '/relative',
    ])
      expect(
        isWebSnapshotRequest(new URL(`https://api.test${path}`), origin, base),
      ).toBe(false);
  });
  it('uses only configured API paths and validated UUIDs for authenticated operations', () => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.test/daemon/');
    expect(webSnapshotApiUrl(conversation)).toBe(
      `https://api.test/daemon${path}`,
    );
    expect(webSnapshotApiUrl(conversation, snapshot, true)).toBe(
      `https://api.test/daemon${path}/${snapshot}/export`,
    );
    expect(webSnapshotApiUrl(conversation, snapshot)).toBe(
      `https://api.test/daemon${path}/${snapshot}`,
    );
    expect(isSnapshotId('../foreign')).toBe(false);
    expect(() => webSnapshotApiUrl('../foreign')).toThrow(
      'Snapshot request unavailable',
    );
    expect(() =>
      webSnapshotApiUrl(conversation, 'https://evil.test'),
    ).toThrow();
  });
  it('excludes matching snapshot routes from the broad cache strategy', () => {
    vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.test/daemon');
    expect(
      shouldUseGeneralRuntimeCache(new URL(`${origin}${path}`), true, origin),
    ).toBe(false);
    expect(
      shouldUseGeneralRuntimeCache(
        new URL(`https://api.test/daemon${path}`),
        false,
        origin,
      ),
    ).toBe(false);
    expect(
      shouldUseGeneralRuntimeCache(
        new URL(`${origin}/assets/web-snapshots.png`),
        true,
        origin,
      ),
    ).toBe(true);
  });
});

describe('targeted legacy snapshot cleanup', () => {
  it('does not create or clear unrelated cache namespaces', async () => {
    const open = vi.fn();
    await clearLegacySnapshotEntries(
      {
        keys: async () => ['static-resources'],
        open,
      } as unknown as CacheStorage,
      origin,
      '',
    );
    expect(open).not.toHaveBeenCalled();
  });
  it('deletes only exact snapshot list/export keys from others', async () => {
    const urls = [
      `${origin}${path}?offset=20`,
      `https://api.test/daemon${path}/${snapshot}/export`,
      `${origin}/keep`,
      `${origin}/assets/web-snapshots.png`,
      `https://foreign.test${path}`,
      `https://api.test/daemon${path}-other`,
    ];
    const requests = urls.map((url) => new Request(url));
    const remove = vi.fn(async (_request: Request) => true);
    const open = vi.fn(async () => ({
      keys: async () => requests,
      delete: remove,
    }));
    await clearLegacySnapshotEntries(
      {
        keys: async () => ['others', 'images'],
        open,
      } as unknown as CacheStorage,
      origin,
      'https://api.test/daemon',
    );
    expect(open).toHaveBeenCalledExactlyOnceWith('others');
    expect(
      remove.mock.calls.map(([request]) => (request as Request).url),
    ).toEqual(urls.slice(0, 2));
  });
});
