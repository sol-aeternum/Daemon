// Never forward browser text, errors, stdout, general attachments or raw reports.
import { lstatSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type {
  FullConfig,
  FullResult,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from '@playwright/test/reporter';
import cases from './issue490-cases.json';

const privateRoot = path.resolve('test-results/issue490-control');
const browserRoot = path.resolve('test-results/issue490-browser');
const keys = cases.map((entry) => JSON.stringify(entry));

export function windowAttachment(result: TestResult): unknown {
  const attachments = result.attachments.filter(
    (attachment) => attachment.name === 'issue490-window',
  );
  if (!attachments.length) return null;
  if (attachments.length !== 1) throw new Error('window_count');
  const attachment = attachments[0];
  if (attachment.contentType !== 'application/json')
    throw new Error('window_type');
  let buffer: Buffer;
  if (attachment.body) buffer = attachment.body;
  else {
    const filename = attachment.path;
    if (
      !filename ||
      !path.resolve(filename).startsWith(browserRoot + path.sep) ||
      realpathSync(filename) !== path.resolve(filename)
    )
      throw new Error('window_path');
    const stat = lstatSync(filename);
    if (!stat.isFile() || stat.size > 2048) throw new Error('window_size');
    buffer = readFileSync(filename);
  }
  if (buffer.length > 2048) throw new Error('window_size');
  // Controller validates the exact numeric schema before anything is exported.
  return JSON.parse(buffer.toString('utf8'));
}

export default class Issue490Reporter implements Reporter {
  private errors = 0;
  private globalErrors = 0;
  private workers = 0;
  private retries: number[] = [];
  private indices: number[] = [];
  private attempts: object[] = [];

  printsToStdio() {
    return false;
  }

  private index(test: TestCase) {
    return keys.indexOf(
      JSON.stringify([
        test.parent.project()?.name,
        path.basename(test.location.file),
        test.title,
      ]),
    );
  }

  onBegin(config: FullConfig, suite: Suite) {
    this.workers = config.workers;
    this.retries = config.projects.map((project) => project.retries);
    this.indices = suite.allTests().map((test) => this.index(test));
    if (
      config.projects.some((project) => project.repeatEach !== 1) ||
      config.shard !== null
    )
      this.errors++;
  }

  onError() {
    this.globalErrors++;
  }

  onTestEnd(test: TestCase, result: TestResult) {
    let window: unknown = null;
    try {
      window = windowAttachment(result);
    } catch {
      this.errors++;
    }
    this.attempts.push({
      index: this.index(test),
      retry: result.retry,
      status: result.status,
      duration_ms: result.duration,
      worker: result.workerIndex,
      start_epoch_ms: result.startTime.getTime(),
      window,
    });
    if (this.attempts.length > cases.length * 2) this.errors++;
  }

  onEnd(result: FullResult) {
    writeFileSync(
      path.join(privateRoot, 'candidate.json'),
      JSON.stringify({
        schema: 1,
        workers: this.workers,
        retries: this.retries,
        indices: this.indices,
        attempts: this.attempts,
        global_errors: this.globalErrors,
        reporter_errors: this.errors,
        status: result.status,
      }),
      { flag: 'wx' },
    );
  }
}
