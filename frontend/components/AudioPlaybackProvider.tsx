"use client";

import {
  createContext,
  useContext,
  useRef,
  useState,
  useCallback,
  ReactNode,
} from "react";

/**
 * AudioPlaybackProvider manages a single HTMLAudioElement for cached TTS playback.
 *
 * This provider solves the coordination problem: multiple TextToSpeechButton instances
 * need to share state about what's currently playing, and only one audio should play at a time.
 *
 * Previously, this was implemented with module-level refs and 100ms polling intervals.
 * Now, state updates propagate reactively via React context.
 *
 * NOTE: This provider is for the CACHED playback path (HTMLAudioElement).
 * Streaming TTS (WebSocket + AudioContext) is handled separately in useStreamingTts hook
 * and should NOT be merged here - it uses a different playback mechanism.
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
  null
);

export function useAudioPlayback(): AudioPlaybackContextValue {
  const context = useContext(AudioPlaybackContext);
  if (!context) {
    throw new Error(
      "useAudioPlayback must be used within an AudioPlaybackProvider"
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

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current.onended = null;
      audioRef.current.onerror = null;
      audioRef.current.oncanplay = null;
      audioRef.current = null;
    }
    setCurrentlyPlayingText(null);
    setIsLoading(false);
  }, []);

  const play = useCallback(
    (text: string, audioUrl: string, playbackRate: number = 1.0) => {
      // Stop any currently playing audio
      stop();

      // Create new audio element
      const audio = new Audio(audioUrl);
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
        }
        setIsLoading(false);
      };

      audio.onerror = () => {
        if (audioRef.current === audio) {
          audioRef.current = null;
          setCurrentlyPlayingText(null);
        }
        setIsLoading(false);
        console.error("Audio playback error");
      };

      audio.play().catch((err) => {
        console.error("Failed to play audio:", err);
        stop();
      });
    },
    [stop]
  );

  const isPlaying = useCallback(
    (text: string) => {
      return currentlyPlayingText === text && audioRef.current !== null;
    },
    [currentlyPlayingText]
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
