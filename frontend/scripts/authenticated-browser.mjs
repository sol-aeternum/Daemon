// Linux-only, owner-operated browser testing. Never exports session credentials.
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import http from 'node:http';

export const PORT = 9227;
export const APP = 'http://localhost:3000';
const BACKEND = 'http://localhost:8000';
const BASE = path.join(os.homedir(), '.local/share/daemon/browser-acceptance');
const PROFILE = path.join(BASE, 'profile');
const STATE = path.join(BASE, 'session.json');
const MARKER = path.join(BASE, 'purpose.json');
const PURPOSE = 'daemon-owner-approved-browser-acceptance-v1';
const MAX_LIFETIME = 20 * 60 * 1000;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class SafetyError extends Error {}
const requireSafe = (condition, message) => {
  if (!condition) throw new SafetyError(message);
};

export function assertPrivateStat(stat, directory, uid) {
  requireSafe(!stat.isSymbolicLink(), 'Unsafe profile or state link.');
  requireSafe(stat.uid === uid, 'Profile or state is not owned by this user.');
  requireSafe(
    directory ? stat.isDirectory() : stat.isFile(),
    'Unexpected profile or state type.',
  );
  requireSafe(
    (stat.mode & 0o777) === (directory ? 0o700 : 0o600),
    'Unsafe profile or state permissions.',
  );
  if (!directory) requireSafe(stat.nlink === 1, 'Unexpected state hard link.');
}

export function parseListeners(text, family) {
  return text
    .trim()
    .split('\n')
    .slice(1)
    .flatMap((line) => {
      const fields = line.trim().split(/\s+/);
      const [address, port] = (fields[1] ?? '').split(':');
      return fields[3] === '0A' && Number.parseInt(port, 16) === PORT
        ? [{ family, address, inode: fields[9] }]
        : [];
    });
}

export function assertLoopbackListeners(rows) {
  requireSafe(
    rows.length === 1 &&
      rows[0].family === 'tcp' &&
      rows[0].address === '0100007F',
    'Debug endpoint is absent or not exclusively IPv4 loopback.',
  );
}

export function validateEndpoint(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new SafetyError('Invalid debug endpoint.');
  }
  requireSafe(
    url.protocol === 'ws:' &&
      url.hostname === '127.0.0.1' &&
      url.port === String(PORT) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      /^\/devtools\/browser\/[a-f0-9-]+$/i.test(url.pathname),
    'Unexpected debug endpoint.',
  );
  return value;
}

export function allowedPage(value, blank = true) {
  if (blank && value === 'about:blank') return true;
  try {
    const url = new URL(value);
    return (
      url.origin === APP &&
      !url.username &&
      !url.password &&
      !/^\/(auth|setup|landing)(\/|$)/.test(url.pathname)
    );
  } catch {
    return false;
  }
}

export function safeMessage(error) {
  return error instanceof SafetyError
    ? error.message
    : 'Browser check failed; private error details suppressed.';
}

export function browserEnvironment(environment) {
  const keys = [
    'HOME',
    'PATH',
    'DISPLAY',
    'WAYLAND_DISPLAY',
    'XDG_RUNTIME_DIR',
    'XAUTHORITY',
    'DBUS_SESSION_BUS_ADDRESS',
    'LANG',
    'LANGUAGE',
    'LC_ALL',
    'LC_CTYPE',
    'TZ',
  ];
  return Object.fromEntries(
    keys
      .filter((key) => environment[key] !== undefined)
      .map((key) => [key, environment[key]]),
  );
}

const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const conversationPath = new RegExp(`^/conversations/${UUID}$`, 'i');
const sourcePath = new RegExp(`^/conversations/(${UUID})/web-snapshots$`, 'i');

