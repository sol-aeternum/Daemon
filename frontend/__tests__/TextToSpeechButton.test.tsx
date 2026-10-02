import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { TextToSpeechButton } from '../components/TextToSpeechButton';

const { play, stop } = vi.hoisted(() => ({ play: vi.fn(), stop: vi.fn() }));
vi.mock('../components/AudioPlaybackProvider', () => ({
  useAudioPlayback: () => ({ play, stop, isPlaying: () => false }),
}));
vi.mock('@/lib/auth', () => ({ ensureAuthHeader: async () => 'Bearer test' }));
vi.mock('../hooks/useLocalStorage', () => ({
  useLocalStorage: () => ({
    value: {
      enabled: true,
      voice: 'daemon-default',
      speed: 1.5,
      format: 'mp3',
    },
  }),
}));

beforeEach(() => vi.clearAllMocks());

it('uses the Daemon speech API, applying speed only on the server', async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ audio_path: '/generated-audio/test.mp3' }),
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<TextToSpeechButton text="Hello Daemon" />);
  fireEvent.click(screen.getByLabelText('Play TTS'));
  await waitFor(() => expect(play).toHaveBeenCalled());
  expect(fetchMock.mock.calls[0][0]).toBe('/api/tts');
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({
    speed: 1.5,
    voice: 'daemon-default',
  });
  expect(play.mock.calls[0][2]).toBe(1);
});

it('aborts synthesis when stopped and does not play stale audio', async () => {
  let resolve: (value: unknown) => void = () => {};
  const fetchMock = vi.fn().mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  vi.stubGlobal('fetch', fetchMock);
  render(<TextToSpeechButton text="Hello" />);
  fireEvent.click(screen.getByLabelText('Play TTS'));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  fireEvent.click(screen.getByLabelText('Stop TTS'));
  expect(fetchMock.mock.calls[0][1].signal.aborted).toBe(true);
  resolve({
    ok: true,
    json: async () => ({ audio_path: '/generated-audio/stale.mp3' }),
  });
  await waitFor(() => expect(screen.getByLabelText('Play TTS')).toBeTruthy());
  expect(play).not.toHaveBeenCalled();
});
