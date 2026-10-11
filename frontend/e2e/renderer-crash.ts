// Test-only controller. No imports from application code and no raw PID signals.
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { createInterface } from 'node:readline';
import type {
  Browser,
  BrowserServer,
  CDPSession,
  Page,
} from '@playwright/test';

type Identity = { pid: number; start: string };
type ProcessInfo = { id: number; type: string };
type Receipt = {
  status: string;
  nonce?: string;
  candidates?: number[];
  root?: number;
  holder?: number;
  peer?: number;
  reason?: string;
};

async function identity(pid: number): Promise<Identity> {
  if (!Number.isSafeInteger(pid) || pid <= 1)
    throw new Error('Invalid owned PID');
  const stat = await readFile(`/proc/${pid}/stat`, 'ascii');
  const fields = stat
    .slice(stat.lastIndexOf(')') + 2)
    .trim()
    .split(/\s+/);
  if (!/^\d+$/.test(fields[19]))
    throw new Error('Missing process start metadata');
  return { pid, start: fields[19] };
}

function assertRoot(server: BrowserServer, listed: ProcessInfo[]) {
  const owned = server.process();
  if (owned.exitCode !== null || owned.signalCode !== null || !owned.pid)
    throw new Error('Owned browser root exited');
  const roots = listed.filter((entry) => entry.type === 'browser');
  if (roots.length !== 1 || roots[0].id !== owned.pid)
    throw new Error('CDP root does not match launchServer handle');
  return owned.pid;
}

class Receipts {
  private pending: {
    resolve: (value: Receipt) => void;
    reject: (error: Error) => void;
  } | null = null;
  private queued: Receipt[] = [];
  private ended = false;
  private failure: Error | null = null;
  readonly exited: Promise<number | null>;

  constructor(readonly child: ChildProcessWithoutNullStreams) {
    const lines = createInterface({ input: child.stdout });
    lines.on('line', (line) => {
      try {
        if (line.length > 8192) throw new Error('Guard receipt too large');
        const receipt = JSON.parse(line) as Receipt;
        if (!receipt || typeof receipt.status !== 'string')
          throw new Error('Invalid guard receipt');
        if (this.pending) {
          const pending = this.pending;
          this.pending = null;
          pending.resolve(receipt);
        } else this.queued.push(receipt);
      } catch {
        this.fail(new Error('Malformed guard receipt'));
      }
    });
    child.on('error', (error) => this.fail(error));
    // Drain stderr without exposing arbitrary subprocess output as test evidence.
    child.stderr.resume();
    this.exited = new Promise((resolve) => {
      child.once('close', (code) => {
        this.ended = true;
        if (this.pending) this.fail(new Error('Guard exited before receipt'));
        resolve(code);
      });
    });
  }

  private fail(error: Error) {
    this.failure = error;
    this.pending?.reject(error);
    this.pending = null;
    this.child.stdin.end();
  }

