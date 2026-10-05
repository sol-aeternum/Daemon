import { afterEach, expect, it, vi } from 'vitest';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

it('targets the configured backend origin on every suggestion path', async () => {
  vi.stubEnv('NEXT_PUBLIC_API_URL', 'https://api.fixture.invalid');
  vi.resetModules();
  const { HOME_SUGGESTIONS_ENDPOINTS } = await import('../lib/homeSuggestions');
  expect(HOME_SUGGESTIONS_ENDPOINTS).toEqual({
    list: 'https://api.fixture.invalid/home-suggestions',
    refresh: 'https://api.fixture.invalid/home-suggestions/refresh',
    settings: 'https://api.fixture.invalid/users/me/settings',
  });
});

it('uses the existing development backend fallback', async () => {
  vi.stubEnv('NEXT_PUBLIC_API_URL', '');
  vi.stubEnv('NODE_ENV', 'development');
  vi.resetModules();
  const { HOME_SUGGESTIONS_ENDPOINTS } = await import('../lib/homeSuggestions');
  expect(HOME_SUGGESTIONS_ENDPOINTS.list).toBe(
    'http://localhost:8000/home-suggestions',
  );
});
