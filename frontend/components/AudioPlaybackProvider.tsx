'use client';

import {
  createContext,
  useContext,
  useRef,
  useState,
  useCallback,
  useEffect,
  ReactNode,
} from 'react';
import { ensureAuthHeader } from '../lib/auth';
import { getProtectedMediaUrl } from '../hooks/useAuthenticatedImageUrl';

/**
 * AudioPlaybackProvider manages a single HTMLAudioElement for cached TTS playback.
 *
 * This provider solves the coordination problem: multiple TextToSpeechButton instances
 * need to share state about what's currently playing, and only one audio should play at a time.
 *
 * Previously, this was implemented with module-level refs and 100ms polling intervals.
 * Now, state updates propagate reactively via React context.
 *
 * The MVP uses server-synthesized audio and authenticated buffered playback.
 */

interface AudioPlaybackContextValue {
  /** The text content currently being played, or null if nothing is playing */
  currentlyPlayingText: string | null;

  /** True between play() call and audio.canplay event */
  isLoading: boolean;

  /** Play audio for the given text. Stops any currently playing audio first. */
  play: (text: string, audioUrl: string, playbackRate?: number) => void;

  /** Stop the currently playing audio, if any */
  stop: () => void;

  /** Check if a specific text is currently playing */
  isPlaying: (text: string) => boolean;
}

const AudioPlaybackContext = createContext<AudioPlaybackContextValue | null>(
  null,
);

export function useAudioPlayback(): AudioPlaybackContextValue {
  const context = useContext(AudioPlaybackContext);
  if (!context) {
    throw new Error(
      'useAudioPlayback must be used within an AudioPlaybackProvider',
    );
  }
  return context;
}

interface AudioPlaybackProviderProps {
  children: ReactNode;
}

export function AudioPlaybackProvider({
  children,
}: AudioPlaybackProviderProps) {
  const [currentlyPlayingText, setCurrentlyPlayingText] = useState<
    string | null
  >(null);
  const [isLoading, setIsLoading] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const fetchControllerRef = useRef<AbortController | null>(null);

  const revokeObjectUrl = useCallback(() => {
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    fetchControllerRef.current?.abort();
    fetchControllerRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current.oncanplay = null;
      audioRef.current = null;
    }
    revokeObjectUrl();
    setCurrentlyPlayingText(null);
    setIsLoading(false);
  }, [revokeObjectUrl]);

  useEffect(() => stop, [stop]);

  const play = useCallback(
    async (text: string, audioUrl: string, playbackRate: number = 1.0) => {
      // Stop any currently playing audio
      stop();
      const controller = new AbortController();
      fetchControllerRef.current = controller;

      const protectedUrl = getProtectedMediaUrl(audioUrl);
      let playableUrl = audioUrl;
      if (protectedUrl) {
        setCurrentlyPlayingText(text);
        setIsLoading(true);
        try {
          const authHeader = await ensureAuthHeader();
          const headers: HeadersInit = {};
          if (authHeader) headers.Authorization = authHeader;
          if (controller.signal.aborted) return;
          const response = await fetch(protectedUrl, {
            headers,
            signal: controller.signal,
          });
          if (!response.ok) throw new Error(`fetch ${response.status}`);
          const blob = await response.blob();
          if (controller.signal.aborted) return;
          playableUrl = URL.createObjectURL(blob);
          objectUrlRef.current = playableUrl;
        } catch (err) {
          if (controller.signal.aborted) return;
          console.error('Failed to load authenticated audio:', err);
          stop();
          return;
        }
      }

      // Create new audio element
      if (controller.signal.aborted) return;
      const audio = new Audio(playableUrl);
      audioRef.current = audio;
      setCurrentlyPlayingText(text);
      setIsLoading(true);

      audio.playbackRate = playbackRate;

      audio.oncanplay = () => {
        setIsLoading(false);
      };

      audio.onended = () => {
        if (audioRef.current === audio) {
          audioRef.current = null;
          setCurrentlyPlayingText(null);
          revokeObjectUrl();
        }
        setIsLoading(false);
      };

      audio.onerror = () => {
        if (audioRef.current === audio) {
          audioRef.current = null;
          setCurrentlyPlayingText(null);
          revokeObjectUrl();
        }
        setIsLoading(false);
        console.error('Audio playback error');
      };

      audio.play().catch((err) => {
        console.error('Failed to play audio:', err);
        stop();
      });
    },
    [revokeObjectUrl, stop],
  );

  const isPlaying = useCallback(
    (text: string) => {
      return currentlyPlayingText === text && audioRef.current !== null;
    },
    [currentlyPlayingText],
  );

  const value: AudioPlaybackContextValue = {
    currentlyPlayingText,
    isLoading,
    play,
    stop,
    isPlaying,
  };

  return (
    <AudioPlaybackContext.Provider value={value}>
      {children}
    </AudioPlaybackContext.Provider>
  );
}
