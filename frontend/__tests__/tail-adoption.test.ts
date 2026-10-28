import { describe, expect, it, vi } from 'vitest';
import { TailAdoption } from '../lib/tailAdoption';
import type { DaemonMessage } from '../lib/chatMessages';

const message = (
  id: string,
  created_at = '2026-10-28T00:00:00.123456Z',
): DaemonMessage => ({
  id,
  created_at,
  role: 'user',
  parts: [{ type: 'text', text: 'synthetic' }],
});

describe('SDK tail presentation adoption', () => {
  it('publishes the new cut before the SDK setter can synchronously render', () => {
    const adoption = new TailAdoption('old', [message('old')]);
    const seen: string[] = [];
    adoption.subscribe(() =>
      seen.push(`notify:${adoption.getSnapshot().scope}`),
    );
    adoption.adopt(
      'new',
      [message('new')],
      () => true,
      () => {
        seen.push(
          `apply:${adoption.getSnapshot().scope}:${adoption.getSnapshot().oldest?.id}`,
        );
      },
    );
    expect(seen).toEqual(['apply:new:new', 'notify:new']);
  });

  it('does not adopt or change the cut for a stale selection/response', () => {
    const adoption = new TailAdoption('old', [message('old')]);
    const before = adoption.getSnapshot();
    const apply = vi.fn();
    expect(
      adoption.adopt('stale', [message('stale')], () => false, apply),
    ).toBe(false);
    expect(adoption.getSnapshot()).toBe(before);
    expect(apply).not.toHaveBeenCalled();
  });

  it('freezes boundary values without caching or modifying SDK messages', () => {
    const source = message('persisted');
    const sdk = [source];
    const adoption = new TailAdoption('scope', sdk);
    source.created_at = '2026-10-29T00:00:00Z';
    source.id = 'local-replacement';
    expect(adoption.getSnapshot().oldest).toEqual({
      id: 'persisted',
      created_at: '2026-10-28T00:00:00.123456Z',
    });
    expect(sdk).toHaveLength(1);
    adoption.adopt(
      'empty',
      [],
      () => true,
      () => {},
    );
    expect(adoption.getSnapshot().oldest).toBeNull();
  });

  it('rolls back presentation metadata if the SDK setter fails', () => {
    const adoption = new TailAdoption('old', [message('old')]);
    const before = adoption.getSnapshot();
    expect(() =>
      adoption.adopt(
        'new',
        [message('new')],
        () => true,
        () => {
          throw new Error('synthetic');
        },
      ),
    ).toThrow('synthetic');
    expect(adoption.getSnapshot()).toBe(before);
  });
});