// Defense in depth for the new owned tab, not a service-worker boundary.
// Deliberately deny unknown routes, exports, downloads and model requests.
export function allowedSourcesRequest(method, value) {
  try {
    const url = new URL(value);
    if (url.username || url.password) return false;
    const frontend = url.origin === APP;
    const backend = url.origin === BACKEND;
    if (!frontend && !backend) return false;
    if (method === 'POST')
      return (
        (frontend && url.pathname === '/api/v1/auth/refresh') ||
        (backend && url.pathname === '/refresh')
      );
    if (method !== 'GET' && method !== 'HEAD') return false;
    const pathname = url.pathname;
    if (
      frontend &&
      (pathname === '/' ||
        pathname.startsWith('/_next/') ||
        pathname.startsWith('/icons/') ||
        pathname.startsWith('/fonts/') ||
        [
          '/sw.js',
          '/favicon.ico',
          '/manifest.json',
          '/manifest.webmanifest',
        ].includes(pathname))
    )
      return true;
    if (method !== 'GET') return false;
    return (
      [
        '/conversations',
        '/v1/catalog',
        '/v1/models',
        '/v1/auth/config',
        '/users/me/settings',
        '/users/me/entitlements',
      ].includes(pathname) ||
      conversationPath.test(pathname) ||
      sourcePath.test(pathname) ||
      (frontend &&
        ['/api/v1/auth/config', '/api/entitlements'].includes(pathname))
    );
  } catch {
    return false;
  }
}

export function matchesSourcesResponse(response, conversationId) {
  try {
    const url = new URL(response.url());
    return (
      url.origin === BACKEND &&
      sourcePath.exec(url.pathname)?.[1] === conversationId &&
      response.request().method() === 'GET'
    );
  } catch {
    return false;
  }
}

// Useful refusal metadata without headers, queries, credentials or opaque ids.
export function sourcesRefusal(method, value, resourceType = 'unknown') {
  const verb = [
    'GET',
    'HEAD',
    'POST',
    'PUT',
    'PATCH',
    'DELETE',
    'OPTIONS',
  ].includes(method)
    ? method
    : 'OTHER';
  try {
    const url = new URL(value);
    const origin =
      url.origin === APP
        ? 'frontend'
        : url.origin === BACKEND
          ? 'backend'
          : 'off-origin';
    if (origin === 'off-origin') {
      const type = [
        'document',
        'stylesheet',
        'image',
        'media',
        'font',
        'script',
        'texttrack',
        'xhr',
        'fetch',
        'eventsource',
        'websocket',
        'manifest',
        'other',
      ].includes(resourceType)
        ? resourceType
        : 'unknown';
      // URL.origin excludes userinfo, path, query and fragment. Only ordinary
      // HTTP(S) origins are reported; data/blob/file payloads stay omitted.
      const endpoint = ['http:', 'https:'].includes(url.protocol)
        ? url.origin
        : [
              'data:',
              'blob:',
              'chrome-extension:',
              'chrome:',
              'file:',
              'about:',
            ].includes(url.protocol)
          ? url.protocol
          : 'non-http';
      return `${verb} off-origin request (${type}; ${endpoint})`;
    }
    const names = new Set([
      'api',
      'v1',
      'conversations',
      'web-snapshots',
      'export',
      'generated-files',
      'generated-images',
      'generated-audio',
      'users',
      'me',
      'settings',
      'entitlements',
      'models',
      'catalog',
      'config',
      'health',
      'icons',
      'fonts',
      '_next',
      'static',
      'favicon.ico',
      'favicon.svg',
      'favicon.png',
      'apple-touch-icon.png',
      'manifest.json',
      'manifest.webmanifest',
      'sw.js',
    ]);
    let sensitive = false;
    const route = url.pathname
      .split('/')
      .map((segment) => {
        if (sensitive) return segment ? ':redacted' : '';
        if (
          /^(auth|refresh|token|oauth|callback|signin|setup|enroll)$/i.test(
            segment,
          )
        ) {
          sensitive = true;
          return segment.toLowerCase();
        }
        return names.has(segment) || !segment ? segment : ':redacted';
      })
      .slice(0, 8)
      .join('/');
    return `${verb} ${origin} ${route}`;
  } catch {
    return `${verb} invalid request URL`;
  }
}

// Request events also describe browser-local scripts, not just network I/O.
// Those events cannot be blocked by closing a page after emission. The route
// guard separately permits only installed-extension script loading, not HTTP.
export function isLocalScriptEvent(request) {
  try {
    const protocol = new URL(request.url()).protocol;
    return (
      request.method() === 'GET' &&
      request.resourceType?.() === 'script' &&
      ['data:', 'blob:', 'chrome-extension:', 'chrome:'].includes(protocol)
    );
  } catch {
    return false;
  }
}

