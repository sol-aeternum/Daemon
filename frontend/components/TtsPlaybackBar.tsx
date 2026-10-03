'use client';

import { Loader2, Pause, Play, X } from 'lucide-react';
import { useAudioPlayback } from './AudioPlaybackProvider';

function formatTime(seconds: number): string {
  const whole = Math.floor(Math.max(0, seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`;
}

/** Lives above the composer, outside the scrolling message list. */
export function TtsPlaybackBar() {
  const {
    phase,
    ownerMessageId,
    ownerConversationId,
    ownerRequestId,
    currentTime,
    duration,
    pauseSpeech,
    resumeSpeech,
    seekSpeech,
    stopSpeech,
  } = useAudioPlayback();

  if (phase === 'idle' || phase === 'error' || ownerMessageId === null)
    return null;

  const owner = {
    messageId: ownerMessageId,
    conversationId: ownerConversationId,
    requestId: ownerRequestId,
  };
  const paused = phase === 'paused';
  const ready = phase === 'playing' || paused || phase === 'starting';
  const status = paused
    ? 'Paused'
    : phase === 'playing'
      ? 'Reading aloud'
      : phase === 'starting'
        ? 'Starting playback…'
        : 'Preparing speech…';
  const controlClass =
    'inline-flex min-h-touch min-w-touch shrink-0 items-center justify-center rounded-lg text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-primary)] disabled:opacity-50';

  return (
    <section
      aria-label="Speech player"
      className="mx-auto mb-2 flex w-full max-w-3xl items-center gap-2 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-2 py-1"
    >
      <button
        type="button"
        aria-label={paused ? 'Resume speech' : 'Pause speech'}
        title={paused ? 'Resume speech' : 'Pause speech'}
        disabled={!ready}
        className={controlClass}
        onClick={() => (paused ? resumeSpeech(owner) : pauseSpeech(owner))}
      >
        {!ready ? (
          <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        ) : paused ? (
          <Play aria-hidden="true" className="h-4 w-4" />
        ) : (
          <Pause aria-hidden="true" className="h-4 w-4" />
        )}
      </button>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2 text-xs text-[var(--color-text-muted)]">
          <span role="status">{status}</span>
          <span className="shrink-0 tabular-nums" aria-hidden="true">
            {formatTime(currentTime)} /{' '}
            {duration > 0 ? formatTime(duration) : '—'}
          </span>
        </div>
        <input
          type="range"
          aria-label="Speech playback position"
          aria-valuetext={`${formatTime(currentTime)} of ${duration > 0 ? formatTime(duration) : 'unknown duration'}`}
          min={0}
          max={duration > 0 ? duration : 1}
          step={0.1}
          value={Math.min(currentTime, duration || 1)}
          disabled={!ready || duration <= 0}
          onChange={(event) =>
            seekSpeech(owner, Number(event.currentTarget.value))
          }
          className="block min-h-touch w-full cursor-pointer accent-[var(--color-accent-primary)] disabled:cursor-not-allowed"
        />
      </div>
      <button
        type="button"
        aria-label="Close speech player"
        title="Stop and close speech player"
        className={controlClass}
        onClick={() => stopSpeech(owner)}
      >
        <X aria-hidden="true" className="h-4 w-4" />
      </button>
    </section>
  );
}
