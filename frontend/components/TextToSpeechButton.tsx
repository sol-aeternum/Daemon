'use client';

import { useLayoutEffect, useRef } from 'react';
import { Volume2, VolumeX } from 'lucide-react';
import { useTtsSettings } from '../hooks/useTtsSettings';
import { getTtsSettingsSnapshot } from '../lib/ttsSettings';
import {
  countTtsTextCodePoints,
  MAX_TTS_TEXT_CODE_POINTS,
  TTS_TEXT_TOO_LONG_MESSAGE,
  TTS_STREAMING_MESSAGE,
  useAudioPlayback,
  type TtsOwner,
} from './AudioPlaybackProvider';

export interface TextToSpeechButtonProps {
  /** Rendered content. Frozen at click time; never used as identity. */
  text: string;
  /** Stable server-side message identity. */
  messageId: string;
  /** Committed conversation the message belongs to. */
  conversationId: string | null;
  /** False while the message is still streaming or the view is transitioning. */
  available?: boolean;
}

export function TextToSpeechButton({
  text,
  messageId,
  conversationId,
  available = true,
}: TextToSpeechButtonProps) {
  const { value: settings } = useTtsSettings();
  const {
    phase,
    ownerMessageId,
    ownerConversationId,
    errorMessage,
    startSpeech,
    stopSpeech,
  } = useAudioPlayback();
  const ownerRef = useRef<TtsOwner | null>(null);
  const hidden = settings.enabled === false || text.trim().length === 0;

  // This button owns its request: unmount, content growth or being hidden
  // cancels it. A `requestId`-qualified stop cannot cancel a newer owner.
  useLayoutEffect(
    () => () => {
      if (ownerRef.current) stopSpeech(ownerRef.current);
      ownerRef.current = null;
    },
    [available, conversationId, hidden, messageId, stopSpeech, text],
  );

  const owns =
    ownerMessageId === messageId && ownerConversationId === conversationId;
  const active = owns && phase !== 'idle' && phase !== 'error';
  const failed = owns && phase === 'error';
  const overLimit = countTtsTextCodePoints(text) > MAX_TTS_TEXT_CODE_POINTS;

  const handleClick = () => {
    if (active) {
      if (ownerRef.current) stopSpeech(ownerRef.current);
      ownerRef.current = null;
      return;
    }
    if (!available || overLimit || hidden) return;
    // Fresh validated preference snapshot, frozen for this request.
    const preferences = getTtsSettingsSnapshot();
    const requestId = startSpeech({
      messageId,
      conversationId,
      text,
      voice: preferences.voice,
      speed: preferences.speed,
      format: preferences.format,
    });
    ownerRef.current = { messageId, conversationId, requestId };
  };

  if (hidden) return null;

  const buttonClass =
    'ml-2 inline-flex min-h-touch items-center gap-1 rounded px-2 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] disabled:cursor-not-allowed disabled:opacity-60';

  if (overLimit) {
    return (
      <span className="ml-2 inline-flex items-center gap-1">
        <button
          type="button"
          disabled
          aria-label="Play TTS unavailable"
          title={TTS_TEXT_TOO_LONG_MESSAGE}
          className={buttonClass}
        >
          <Volume2 className="h-3.5 w-3.5" />
        </button>
        <span role="status" className="text-xs text-[var(--color-text-muted)]">
          {TTS_TEXT_TOO_LONG_MESSAGE}
        </span>
      </span>
    );
  }

  const label = active ? 'Stop TTS' : failed ? 'Retry speech' : 'Play TTS';
  const busy = active && phase !== 'playing' && phase !== 'paused';
  const title = !available
    ? TTS_STREAMING_MESSAGE
    : failed && errorMessage
      ? `TTS: ${errorMessage}`
      : label;

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={!available}
        aria-label={available ? label : 'Play TTS unavailable'}
        title={title}
        className={buttonClass}
      >
        {active ? (
          <VolumeX className="h-3.5 w-3.5" />
        ) : (
          <Volume2 className="h-3.5 w-3.5" />
        )}
        {busy ? 'Loading…' : failed ? 'Retry speech' : null}
      </button>
      {failed && errorMessage ? (
        <span
          role="alert"
          className="ml-1 text-xs text-[var(--color-status-error)]"
        >
          {errorMessage}
        </span>
      ) : null}
    </>
  );
}