export function isExtensionScriptRequest(request) {
  try {
    const url = new URL(request.url());
    return (
      url.protocol === 'chrome-extension:' &&
      !url.username &&
      !url.password &&
      request.method() === 'GET' &&
      request.resourceType() === 'script' &&
      request.isNavigationRequest() === false
    );
  } catch {
    return false;
  }
}

// This specific diagnostic returns aggregates only. URLs/ids are transient.
// Never read response bodies, headers, source links/titles or transcript text.
export async function inspectReadOnlySources(page) {
  let blocked = '';
  let expired = false;
  const stopUnsafe = (reason) => {
    blocked ||= reason;
    void page.close().catch(() => {});
  };
  const requestGuard = (request) => {
    if (isLocalScriptEvent(request)) return;
    if (!allowedSourcesRequest(request.method(), request.url()))
      stopUnsafe(
        'event: ' +
          sourcesRefusal(
            request.method(),
            request.url(),
            request.resourceType?.(),
          ),
      );
  };
  const popupGuard = (popup) => {
    void popup.close().catch(() => {});
    stopUnsafe('popup');
  };
  const downloadGuard = () => stopUnsafe('download');
  const routeGuard = async (route) => {
    const request = route.request();
    if (
      isExtensionScriptRequest(request) ||
      allowedSourcesRequest(request.method(), request.url())
    )
      await route.fallback();
    else {
      await route.abort();
      stopUnsafe(
        'route: ' +
          sourcesRefusal(
            request.method(),
            request.url(),
            request.resourceType?.(),
          ),
      );
    }
  };
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => {
      expired = true;
      void page.close().catch(() => {});
      reject(new SafetyError('Read-only Sources check timed out.'));
    }, 90000);
  });
  page.on('request', requestGuard);
  page.on('popup', popupGuard);
  page.on('download', downloadGuard);
  try {
    return await Promise.race([
      (async () => {
        await page.route('**/*', routeGuard);
        page.setDefaultTimeout(8000);
        await page.setViewportSize({ width: 1440, height: 900 });
        const authenticated = page
          .waitForResponse(
            (response) => {
              const url = new URL(response.url());
              return (
                url.origin === BACKEND &&
                url.pathname === '/conversations' &&
                response.request().method() === 'GET'
              );
            },
            { timeout: 15000 },
          )
          .then(
            (response) => response.status() === 200,
            () => false,
          );
        await page.goto(APP, { waitUntil: 'domcontentloaded', timeout: 20000 });
        requireSafe(
          await authenticated,
          'Authenticated readiness not established; sign in manually.',
        );
        const titles = page.locator(
          'div.group.cursor-pointer:has(button[aria-label="Conversation actions"]) p.font-medium',
        );
        // The authenticated list can be empty; no new conversation is created.
        await page
          .getByRole('textbox', { name: 'Message Daemon', exact: true })
          .waitFor({ state: 'visible' });
        await titles
          .first()
          .or(page.getByText('No conversations found', { exact: true }))
          .first()
          .waitFor({ state: 'visible' });
        const selectedCount = Math.min(3, await titles.count());
        const result = {
          authenticated: true,
          conversationsChecked: 0,
          emptyConversations: 0,
          nonemptyConversations: 0,
          retainedRowsObserved: 0,
          metadataLabelsAndControls: null,
          paginationExercised: false,
          exportsPerformed: 0,
          removalsPerformed: 0,
        };
        for (let index = 0; index < selectedCount; index++) {
          const previous = new URL(page.url()).searchParams.get('id');
          const loaded = page
            .waitForResponse(
              (response) => {
                const url = new URL(response.url());
                return (
                  url.origin === BACKEND &&
                  conversationPath.test(url.pathname) &&
                  url.pathname.split('/')[2] !== previous &&
                  response.request().method() === 'GET'
                );
              },
              { timeout: 15000 },
            )
            .then(
              (response) =>
                response.status() === 200
                  ? new URL(response.url()).pathname.split('/')[2]
                  : null,
              () => null,
            );
          await titles.nth(index).click();
          const loadedId = await loaded;
          requireSafe(loadedId, 'Selected conversation was unavailable.');
          await page.waitForURL(
            (url) =>
              url.origin === APP &&
              url.pathname === '/' &&
              url.searchParams.get('id') === loadedId,
          );
          const id = new URL(page.url()).searchParams.get('id');
          requireSafe(
            id && new RegExp(`^${UUID}$`, 'i').test(id),
            'Selected conversation was not established.',
          );
          const listed = page
            .waitForResponse(
              (response) => matchesSourcesResponse(response, id),
              { timeout: 15000 },
            )
            .then(
              (response) => response.status(),
              () => 0,
            );
          await page
            .getByRole('button', {
              name: 'Open conversation details',
              exact: true,
            })
            .click();
          requireSafe(
            (await listed) === 200,
            'Retained Sources list was unavailable; no account operation retried.',
          );
          const region = page.getByRole('region', {
            name: 'Retained sources',
            exact: true,
          });
          await region.waitFor({ state: 'visible' });
          await region
            .locator('p')
            .filter({
              hasText: /^No retained sources yet\.$|^Retrieved .* · Expires /,
            })
            .first()
            .waitFor({ state: 'visible' });
          await region
            .getByText('Loading retained sources…', { exact: true })
            .waitFor({ state: 'hidden' });
          const rows = region.locator('[data-snapshot-id]');
          const count = await rows.count();
          requireSafe(count <= 20, 'Retained Sources page exceeded its bound.');
          if (count === 0) {
            requireSafe(
              await region
                .getByText('No retained sources yet.', { exact: true })
                .isVisible(),
              'Retained Sources did not reach a truthful ready state.',
            );
            result.emptyConversations++;
          } else {
            requireSafe(
              (await rows
                .locator('p.text-xs')
                .filter({ hasText: /^Retrieved .* · Expires / })
                .count()) === count &&
                (await rows.locator('[data-snapshot-export]').count()) ===
                  count &&
                (await rows.locator('[data-removal-trigger]').count()) ===
                  count,
              'Retained Sources metadata or controls were incomplete.',
            );
            result.nonemptyConversations++;
            result.metadataLabelsAndControls = true;
          }
          result.conversationsChecked++;
          result.retainedRowsObserved += count;
          await page
            .getByRole('button', {
              name: 'Close conversation details',
              exact: true,
            })
            .click();
          await region.waitFor({ state: 'hidden' });
        }
        requireSafe(
          !blocked && !expired,
          'Read-only Sources guard stopped unexpected activity.',
        );
        return result;
      })(),
      deadline,
    ]);
  } catch (error) {
    if (blocked)
      throw new SafetyError(
        `Read-only Sources guard stopped unexpected activity: ${blocked}`,
      );
    if (expired) throw new SafetyError('Read-only Sources check timed out.');
    throw error;
  } finally {
    clearTimeout(timer);
    page.off('request', requestGuard);
    page.off('popup', popupGuard);
    page.off('download', downloadGuard);
    // Keep the deny route until the caller closes this owned tab.
  }
}

