const { createHash } = require('node:crypto');
const { mkdirSync, readFileSync } = require('node:fs');
const { join } = require('node:path');

const here = __dirname;
const screenshotFontDirectory = join(here, 'fonts');

function verifyScreenshotFonts(directory = screenshotFontDirectory) {
  const manifest = JSON.parse(
    readFileSync(join(directory, 'manifest.json'), 'utf8'),
  );
  for (const [name, expected] of Object.entries(manifest.sha256)) {
    const actual = createHash('sha256')
      .update(readFileSync(join(directory, name)))
      .digest('hex');
    if (actual !== expected)
      throw new Error(`Screenshot font checksum mismatch: ${name}`);
  }
}

// These Linux references use system-ui -> Noto Sans. Keep this entirely in the
// browser process: no app CSS, fetched fonts, host font directories or env overrides.
function screenshotFontLaunchOptions() {
  if (process.platform !== 'linux') return {};
  verifyScreenshotFonts();
  const cache = join(here, '../test-results');
  mkdirSync(cache, { recursive: true });
  return {
    env: {
      FONTCONFIG_FILE: join(screenshotFontDirectory, 'fonts.conf'),
      XDG_CACHE_HOME: cache,
    },
  };
}

module.exports = {
  screenshotFontDirectory,
  verifyScreenshotFonts,
  screenshotFontLaunchOptions,
};
