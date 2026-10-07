import { describe, expect, it } from 'vitest';
import { isolateSuggestionBody } from '../lib/suggestionSubmission';
import { shouldUseGeneralRuntimeCache } from '../lib/pwaCaching';

describe('isolated suggestion transport', () => {
  it('forces a new destination and excludes unrelated history, attachments and metadata', () => {
    const prompt =
      'Write a concrete implementation plan with verification steps.';
    expect(
      isolateSuggestionBody({
        id: 'old-empty-destination',
        suggestion_id: 'opaque-candidate',
        model: 'auto',
        attachments: [{ name: 'unsent-file.pdf' }],
        metadata: { unrelated: 'unsent draft' },
        messages: [
          { role: 'system', content: 'Never transported' },
          { role: 'user', parts: [{ type: 'text', text: prompt }] },
        ],
      }),
    ).toEqual({
      id: null,
      suggestion_id: 'opaque-candidate',
      model: 'auto',
      messages: [{ role: 'user', parts: [{ type: 'text', text: prompt }] }],
    });
  });

  it('keeps the declared client features so the turn is marked request-bound', () => {
    expect(
      isolateSuggestionBody({
        suggestion_id: 'opaque-candidate',
        model: 'auto',
        client_features: ['task-cancel', 'task-reset'],
        idempotency_key: 'never-sent',
        messages: [],
      }),
    ).toEqual({
      id: null,
      suggestion_id: 'opaque-candidate',
      model: 'auto',
      client_features: ['task-cancel', 'task-reset'],
      messages: [],
    });
  });

  it('leaves ordinary chat transport unchanged', () => {
    const body = {
      id: 'current',
      attachments: [],
      messages: [],
      model: 'auto',
    };
    expect(isolateSuggestionBody(body)).toBe(body);
  });

  it.each(['/home-suggestions', '/home-suggestions/refresh'])(
    'never runtime-caches private suggestions from the backend origin: %s',
    (path) => {
      expect(
        shouldUseGeneralRuntimeCache(
          new URL(`https://backend.test${path}`),
          false,
        ),
      ).toBe(false);
    },
  );
});
