import { describe, it, expect } from 'vitest';
import {
  isChatEvent,
  isCouncilEvent,
  isCouncilInterviewEvent,
  isCouncilProgressEvent,
  isCouncilOutputEvent,
  isCouncilDoneEvent,
  isCouncilErrorEvent,
  type ChatEvent,
} from '../lib/events';

describe('Council Event Type Guards', () => {
  describe('isCouncilInterviewEvent', () => {
    it('should return true for valid council_interview event', () => {
      const event: ChatEvent = {
        type: 'council_interview',
        roster: { model1: { role: 'analyzer' } },
        presets: ['default', 'custom'],
        rounds_options: [1, 3, 5],
        audit_default: true,
      };
      expect(isCouncilInterviewEvent(event)).toBe(true);
    });

    it('should return false for other council events', () => {
      const event: ChatEvent = { type: 'council_progress', stage: 'interview', current_round: 1, total_rounds: 3, models_complete: 0, models_total: 3 };
      expect(isCouncilInterviewEvent(event)).toBe(false);
    });

    it('should return false for non-council events', () => {
      const event: ChatEvent = { type: 'text', content: 'hello' };
      expect(isCouncilInterviewEvent(event)).toBe(false);
    });
  });

  describe('isCouncilProgressEvent', () => {
    it('should return true for valid council_progress event', () => {
      const event: ChatEvent = {
        type: 'council_progress',
        stage: 'discussion',
        current_round: 2,
        total_rounds: 3,
        models_complete: 1,
        models_total: 3,
      };
      expect(isCouncilProgressEvent(event)).toBe(true);
    });

    it('should return false for other council events', () => {
      const event: ChatEvent = { type: 'council_done', session_id: 'abc', total_tokens: 1000, total_cost_usd: 0.05, models_used: ['gpt-4'] };
      expect(isCouncilProgressEvent(event)).toBe(false);
    });

    it('should return false for non-council events', () => {
      const event: ChatEvent = { type: 'thinking', content: 'thinking...' };
      expect(isCouncilProgressEvent(event)).toBe(false);
    });
  });

  describe('isCouncilOutputEvent', () => {
    it('should return true for valid council_output event', () => {
      const event: ChatEvent = {
        type: 'council_output',
        section: 'summary',
        content: 'Final summary content',
        metadata: { confidence: 0.9 },
      };
      expect(isCouncilOutputEvent(event)).toBe(true);
    });

    it('should return false for other council events', () => {
      const event: ChatEvent = { type: 'council_error', error: 'Something went wrong' };
      expect(isCouncilOutputEvent(event)).toBe(false);
    });

    it('should return false for non-council events', () => {
      const event: ChatEvent = { type: 'routing', model: 'gpt-4' };
      expect(isCouncilOutputEvent(event)).toBe(false);
    });
  });

  describe('isCouncilDoneEvent', () => {
    it('should return true for valid council_done event', () => {
      const event: ChatEvent = {
        type: 'council_done',
        session_id: 'session-123',
        total_tokens: 5000,
        total_cost_usd: 0.25,
        models_used: ['gpt-4', 'claude-3'],
      };
      expect(isCouncilDoneEvent(event)).toBe(true);
    });

    it('should return false for other council events', () => {
      const event: ChatEvent = {
        type: 'council_interview',
        roster: {},
        presets: [],
        rounds_options: [],
        audit_default: false,
      };
      expect(isCouncilDoneEvent(event)).toBe(false);
    });

    it('should return false for non-council events', () => {
      const event: ChatEvent = { type: 'tool_call', name: 'search', arguments: {} };
      expect(isCouncilDoneEvent(event)).toBe(false);
    });
  });

  describe('isCouncilErrorEvent', () => {
    it('should return true for valid council_error event', () => {
      const event: ChatEvent = { type: 'council_error', error: 'Connection timeout' };
      expect(isCouncilErrorEvent(event)).toBe(true);
    });

    it('should return false for other council events', () => {
      const event: ChatEvent = { type: 'council_output', section: 'test', content: 'test', metadata: {} };
      expect(isCouncilErrorEvent(event)).toBe(false);
    });

    it('should return false for non-council events', () => {
      const event: ChatEvent = { type: 'agent_complete', agent: 'research', result: 'done' };
      expect(isCouncilErrorEvent(event)).toBe(false);
    });
  });

  describe('isCouncilEvent (combined guard)', () => {
    it('should return true for council_interview', () => {
      const event: ChatEvent = { type: 'council_interview', roster: {}, presets: [], rounds_options: [], audit_default: false };
      expect(isCouncilEvent(event)).toBe(true);
    });

    it('should return true for council_progress', () => {
      const event: ChatEvent = { type: 'council_progress', stage: 'test', current_round: 1, total_rounds: 1, models_complete: 0, models_total: 1 };
      expect(isCouncilEvent(event)).toBe(true);
    });

    it('should return true for council_output', () => {
      const event: ChatEvent = { type: 'council_output', section: 'test', content: 'test', metadata: {} };
      expect(isCouncilEvent(event)).toBe(true);
    });

    it('should return true for council_done', () => {
      const event: ChatEvent = { type: 'council_done', session_id: 'x', total_tokens: 0, total_cost_usd: 0, models_used: [] };
      expect(isCouncilEvent(event)).toBe(true);
    });

    it('should return true for council_error', () => {
      const event: ChatEvent = { type: 'council_error', error: 'test' };
      expect(isCouncilEvent(event)).toBe(true);
    });

    it('should return false for non-council events', () => {
      const event: ChatEvent = { type: 'text', content: 'hello' };
      expect(isCouncilEvent(event)).toBe(false);
    });
  });

  describe('Discrimination from existing chat events', () => {
    const nonCouncilEvents: ChatEvent[] = [
      { type: 'text', content: 'hello' },
      { type: 'thinking', content: 'thinking...' },
      { type: 'routing', model: 'gpt-4', tier: 'auto' },
      { type: 'agent_spawn', agent: 'research', agentType: 'explore', task: 'search' },
      { type: 'agent_status', agent: 'research', status: 'running', progress: 50 },
      { type: 'agent_complete', agent: 'research', result: 'done' },
      { type: 'image_ready', url: 'http://example.com/img.png', prompt: 'a cat' },
      { type: 'video_generating', request_id: 'req-123', estimated_seconds: 30 },
      { type: 'video_complete', request_id: 'req-123', url: 'http://example.com/vid.mp4', duration: 10, resolution: '1080p' },
      { type: 'video_failed', request_id: 'req-123', error: 'failed', refunded: true },
      { type: 'tool_call', name: 'search', arguments: { query: 'test' } },
      { type: 'tool_result', name: 'search', result: { data: [] } },
      { type: 'pipeline_switch', pipeline: 'cloud' },
      { type: 'conversation', conversation_id: 'conv-123' },
    ];

    it('should reject all non-council events for isCouncilInterviewEvent', () => {
      nonCouncilEvents.forEach(event => {
        expect(isCouncilInterviewEvent(event)).toBe(false);
      });
    });

    it('should reject all non-council events for isCouncilProgressEvent', () => {
      nonCouncilEvents.forEach(event => {
        expect(isCouncilProgressEvent(event)).toBe(false);
      });
    });

    it('should reject all non-council events for isCouncilOutputEvent', () => {
      nonCouncilEvents.forEach(event => {
        expect(isCouncilOutputEvent(event)).toBe(false);
      });
    });

    it('should reject all non-council events for isCouncilDoneEvent', () => {
      nonCouncilEvents.forEach(event => {
        expect(isCouncilDoneEvent(event)).toBe(false);
      });
    });

    it('should reject all non-council events for isCouncilErrorEvent', () => {
      nonCouncilEvents.forEach(event => {
        expect(isCouncilErrorEvent(event)).toBe(false);
      });
    });
  });

  describe('Malformed event rejection', () => {
    it('should reject null', () => {
      expect(isCouncilEvent(null as any)).toBe(false);
      expect(isCouncilInterviewEvent(null as any)).toBe(false);
      expect(isCouncilProgressEvent(null as any)).toBe(false);
      expect(isCouncilOutputEvent(null as any)).toBe(false);
      expect(isCouncilDoneEvent(null as any)).toBe(false);
      expect(isCouncilErrorEvent(null as any)).toBe(false);
    });

    it('should reject undefined', () => {
      expect(isCouncilEvent(undefined as any)).toBe(false);
      expect(isCouncilInterviewEvent(undefined as any)).toBe(false);
      expect(isCouncilProgressEvent(undefined as any)).toBe(false);
      expect(isCouncilOutputEvent(undefined as any)).toBe(false);
      expect(isCouncilDoneEvent(undefined as any)).toBe(false);
      expect(isCouncilErrorEvent(undefined as any)).toBe(false);
    });

    it('should reject objects without type', () => {
      const event = { content: 'test' };
      expect(isCouncilEvent(event as any)).toBe(false);
      expect(isCouncilInterviewEvent(event as any)).toBe(false);
      expect(isCouncilProgressEvent(event as any)).toBe(false);
      expect(isCouncilOutputEvent(event as any)).toBe(false);
      expect(isCouncilDoneEvent(event as any)).toBe(false);
      expect(isCouncilErrorEvent(event as any)).toBe(false);
    });

    it('should reject objects with invalid type', () => {
      const event = { type: 'invalid_type', content: 'test' };
      expect(isCouncilEvent(event as any)).toBe(false);
      expect(isCouncilInterviewEvent(event as any)).toBe(false);
      expect(isCouncilProgressEvent(event as any)).toBe(false);
      expect(isCouncilOutputEvent(event as any)).toBe(false);
      expect(isCouncilDoneEvent(event as any)).toBe(false);
      expect(isCouncilErrorEvent(event as any)).toBe(false);
    });

    it('should reject objects with non-string type', () => {
      const event = { type: 123, content: 'test' };
      expect(isCouncilEvent(event as any)).toBe(false);
      expect(isCouncilInterviewEvent(event as any)).toBe(false);
      expect(isCouncilProgressEvent(event as any)).toBe(false);
      expect(isCouncilOutputEvent(event as any)).toBe(false);
      expect(isCouncilDoneEvent(event as any)).toBe(false);
      expect(isCouncilErrorEvent(event as any)).toBe(false);
    });

    it('should reject primitives', () => {
      expect(isCouncilEvent('string' as any)).toBe(false);
      expect(isCouncilEvent(123 as any)).toBe(false);
      expect(isCouncilEvent(true as any)).toBe(false);
    });
  });

  describe('isChatEvent validation', () => {
    it('should accept valid council events', () => {
      const events: ChatEvent[] = [
        { type: 'council_interview', roster: {}, presets: [], rounds_options: [], audit_default: false },
        { type: 'council_progress', stage: 'test', current_round: 1, total_rounds: 1, models_complete: 0, models_total: 1 },
        { type: 'council_output', section: 'test', content: 'test', metadata: {} },
        { type: 'council_done', session_id: 'x', total_tokens: 0, total_cost_usd: 0, models_used: [] },
        { type: 'council_error', error: 'test' },
      ];
      events.forEach(event => {
        expect(isChatEvent(event)).toBe(true);
      });
    });

    it('should reject malformed events', () => {
      expect(isChatEvent(null)).toBe(false);
      expect(isChatEvent(undefined)).toBe(false);
      expect(isChatEvent({})).toBe(false);
      expect(isChatEvent({ type: 'invalid' })).toBe(false);
    });
  });
});