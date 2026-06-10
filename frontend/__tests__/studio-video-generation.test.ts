import { describe, expect, it } from 'vitest';
import { getVideoGenerationEndpointCandidates } from '../app/studio/hooks/useVideoGeneration';

describe('getVideoGenerationEndpointCandidates', () => {
  it('tries the raw backend SSE chat endpoint before the Next.js AI SDK bridge', () => {
    expect(
      getVideoGenerationEndpointCandidates('http://localhost:8000'),
    ).toEqual([
      'http://localhost:8000/chat',
      '/chat',
      'http://localhost:8000/api/chat',
      '/api/chat',
    ]);
  });

  it('keeps same-origin raw SSE ahead of the data-stream bridge when no backend base is configured', () => {
    expect(getVideoGenerationEndpointCandidates('')).toEqual([
      '/chat',
      '/api/chat',
    ]);
  });
});
