// Shared by the launch context and coordinator: validate before starting Node,
// not only after NODE_OPTIONS has already been interpreted by a child runtime.
import { tmpdir } from 'node:os';

export function fixtureEnvironment() {
  const env = {
    PATH: process.env.PATH,
    HOME: process.env.HOME,
    TMPDIR: process.env.TMPDIR ?? tmpdir(),
    NODE_OPTIONS: process.env.NODE_OPTIONS,
    UV_PROJECT_ENVIRONMENT: process.env.UV_PROJECT_ENVIRONMENT ?? '.uv-venv',
    PYTHONPATH: '..',
    LITELLM_MODE: 'PRODUCTION',
    NEXT_TELEMETRY_DISABLED: '1',
    DAEMON_INTERNAL_API_URL: 'http://127.0.0.1:3143/daemon',
    NEXT_PUBLIC_API_URL: 'http://127.0.0.1:3143/daemon',
  };
  if (
    env.NODE_OPTIONS &&
    (!/^--max-old-space-size=[1-9]\d*$/.test(env.NODE_OPTIONS) ||
      env.NODE_OPTIONS !== env.NODE_OPTIONS.trim() ||
      Number(env.NODE_OPTIONS.split('=')[1]) > 2048)
  )
    throw new Error('Fixture NODE_OPTIONS must be a heap limit of 1–2048 MiB.');
  return env;
}
