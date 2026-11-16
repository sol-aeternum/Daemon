import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_BACKEND_BASE,
  selectBackendBase,
} from '../app/api/_lib/bootstrapBackend';

const INTERNAL = 'http://internal-broker-7.cluster.svc:8000';
const PUBLIC = 'http://public-backend.fixture:8000';

beforeEach(() => {
  delete process.env.DAEMON_INTERNAL_API_URL;
  delete process.env.NEXT_PUBLIC_API_URL;
});

afterEach(() => {
  delete process.env.DAEMON_INTERNAL_API_URL;
  delete process.env.NEXT_PUBLIC_API_URL;
  vi.restoreAllMocks();
});

describe('bootstrap backend selection', () => {
  it('selects the internal override before the public base', () => {
    process.env.DAEMON_INTERNAL_API_URL = INTERNAL;
    process.env.NEXT_PUBLIC_API_URL = PUBLIC;
    expect(selectBackendBase()).toEqual({ ok: true, base: INTERNAL });
  });

  it('falls through only empty or whitespace candidates', () => {
    process.env.DAEMON_INTERNAL_API_URL = '   ';
    process.env.NEXT_PUBLIC_API_URL = PUBLIC;
    expect(selectBackendBase()).toEqual({ ok: true, base: PUBLIC });

    process.env.DAEMON_INTERNAL_API_URL = '';
    expect(selectBackendBase()).toEqual({ ok: true, base: PUBLIC });
  });

  it('defaults to the single compose authority, never localhost', () => {
    expect(selectBackendBase()).toEqual({
      ok: true,
      base: DEFAULT_BACKEND_BASE,
    });
    expect(DEFAULT_BACKEND_BASE).toBe('http://backend:8000');
  });

  it('trims trailing slashes and preserves a path prefix', () => {
    expect(selectBackendBase(`${PUBLIC}/`)).toEqual({ ok: true, base: PUBLIC });
    expect(selectBackendBase(`${PUBLIC}/daemon///`)).toEqual({
      ok: true,
      base: `${PUBLIC}/daemon`,
    });
  });

  it('accepts absolute http and https bases', () => {
    expect(selectBackendBase('https://backend.fixture:8443')).toEqual({
      ok: true,
      base: 'https://backend.fixture:8443',
    });
  });

  it('refuses a selected nonempty value instead of falling through', () => {
    for (const invalid of [
      'backend:8000',
      'ftp://backend.fixture',
      'http://user:secret@backend.fixture',
      'http://backend.fixture/daemon?x=1',
      'http://backend.fixture/daemon?',
      'http://backend.fixture/daemon#frag',
      'http://backend.fixture/daemon#',
      '/relative-only',
      'not a url',
    ]) {
      expect(selectBackendBase(invalid, PUBLIC), invalid).toEqual({
        ok: false,
      });
    }
  });
});