  async next(): Promise<Receipt> {
    if (this.failure) throw this.failure;
    const queued = this.queued.shift();
    if (queued) return queued;
    if (this.ended || this.pending)
      throw new Error('Invalid guard protocol phase');
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      return await new Promise<Receipt>((resolve, reject) => {
        this.pending = { resolve, reject };
        timer = setTimeout(
          () => this.fail(new Error('Guard receipt timed out')),
          3000,
        );
      });
    } finally {
      clearTimeout(timer);
    }
  }

  send(value: unknown) {
    if (this.failure || this.ended || this.child.stdin.destroyed)
      throw new Error('Guard input unavailable');
    const line = JSON.stringify(value);
    if (Buffer.byteLength(line) > 8192)
      throw new Error('Guard request too large');
    this.child.stdin.write(`${line}\n`);
  }

  async close() {
    this.child.stdin.end();
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        this.exited,
        new Promise<never>((_resolve, reject) => {
          timer = setTimeout(
            () => reject(new Error('Guard did not exit after EOF')),
            3000,
          );
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }
}

async function markerPids(
  protocol: CDPSession,
  pages: Page[],
): Promise<number[]> {
  const names = pages.map(() => `p02-owned-${randomUUID()}`);
  const pids = new Map(names.map((name) => [name, new Set<number>()]));
  const collect = ({ value }: { value: Array<Record<string, unknown>> }) => {
    for (const event of value) {
      if (
        typeof event.name === 'string' &&
        typeof event.pid === 'number' &&
        Number.isSafeInteger(event.pid)
      )
        pids.get(event.name)?.add(event.pid);
    }
  };
  protocol.on('Tracing.dataCollected', collect);
  try {
    await protocol.send('Tracing.start', {
      categories: 'blink.user_timing',
      transferMode: 'ReportEvents',
    });
    try {
      for (let index = 0; index < pages.length; index++)
        await pages[index].evaluate(
          (name) => performance.mark(name),
          names[index],
        );
    } finally {
      const complete = new Promise<void>((resolve) =>
        protocol.once('Tracing.tracingComplete', () => resolve()),
      );
      await protocol.send('Tracing.end');
      await complete;
    }
    return names.map((name) => {
      const found = pids.get(name)!;
      if (found.size !== 1) throw new Error('Ambiguous renderer marker');
      return [...found][0];
    });
  } finally {
    protocol.off('Tracing.dataCollected', collect);
  }
}

export async function bindRendererCrash(
  server: BrowserServer,
  browser: Browser,
  holder: Page,
  peer: Page,
) {
  if (process.platform !== 'linux')
    throw new Error('Controlled crash requires Linux pidfds');
  const protocol = await browser.newBrowserCDPSession();
  let receipts: Receipts | undefined;
  try {
    const initial = (await protocol.send('SystemInfo.getProcessInfo'))
      .processInfo as ProcessInfo[];
    const root = assertRoot(server, initial);
    const rendererIds = initial
      .filter((entry) => entry.type === 'renderer')
      .map((entry) => entry.id);
    if (
      rendererIds.length < 2 ||
      rendererIds.length > 32 ||
      new Set(rendererIds).size !== rendererIds.length
    )
      throw new Error('Ambiguous browser renderer candidates');
    const rootIdentity = await identity(root);
    const candidates = await Promise.all(rendererIds.map(identity));
    const python = path.resolve(
      '..',
      process.env.UV_PROJECT_ENVIRONMENT ?? '.uv-venv',
      'bin/python',
    );
    receipts = new Receipts(
      spawn(python, ['e2e/renderer_guard.py'], {
        env: {
          PATH: process.env.PATH,
          HOME: process.env.HOME,
          NODE_ENV: 'test',
        },
        stdio: 'pipe',
      }),
    );
    receipts.send({ root: rootIdentity, candidates });
    const ready = await receipts.next();
    if (
      ready.status !== 'ready' ||
      ready.root !== root ||
      typeof ready.nonce !== 'string' ||
      !/^[0-9a-f]{32}$/.test(ready.nonce) ||
      !Array.isArray(ready.candidates) ||
      ready.candidates.length !== candidates.length ||
      !rendererIds.every((pid) => ready.candidates!.includes(pid))
    )
      throw new Error('Guard refused candidate binding');
    // No marker existed until the Python process bound and validated every fd.
    assertRoot(
      server,
      (await protocol.send('SystemInfo.getProcessInfo')).processInfo,
    );
    const [holderPid, peerPid] = await markerPids(protocol, [holder, peer]);
    const members = new Set(rendererIds);
    if (
      holderPid === peerPid ||
      !members.has(holderPid) ||
      !members.has(peerPid)
    )
      throw new Error('Holder/peer are shared or unbound');
    const guard = receipts;
    return {
      async terminate() {
        // The holder is frozen. Re-identify the live peer, never evaluate holder.
        const [freshPeer] = await markerPids(protocol, [peer]);
        const fresh = (await protocol.send('SystemInfo.getProcessInfo'))
          .processInfo as ProcessInfo[];
        assertRoot(server, fresh);
        if (
          freshPeer !== peerPid ||
          freshPeer === holderPid ||
          !fresh.some(
            (entry) => entry.id === holderPid && entry.type === 'renderer',
          ) ||
          !fresh.some(
            (entry) => entry.id === freshPeer && entry.type === 'renderer',
          )
        )
          throw new Error('Renderer identity changed before commit');
        const crashed = holder.waitForEvent('crash');
        // Attach rejection handling immediately; the actual event remains mandatory.
        void crashed.catch(() => {});
        guard.send({ nonce: ready.nonce, holder: holderPid, peer: freshPeer });
        const result = await guard.next();
        if (
          result.status !== 'signaled' ||
          result.holder !== holderPid ||
          result.peer !== freshPeer ||
          (await guard.exited) !== 0
        )
          throw new Error('Guard refused renderer signal');
        await crashed;
        await peer.evaluate(() => true);
      },
      async close() {
        await guard.close();
        await protocol.detach();
      },
    };
  } catch (error) {
    if (receipts) await receipts.close();
    await protocol.detach();
    throw error;
  }
}
