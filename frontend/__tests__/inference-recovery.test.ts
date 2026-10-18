import { describe, expect, it } from 'vitest';
import {
  getDaemonMessageText,
  hasUnresolvedInference,
  normalizeDaemonMessages,
} from '../lib/chatMessages';

describe('inference quarantine display', () => {
  it('keeps partial output explicitly incomplete and does not advertise resume', () => {
    const [message] = normalizeDaemonMessages([
      {
        id: 'a',
        role: 'assistant',
        content: 'Saved partial',
        metadata: { terminal_reason: 'inference_dispatch_unresolved' },
      },
    ]);
    const text = getDaemonMessageText(message);
    expect(text).toContain('Saved partial');
    expect(text).toContain('will not restart it automatically');
    expect(text).toContain('provider may still be working or charging');
    expect(text).toContain('partial answer is incomplete');
    expect(hasUnresolvedInference(message)).toBe(true);
  });

  it('preserves Stop precedence without claiming remote or billing cessation', () => {
    const [message] = normalizeDaemonMessages([
      {
        id: 'a',
        role: 'assistant',
        content: 'Saved partial',
        metadata: {
          terminal_reason: 'cancelled',
          inference_dispatch_possible: true,
        },
      },
    ]);
    expect(getDaemonMessageText(message)).toContain(
      'Daemon stopped local work; provider activity or charges may continue.',
    );
    expect(hasUnresolvedInference(message)).toBe(true);
  });
});
