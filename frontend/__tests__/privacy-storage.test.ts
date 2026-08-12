import { readdirSync, readFileSync } from 'node:fs';
import { extname, join, relative } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import { clearLegacyPiiStorage } from '@/lib/privacy-storage';

const FRONTEND_ROOT = process.cwd();
const SOURCE_ROOTS = ['app', 'components', 'hooks', 'lib', 'src'];
const SKIPPED_DIRECTORIES = new Set([
  '__tests__',
  'e2e',
  'node_modules',
  '.next',
]);
const CLEANUP_MODULE = 'lib/privacy-storage.ts';
const LEGACY_KEY_LITERAL = /['"](?:user_name|daemon_user_id|daemon_tier)['"]/;

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if (entry.isDirectory()) {
      return SKIPPED_DIRECTORIES.has(entry.name)
        ? []
        : sourceFiles(join(directory, entry.name));
    }

    return ['.ts', '.tsx'].includes(extname(entry.name))
      ? [join(directory, entry.name)]
      : [];
  });
}

function installStorage(): Storage {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(key);
    },
    setItem: (key, value) => {
      values.set(key, value);
    },
  };

  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: storage,
  });
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: storage,
  });
  return storage;
}

describe('legacy browser PII cleanup', () => {
  let storage: Storage;

  beforeEach(() => {
    storage = installStorage();
  });

  it('purges legacy profile keys without clearing unrelated preferences', () => {
    storage.setItem('user_name', 'Ada');
    storage.setItem('daemon_user_id', 'user-id');
    storage.setItem('daemon_tier', 'pro');
    storage.setItem('theme', 'dark');

    clearLegacyPiiStorage();

    expect(storage.getItem('user_name')).toBeNull();
    expect(storage.getItem('daemon_user_id')).toBeNull();
    expect(storage.getItem('daemon_tier')).toBeNull();
    expect(storage.getItem('theme')).toBe('dark');
  });

  it('does not read or write legacy PII keys in production source', () => {
    const offenders = SOURCE_ROOTS.flatMap((root) => {
      const directory = join(FRONTEND_ROOT, root);
      return sourceFiles(directory)
        .filter((file) => relative(FRONTEND_ROOT, file) !== CLEANUP_MODULE)
        .filter((file) => LEGACY_KEY_LITERAL.test(readFileSync(file, 'utf8')))
        .map((file) => relative(FRONTEND_ROOT, file));
    });

    expect(offenders).toEqual([]);
  });
});
