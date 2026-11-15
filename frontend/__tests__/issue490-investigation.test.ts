import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { TestInfo } from '@playwright/test';
import type { TestResult } from '@playwright/test/reporter';
import ts from 'typescript';
import { describe, expect, it, vi } from 'vitest';
import { recordScrollWindow } from '../e2e/issue490-window';
import { windowAttachment } from '../e2e/issue490-reporter';
import cases from '../e2e/issue490-cases.json';

describe('#490 production investigation', () => {
  it('records numeric Node-side brackets after invoking the original sample once', async () => {
    const order: string[] = [];
    const attach = vi.fn(async () => {
      order.push('attach');
    });
    const sample = vi.fn(async () => {
      order.push('sample');
      return 42;
    });
    expect(
      await recordScrollWindow({ attach } as unknown as TestInfo, sample),
    ).toBe(42);
    expect(sample).toHaveBeenCalledTimes(1);
    expect(order).toEqual(['sample', 'attach']);
    const data = JSON.parse(
      (attach.mock.calls[0] as unknown as [string, { body: string }])[1].body,
    );
    expect(Object.keys(data).sort()).toEqual([
      'end_epoch_ms',
      'end_monotonic_ms',
      'fps',
      'start_epoch_ms',
      'start_monotonic_ms',
    ]);
    expect(data.fps).toBe(42);
  });

  it('does not invent a window when the sample throws', async () => {
    const attach = vi.fn();
    const error = new Error('synthetic sample failure');
    await expect(
      recordScrollWindow({ attach } as unknown as TestInfo, async () => {
        throw error;
      }),
    ).rejects.toBe(error);
    expect(attach).not.toHaveBeenCalled();
  });

  it('does not consume or publish unrelated browser attachments', () => {
    const result = {
      attachments: [
        {
          name: 'browser-console',
          contentType: 'application/json',
          body: Buffer.from('private-canary'),
        },
      ],
    } as unknown as TestResult;
    expect(windowAttachment(result)).toBeNull();
    const numeric = {
      attachments: [
        {
          name: 'issue490-window',
          contentType: 'application/json',
          body: Buffer.from('{"fps":40}'),
        },
      ],
    } as unknown as TestResult;
    expect(windowAttachment(numeric)).toEqual({ fps: 40 });
    numeric.attachments[0].body = Buffer.alloc(2049);
    expect(() => windowAttachment(numeric)).toThrow('window_size');
  });

  it('keeps the original FPS callback AST and strict assertion', () => {
    const filename = path.resolve('e2e/conversation-ergonomics.spec.ts');
    const source = readFileSync(filename, 'utf8');
    const file = ts.createSourceFile(
      filename,
      source,
      ts.ScriptTarget.Latest,
      true,
    );
    const callbacks: ts.Node[] = [];
    const visit = (node: ts.Node) => {
      if (
        ts.isCallExpression(node) &&
        node.expression.getText(file) === 'messages.evaluate' &&
        node.arguments[0]?.getText(file).includes('requestAnimationFrame(step)')
      )
        callbacks.push(node.arguments[0]);
      ts.forEachChild(node, visit);
    };
    visit(file);
    expect(callbacks).toHaveLength(1);
    const original = ts.createSourceFile(
      'original.ts',
      `(element) => new Promise<number>((resolve) => {
      let frames = 0;
      const started = performance.now();
      function step(now: number) {
        frames += 1;
        element.scrollTop = ((now - started) / 1000) * (element.scrollHeight - element.clientHeight);
        if (now - started >= 1000) resolve((frames * 1000) / (now - started));
        else requestAnimationFrame(step);
      }
      requestAnimationFrame(step);
    })`,
      ts.ScriptTarget.Latest,
      true,
    );
    const shape = (node: ts.Node, sourceFile: ts.SourceFile): object => {
      const children: object[] = [];
      ts.forEachChild(node, (child) => {
        children.push(shape(child, sourceFile));
      });
      return {
        kind: node.kind,
        children,
        text: children.length ? undefined : node.getText(sourceFile),
      };
    };
    const statement = original.statements[0] as ts.ExpressionStatement;
    expect(shape(callbacks[0], file)).toEqual(
      shape(statement.expression, original),
    );
    expect(source).toContain('expect(fps).toBeGreaterThan(30);');
    expect(cases).toHaveLength(69);
    expect(new Set(cases.map((entry) => JSON.stringify(entry))).size).toBe(69);
  });
});