async function ensureBase() {
  requireSafe(
    process.platform === 'linux' && typeof process.getuid === 'function',
    'This launcher requires Linux.',
  );
  const uid = process.getuid();
  let current = os.homedir();
  const home = await fs.lstat(current);
  requireSafe(
    !home.isSymbolicLink() && home.uid === uid,
    'Unsafe home directory.',
  );
  for (const part of path.relative(current, BASE).split(path.sep)) {
    current = path.join(current, part);
    try {
      await fs.mkdir(current, { mode: 0o700 });
    } catch (error) {
      if (error.code !== 'EEXIST') throw error;
    }
    const stat = await fs.lstat(current);
    requireSafe(
      stat.isDirectory() && !stat.isSymbolicLink() && stat.uid === uid,
      'Unsafe profile parent.',
    );
    requireSafe(
      (stat.mode & 0o022) === 0,
      'Profile parent is writable by another user.',
    );
  }
  assertPrivateStat(await fs.lstat(BASE), true, uid);
  let marker;
  try {
    marker = await fs.lstat(MARKER);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    requireSafe(
      (await fs.readdir(BASE)).length === 0,
      'Refusing to adopt an existing unmarked profile.',
    );
    await fs.writeFile(MARKER, JSON.stringify({ purpose: PURPOSE }), {
      mode: 0o600,
      flag: 'wx',
    });
    marker = await fs.lstat(MARKER);
  }
  assertPrivateStat(marker, false, uid);
  requireSafe(
    JSON.parse(await fs.readFile(MARKER, 'utf8')).purpose === PURPOSE,
    'Profile purpose does not match.',
  );
  try {
    await fs.mkdir(PROFILE, { mode: 0o700 });
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
  }
  assertPrivateStat(await fs.lstat(PROFILE), true, uid);
}

