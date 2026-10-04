import { describe, expect, it } from 'vitest';

import {
  isSameOriginApiRequest,
  isPrivateSpeechRequest,
  shouldUseGeneralRuntimeCache,
} from '@/lib/pwaCaching';

describe('PWA runtime cache boundaries', () => {
  it('excludes protected speech and capabilities on direct backend origins', () => {
    for (const path of [
      '/tts/capabilities',
      '/tts/stream/v1',
      '/generated-audio/fixture.mp3',
    ]) {
      const url = new URL(`https://backend.fixture${path}`);
      expect(isPrivateSpeechRequest(url)).toBe(true);
      expect(shouldUseGeneralRuntimeCache(url, false)).toBe(false);
    }
    expect(
      isPrivateSpeechRequest(new URL('https://backend.fixture/tts-example')),
    ).toBe(false);
  });
  it('classifies same-origin /api/* requests as network-only', () => {
    expect(
      isSameOriginApiRequest(new URL('https://daemon.test/api/chat'), true),
    ).toBe(true);
    expect(
      isSameOriginApiRequest(
        new URL('https://daemon.test/api/v1/models'),
        true,
      ),
    ).toBe(true);
  });

  it('does not classify cross-origin or non-matching paths as private APIs', () => {
    expect(
      isSameOriginApiRequest(
        new URL('https://api.example.test/api/chat'),
        false,
      ),
    ).toBe(false);
    expect(
      isSameOriginApiRequest(new URL('https://daemon.test/api'), true),
    ).toBe(false);
    expect(
      isSameOriginApiRequest(new URL('https://daemon.test/apiary'), true),
    ).toBe(false);
  });

  it('keeps same-origin API requests out of the general runtime cache', () => {
    expect(
      shouldUseGeneralRuntimeCache(
        new URL('https://daemon.test/api/conversations'),
        true,
      ),
    ).toBe(false);
    expect(
      shouldUseGeneralRuntimeCache(new URL('https://daemon.test/chat'), true),
    ).toBe(true);
    expect(
      shouldUseGeneralRuntimeCache(
        new URL('https://cdn.example.test/app.js'),
        false,
      ),
    ).toBe(true);
  });
});
