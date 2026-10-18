import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import type { TestProject } from 'vitest/node';

type CompletedTranscript = {
  task_id: string;
  prefix: string;
  final: string;
  early_frames: string[];
  reconnect_frames: string[];
  dispatches: number;
  reads: number;
};
type TerminalTranscript = {
  task_id: string;
  final: string;
  frames: string[];
  status: string;
};
type DeliveryTranscripts = {
  completed: CompletedTranscript;
  source_incomplete: CompletedTranscript;
  failed: TerminalTranscript;
  cancelled: TerminalTranscript;
  uncertain_write: TerminalTranscript;
  uncertain_cancel: TerminalTranscript;
  uncertain_periodic: TerminalTranscript;
};

declare module 'vitest' {
  export interface ProvidedContext {
    progressiveResearch: DeliveryTranscripts;
  }
}

// Run once before competing jsdom workers start. This is a fresh real backend
// execution on EVERY invocation, never a disk cache or stored golden transcript.
// Keep the previous hook's 10s budget as an explicit failing setup deadline;
// test/hook deadlines and suite concurrency remain unchanged.
export default function setup(project: TestProject) {
  const started = performance.now();
  const root = path.resolve(project.config.root, '..');
  const python = path.resolve(
    root,
    process.env.UV_PROJECT_ENVIRONMENT ?? '.uv-venv',
    'bin/python',
  );
  const env: NodeJS.ProcessEnv = {
    NODE_ENV: 'test',
    LITELLM_MODE: 'PRODUCTION',
    HF_HUB_OFFLINE: '1',
    PYTHONPATH: '.',
  };
  const inherited = {
    PATH: process.env.PATH,
    HOME: process.env.HOME,
    TMPDIR: process.env.TMPDIR,
    TMP: process.env.TMP,
    TEMP: process.env.TEMP,
    PYTHONPYCACHEPREFIX: process.env.PYTHONPYCACHEPREFIX,
  };
  for (const [key, value] of Object.entries(inherited)) {
    if (value) env[key] = value;
  }
  const transcripts = JSON.parse(
    execFileSync(python, ['-m', 'tests.progressive_research_support'], {
      cwd: root,
      env,
      encoding: 'utf8',
      timeout: 10_000,
      maxBuffer: 4 * 1024 * 1024,
    }),
  ) as DeliveryTranscripts;
  const elapsed = performance.now() - started;
  if (elapsed >= 10_000) {
    throw new Error(
      'Progressive research backend setup exceeded its 10000ms budget',
    );
  }
  project.provide('progressiveResearch', transcripts);
  console.info(
    `Progressive research backend setup: ${Math.round(elapsed)}ms (budget 10000ms)`,
  );
}
