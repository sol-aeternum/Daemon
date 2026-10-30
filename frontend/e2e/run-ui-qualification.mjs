import { spawnSync } from 'node:child_process';
import { resolve, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const frontend = fileURLToPath(new URL('../', import.meta.url));

// Existing tooling controls only: do not forward credentials or Node injection.
export function qualificationEnvironment() {
  return {
    PATH: process.env.PATH,
    HOME: process.env.HOME,
    TMPDIR: process.env.TMPDIR,
    TMP: process.env.TMP,
    TEMP: process.env.TEMP,
    PYTHONPYCACHEPREFIX: process.env.PYTHONPYCACHEPREFIX,
    UV_PROJECT_ENVIRONMENT: process.env.UV_PROJECT_ENVIRONMENT,
    CI: process.env.CI,
    NEXT_PUBLIC_API_URL: 'http://127.0.0.1:3181',
  };
}

/**
 * @param {string} outputRoot
 * @param {(command: string, args: string[], options: {cwd: string, env: NodeJS.ProcessEnv, stdio: 'inherit'}) => {status: number | null, error?: unknown, signal?: string | null}} run
 */
export function runQualification(outputRoot, run = spawnSync) {
  // Synchronous stages are intentional: Next dev may not mutate a live build.
  /** @type {[string, string[]][]} */
  const stages = [
    [
      'Development observability',
      [
        'run',
        'test:browser',
        '--',
        '--config=playwright.development.config.ts',
        `--output=${join(outputRoot, 'development')}`,
      ],
    ],
    ['Fresh production build', ['run', 'build']],
    [
      'Production UI inventory',
      [
        'run',
        'test:browser',
        '--',
        '--config=playwright.production.config.ts',
        `--output=${join(outputRoot, 'production')}`,
      ],
    ],
  ];
  for (const [label, args] of stages) {
    console.info(`UI qualification: ${label}`);
    const result = run('npm', args, {
      cwd: frontend,
      env: qualificationEnvironment(),
      stdio: 'inherit',
    });
    if (result.error || result.signal || result.status !== 0) {
      console.error(`UI qualification failed: ${label}`);
      return result.status || 1;
    }
  }
  console.info(
    'Combined UI qualification complete: development + fresh build + production',
  );
  return 0;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  if (process.argv.length > 3)
    throw new Error(
      'Expected only an optional output root; case filters are not accepted',
    );
  process.exitCode = runQualification(
    resolve(process.argv[2] ?? 'test-results/ui-qualification'),
  );
}