async function startTime(pid) {
  requireSafe(
    Number.isSafeInteger(pid) && pid > 1,
    'Invalid owned browser process.',
  );
  const stat = await fs.lstat(`/proc/${pid}`);
  requireSafe(
    stat.uid === process.getuid(),
    'Unexpected browser process owner.',
  );
  const text = await fs.readFile(`/proc/${pid}/stat`, 'utf8');
  return text.slice(text.lastIndexOf(')') + 2).split(/\s+/)[19];
}

async function live(state) {
  try {
    const text = await fs.readFile(`/proc/${state.pid}/stat`, 'utf8');
    const status = text.slice(text.lastIndexOf(')') + 2).split(/\s+/)[0];
    return (
      !state.closed &&
      status !== 'Z' &&
      (await startTime(state.pid)) === state.startTime
    );
  } catch {
    return false;
  }
}

async function readState() {
  await ensureBase();
  assertPrivateStat(await fs.lstat(STATE), false, process.getuid());
  const state = JSON.parse(await fs.readFile(STATE, 'utf8'));
  requireSafe(
    state.purpose === PURPOSE && state.profile === PROFILE,
    'Unexpected session state.',
  );
  return state;
}

async function listeners() {
  return (
    await Promise.all(
      ['tcp', 'tcp6'].map(async (family) =>
        parseListeners(
          await fs.readFile(`/proc/net/${family}`, 'utf8'),
          family,
        ),
      ),
    )
  ).flat();
}

async function ownedEndpoint(state) {
  requireSafe(await live(state), 'Dedicated browser is not running.');
  const rows = await listeners();
  assertLoopbackListeners(rows);
  let owned = false;
  for (const fd of await fs.readdir(`/proc/${state.pid}/fd`)) {
    try {
      if (
        (await fs.readlink(`/proc/${state.pid}/fd/${fd}`)) ===
        `socket:[${rows[0].inode}]`
      )
        owned = true;
    } catch {
      /* Closed descriptor. */
    }
  }
  requireSafe(owned, 'Debug listener is not owned by the dedicated browser.');
}

function localJson(route) {
  return new Promise((resolve, reject) => {
    const request = http.get(
      { hostname: '127.0.0.1', port: PORT, path: route, timeout: 5000 },
      (response) => {
        if (response.statusCode !== 200) {
          response.resume();
          reject(new SafetyError('Unexpected debug HTTP response.'));
          return;
        }
        let data = '';
        response.setEncoding('utf8');
        response.on('data', (chunk) => {
          data += chunk;
          if (data.length > 256 * 1024) request.destroy();
        });
        response.on('end', () => {
          try {
            resolve(JSON.parse(data));
          } catch {
            reject(new SafetyError('Invalid debug response.'));
          }
        });
        response.on('error', () =>
          reject(new SafetyError('Debug response interrupted.')),
        );
      },
    );
    request.on('timeout', () => request.destroy());
    request.on('error', () =>
      reject(new SafetyError('Debug endpoint unavailable.')),
    );
  });
}

