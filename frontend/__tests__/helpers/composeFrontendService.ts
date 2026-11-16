import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * Source-only reading of the checked-in ``frontend`` service.
 *
 * Compose is never rendered and no ``.env`` value or interpreter is involved;
 * this parses the exact checked-in ``environment:`` list and ``build.args:``
 * mapping. The parser is deliberately narrow and mirrors the one in
 * ``bootstrap-proxy-authority.test.ts`` so a chat-transport test can derive the
 * same default upstream values without editing that existing file.
 */
export type FrontendService = {
  environment: Record<string, string>;
  buildArgs: Record<string, string>;
};

/**
 * Locate the checked-in Compose file without relying on ``import.meta.url``,
 * which the jsdom test environment rewrites to a non-file URL.
 */
export function composePath(): string {
  for (const candidate of [
    resolve(process.cwd(), 'docker-compose.yml'),
    resolve(process.cwd(), '..', 'docker-compose.yml'),
  ]) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(
    'docker-compose.yml not found from the test working directory',
  );
}

export function readFrontendService(composeText?: string): FrontendService {
  const text = composeText ?? readFileSync(composePath(), 'utf8');
  const lines = text.split(/\r?\n/);
  const start = lines.indexOf('  frontend:');
  if (start < 0) throw new Error('docker-compose.yml has no frontend service');

  const environment: Record<string, string> = {};
  const buildArgs: Record<string, string> = {};
  let section: 'build' | 'args' | 'environment' | undefined;

  for (let index = start + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^ {0,2}\S/.test(line)) break; // next top-level key or service
    if (/^ {4}\S/.test(line)) {
      section = undefined;
      if (/^ {4}build:\s*$/.test(line)) section = 'build';
      else if (/^ {4}environment:\s*$/.test(line)) section = 'environment';
      continue;
    }
    if (/^ {6}args:\s*$/.test(line)) {
      section = 'args';
      continue;
    }
    if (section === 'args') {
      const arg = /^ {8}([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$/.exec(line);
      if (arg) buildArgs[arg[1]] = arg[2];
      else if (/^ {6}\S/.test(line) || line.trim() === '') section = 'build';
      continue;
    }
    if (section === 'environment') {
      const entry = /^ {6}- (.+)$/.exec(line);
      if (!entry) continue;
      const separator = entry[1].indexOf('=');
      environment[separator < 0 ? entry[1] : entry[1].slice(0, separator)] =
        separator < 0 ? '' : entry[1].slice(separator + 1);
    }
  }

  return { environment, buildArgs };
}

/** Interpolate the only Compose form this repository uses: ``${NAME:-default}``. */
export function interpolateEntry(
  name: string,
  host: Record<string, string | undefined>,
  service: FrontendService = readFrontendService(),
): string {
  const raw = service.environment[name];
  if (raw === undefined) throw new Error(`frontend environment has no ${name}`);
  return raw.replace(
    /\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}/g,
    (_match, key: string, _operator: string | undefined, fallback?: string) => {
      const value = host[key];
      if (fallback !== undefined && (value === undefined || value === '')) {
        return fallback;
      }
      return value ?? '';
    },
  );
}
