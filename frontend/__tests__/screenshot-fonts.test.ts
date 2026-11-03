// @vitest-environment node
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cpSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import {
  screenshotFontDirectory,
  screenshotFontLaunchOptions,
  verifyScreenshotFonts,
} from '../e2e/screenshot-fonts.cjs';

const owned: string[] = [];
afterEach(() => {
  vi.unstubAllEnvs();
  for (const directory of owned.splice(0))
    rmSync(directory, { recursive: true });
});

describe('reproducible Linux screenshot fonts', () => {
  it('verifies the checked-in release and refuses a changed font', () => {
    expect(() => verifyScreenshotFonts()).not.toThrow();
    const directory = mkdtempSync(join(tmpdir(), 'daemon-screenshot-fonts-'));
    owned.push(directory);
    cpSync(screenshotFontDirectory, directory, { recursive: true });
    writeFileSync(
      join(directory, 'NotoSans-Regular.ttf'),
      'synthetic corrupted font',
    );
    expect(() => verifyScreenshotFonts(directory)).toThrow(
      'Screenshot font checksum mismatch: NotoSans-Regular.ttf',
    );
  });

  it('pins the browser to the repository config without inheriting caller controls', () => {
    vi.stubEnv('FONTCONFIG_FILE', '/synthetic/foreign-fonts.conf');
    vi.stubEnv('FONTCONFIG_PATH', '/synthetic/foreign-fonts');
    vi.stubEnv('SYNTHETIC_SECRET', 'fictional-do-not-forward');
    const options = screenshotFontLaunchOptions();
    expect(options).toEqual(
      process.platform === 'linux'
        ? {
            env: {
              FONTCONFIG_FILE: join(screenshotFontDirectory, 'fonts.conf'),
              XDG_CACHE_HOME: join(
                screenshotFontDirectory,
                '../../test-results',
              ),
            },
          }
        : {},
    );
  });
});