export async function terminateOwnedProcess(
  state,
  {
    isLive = live,
    signal = (pid, value) => {
      process.kill(pid, value);
    },
    wait = pause,
  } = {},
) {
  if (!(await isLive(state))) return false;
  signal(state.pid, 'SIGTERM');
  for (let i = 0; i < 100 && (await isLive(state)); i++) await wait(100);
  let forced = false;
  if (await isLive(state)) {
    signal(state.pid, 'SIGKILL');
    forced = true;
    for (let i = 0; i < 50 && (await isLive(state)); i++) await wait(100);
  }
  requireSafe(
    !(await isLive(state)),
    'Owned browser has not exited; no other process was stopped.',
  );
  return forced;
}

async function stop(state) {
  requireSafe(await live(state), 'Dedicated browser is not running.');
  const forced = await terminateOwnedProcess(state);
  requireSafe(
    (await listeners()).length === 0,
    'Debug listener remains; do not attach.',
  );
  await fs.writeFile(STATE, JSON.stringify({ ...state, closed: true }), {
    mode: 0o600,
  });
  if (forced)
    console.log('Owned browser required a forced stop; profile retained.');
}

async function launch(blank) {
  await ensureBase();
  try {
    requireSafe(
      !(await live(await readState())),
      'Dedicated browser is already running.',
    );
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  requireSafe(
    (await listeners()).length === 0,
    'Debug port is already in use.',
  );
  const child = spawn(
    '/usr/bin/chromium',
    [
      `--user-data-dir=${PROFILE}`,
      '--remote-debugging-address=127.0.0.1',
      `--remote-debugging-port=${PORT}`,
      '--no-first-run',
      '--no-default-browser-check',
      blank ? 'about:blank' : `${APP}/auth`,
    ],
    {
      stdio: 'ignore',
      env: browserEnvironment(process.env),
    },
  );
  const exited = new Promise((resolve, reject) => {
    child.once('exit', resolve);
    child.once('error', reject);
  });
  let state;
  let timer;
  let forceTimer;
  const terminate = () => {
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGTERM');
      if (!forceTimer)
        forceTimer = setTimeout(async () => {
          if (state && (await live(state))) child.kill('SIGKILL');
        }, 10000);
    }
  };
  process.once('SIGINT', terminate);
  process.once('SIGTERM', terminate);
  try {
    requireSafe(Boolean(child.pid), 'Browser launch failed.');
    state = {
      purpose: PURPOSE,
      profile: PROFILE,
      pid: child.pid,
      startTime: await startTime(child.pid),
      closed: false,
    };
    await fs.writeFile(STATE, JSON.stringify(state), { mode: 0o600 });
    for (
      let i = 0;
      i < 200 && (await listeners()).length === 0 && (await live(state));
      i++
    )
      await pause(100);
    await ownedEndpoint(state);
    console.log(
      'Dedicated Chromium ready; debug access is loopback-only. No Playwright connection is active.',
    );
    console.log(
      'Close this test browser when finished. It will also close after 20 minutes.',
    );
    timer = setTimeout(terminate, MAX_LIFETIME);
    await exited;
  } finally {
    clearTimeout(timer);
    terminate();
    await Promise.race([exited.catch(() => {}), pause(10000)]);
    clearTimeout(forceTimer);
    if (state && (await live(state))) await terminateOwnedProcess(state);
    if (state && !(await live(state)))
      await fs.writeFile(STATE, JSON.stringify({ ...state, closed: true }), {
        mode: 0o600,
      });
    process.removeListener('SIGINT', terminate);
    process.removeListener('SIGTERM', terminate);
    requireSafe(
      (await listeners()).length === 0,
      'Debug port did not close; investigate the owned browser.',
    );
  }
}

