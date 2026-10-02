'use client';

import { useEffect, useRef, useState } from 'react';
import { Volume2, VolumeX } from 'lucide-react';
import { ensureAuthHeader } from '@/lib/auth';
import { useLocalStorage } from '../hooks/useLocalStorage';
import { TtsSettings, DEFAULT_TTS_SETTINGS } from '../lib/constants';
import { useAudioPlayback } from './AudioPlaybackProvider';

export function TextToSpeechButton({ text }: { text: string }) {
  const { value: settings } = useLocalStorage<TtsSettings>(
    'tts_settings',
    DEFAULT_TTS_SETTINGS,
  );
  const { play, stop, isPlaying } = useAudioPlayback();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  const handleClick = async () => {
    if (loading || isPlaying(text)) {
      controller.current?.abort();
      controller.current = null;
      setLoading(false);
      stop();
      return;
    }
    const abort = new AbortController();
    controller.current = abort;
    setLoading(true);
    setError(null);
    stop();
    try {
      const auth = await ensureAuthHeader();
      if (abort.signal.aborted) return;
      const response = await fetch('/api/tts', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(auth ? { Authorization: auth } : {}),
        },
        signal: abort.signal,
        body: JSON.stringify({
          text,
          voice: settings.voice,
          speed: settings.speed,
          format: settings.format,
          cache: true,
        }),
      });
      const data = await response.json();
      if (!response.ok)
        throw new Error(data?.detail?.code || 'Speech unavailable');
      if (typeof data.audio_path !== 'string') throw new Error('Audio missing');
      if (!abort.signal.aborted) {
        const apiUrl =
          process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
        // Rate is applied by the speech provider exactly once, not on playback.
        play(text, `${apiUrl}${data.audio_path}`, 1);
      }
    } catch (err) {
      if (!abort.signal.aborted) {
        setError(err instanceof Error ? err.message : 'Speech unavailable');
      }
    } finally {
      if (controller.current === abort) {
        controller.current = null;
        setLoading(false);
      }
    }
  };

  if (settings.enabled === false || !text.trim()) return null;
  const active = loading || isPlaying(text);
  const label = active ? 'Stop TTS' : 'Play TTS';
  return (
    <button
      type="button"
      onClick={handleClick}
      aria-label={label}
      title={error ? `TTS: ${error}` : label}
      className="ml-2 inline-flex min-h-touch items-center gap-1 rounded px-2 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
    >
      {active ? (
        <VolumeX className="h-3.5 w-3.5" />
      ) : (
        <Volume2 className="h-3.5 w-3.5" />
      )}
      {loading ? 'Loading…' : error ? 'Retry speech' : null}
    </button>
  );
}