async function smoke(probe, sources = false) {
  const state = await readState();
  await ownedEndpoint(state);
  const targets = await localJson('/json/list');
  requireSafe(
    targets
      .filter((target) => target.type === 'page')
      .every((target) => allowedPage(target.url)),
    'Finish sign-in and close unrelated/sign-in tabs before attachment.',
  );
  const version = await localJson('/json/version');
  const endpoint = validateEndpoint(version.webSocketDebuggerUrl);
  const { chromium } = await import('playwright');
  let browser;
  let page;
  let escaped = false;
  try {
    browser = await chromium.connectOverCDP(endpoint, {
      noDefaults: true,
      timeout: 10000,
    });
    requireSafe(
      browser.contexts().length === 1,
      'Unexpected browser contexts.',
    );
    const context = browser.contexts()[0];
    requireSafe(
      context.pages().every((candidate) => allowedPage(candidate.url())),
      'Unexpected browser tabs.',
    );
    page = await context.newPage();
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame() && !allowedPage(frame.url())) {
        escaped = true;
        void page.close().catch(() => {});
      }
    });
    if (sources) {
      const result = await inspectReadOnlySources(page);
      requireSafe(!escaped, 'Test tab left the allowed application origin.');
      console.log(JSON.stringify(result));
      return;
    } else if (probe) {
      await page.setContent('<main>Unsigned-in attachment probe</main>');
      requireSafe(
        (await page.locator('main').textContent()) ===
          'Unsigned-in attachment probe',
        'Blank attachment probe failed.',
      );
    } else {
      await page.setViewportSize({ width: 1440, height: 900 });
      const authenticated = page
        .waitForResponse(
          (response) => {
            const url = new URL(response.url());
            return (
              url.origin === BACKEND &&
              url.pathname === '/conversations' &&
              response.status() === 200
            );
          },
          { timeout: 15000 },
        )
        .then(
          () => true,
          () => false,
        );
      await page.goto(APP, { waitUntil: 'domcontentloaded', timeout: 20000 });
      requireSafe(
        (await authenticated) && !escaped,
        'Authenticated readiness not established; sign in manually.',
      );
      const composer = page.getByRole('textbox', {
        name: 'Message Daemon',
        exact: true,
      });
      await composer.waitFor({ state: 'visible', timeout: 10000 });
      requireSafe(
        (await composer.inputValue()) === '',
        'Test tab already has a draft; refusing to overwrite it.',
      );
      const draft = 'Acceptance smoke draft — never sent';
      await composer.fill(draft);
      await page.locator('input[type="file"]').setInputFiles({
        name: 'acceptance-fixture.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from('Local UI fixture; never submitted.'),
      });
      await page
        .getByRole('button', { name: 'Open settings', exact: true })
        .click();
      await page
        .getByRole('navigation', { name: 'Settings' })
        .waitFor({ state: 'visible', timeout: 10000 });
      await page.getByRole('link', { name: '← Chat', exact: true }).click();
      await composer.waitFor({ state: 'visible', timeout: 10000 });
      requireSafe(
        (await composer.inputValue()) === draft,
        'Settings round-trip did not preserve the draft.',
      );
      requireSafe(
        await page
          .getByText('acceptance-fixture.txt', { exact: true })
          .isVisible(),
        'Settings round-trip did not preserve the attachment.',
      );
    }
    requireSafe(!escaped, 'Test tab left the allowed application origin.');
    console.log(
      probe
        ? 'PASS: unsigned-in owned-page attachment.'
        : 'PASS: real authenticated readiness, draft and attachment Settings round-trip. No message sent.',
    );
  } finally {
    if (page) await page.close().catch(() => {});
    if (browser) await browser.close(); // CDP disconnect, not external browser shutdown.
    await ownedEndpoint(state);
  }
}

export async function main(args) {
  requireSafe(
    !process.env.DEBUG && !process.env.PWDEBUG && !process.env.NODE_OPTIONS,
    'Disable debug logging and Node injection options before using this runner.',
  );
  const [command, ...options] = args;
  if (command === 'launch' && options.every((option) => option === '--blank'))
    return launch(options.includes('--blank'));
  if (
    (command === 'smoke' || command === 'probe' || command === 'sources') &&
    options.length === 1 &&
    options[0] === '--owner-ready'
  )
    return smoke(command === 'probe', command === 'sources');
  if (command === 'stop' && options.length === 0) {
    await stop(await readState());
    console.log('Owned browser stopped; debug port closed. Profile retained.');
    return;
  }
  throw new SafetyError(
    'Usage: node scripts/authenticated-browser.mjs launch [--blank] | probe --owner-ready | smoke --owner-ready | sources --owner-ready | stop',
  );
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(safeMessage(error));
    process.exitCode = 1;
  });
}
