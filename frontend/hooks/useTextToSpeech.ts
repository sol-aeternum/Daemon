"use client";

import { useState, useCallback, useRef, useEffect } from "react";

interface UseTextToSpeechOptions {
  onStart?: () => void;
  onEnd?: () => void;
  onError?: (error: string) => void;
  rate?: number;
  pitch?: number;
  volume?: number;
  voice?: SpeechSynthesisVoice;
}

interface UseTextToSpeechReturn {
  isSpeaking: boolean;
  isSupported: boolean;
  error: string | null;
  speak: (text: string) => void;
  stop: () => void;
  pause: () => void;
  resume: () => void;
  voices: SpeechSynthesisVoice[];
  setVoice: (voice: SpeechSynthesisVoice) => void;
  setRate: (rate: number) => void;
  setPitch: (pitch: number) => void;
  setVolume: (volume: number) => void;
}

export function useTextToSpeech(
  options: UseTextToSpeechOptions = {}
): UseTextToSpeechReturn {
  const {
    onStart,
    onEnd,
    onError,
    rate = 1,
    pitch = 1,
    volume = 1,
  } = options;

  const [isSpeaking, setIsSpeaking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const voicesRef = useRef<SpeechSynthesisVoice[]>([]);
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null);
  const pendingTextRef = useRef<string | null>(null);
  const startTimeoutRef = useRef<number | null>(null);
  const startTokenRef = useRef(0);
  const hasStartedRef = useRef(false);

  // Check browser support
  const isSupported = typeof window !== "undefined" && "speechSynthesis" in window;

  const startSpeech = useCallback(
    (text: string, availableVoices: SpeechSynthesisVoice[]) => {
      startTokenRef.current += 1;
      const startToken = startTokenRef.current;
      if (startTimeoutRef.current !== null) {
        window.clearTimeout(startTimeoutRef.current);
      }
      hasStartedRef.current = false;

      // Cancel any ongoing speech
      window.speechSynthesis.cancel();
      if (window.speechSynthesis.paused) {
        window.speechSynthesis.resume();
      }

      const utterance = new SpeechSynthesisUtterance(text);
      utteranceRef.current = utterance;

      const selectedVoice =
        availableVoices.find((voice) => voice.default) ||
        availableVoices.find((voice) =>
          voice.lang?.toLowerCase().startsWith("en")
        ) ||
        availableVoices[0];
      if (selectedVoice) {
        utterance.voice = selectedVoice;
      }
      utterance.rate = rate;
      utterance.pitch = pitch;
      utterance.volume = volume;

      utterance.onstart = () => {
        if (startToken !== startTokenRef.current) return;
        hasStartedRef.current = true;
        setError(null);
        setIsSpeaking(true);
        if (onStart) onStart();
      };

      utterance.onend = () => {
        if (startToken !== startTokenRef.current) return;
        setIsSpeaking(false);
        if (onEnd) onEnd();
      };

      utterance.onerror = (event) => {
        if (startToken !== startTokenRef.current) return;
        setIsSpeaking(false);
        setError(event.error || "Speech synthesis error");
        if (onError) onError(event.error);
      };

      setIsSpeaking(true);
      window.speechSynthesis.speak(utterance);

      startTimeoutRef.current = window.setTimeout(() => {
        if (startToken !== startTokenRef.current) return;
        if (!hasStartedRef.current) {
          setIsSpeaking(false);
          if (voicesRef.current.length === 0) {
            setError("No speech voices available");
            if (onError) onError("No speech voices available");
          } else {
            setError("Speech synthesis failed to start");
            if (onError) onError("Speech synthesis failed to start");
          }
          window.speechSynthesis.cancel();
          return;
        }
        if (!window.speechSynthesis.speaking && !window.speechSynthesis.pending) {
          window.speechSynthesis.cancel();
          window.speechSynthesis.speak(utterance);
          window.setTimeout(() => {
            if (startToken !== startTokenRef.current) return;
            if (!window.speechSynthesis.speaking && !window.speechSynthesis.pending) {
              setIsSpeaking(false);
              setError("Speech synthesis failed to start");
              if (onError) onError("Speech synthesis failed to start");
            }
          }, 600);
        }
      }, 1500);
    },
    [onStart, onEnd, onError, rate, pitch, volume]
  );

  // Load available voices
  useEffect(() => {
    if (!isSupported) return;

    const loadVoices = () => {
      const availableVoices = window.speechSynthesis.getVoices();
      voicesRef.current = availableVoices;
      setVoices(availableVoices);

      if (pendingTextRef.current && availableVoices.length > 0) {
        const pendingText = pendingTextRef.current;
        pendingTextRef.current = null;
        startSpeech(pendingText, availableVoices);
      }
    };

    loadVoices();

    // Chrome loads voices asynchronously
    const speechSynthesis = window.speechSynthesis;
    if (typeof speechSynthesis.addEventListener === "function") {
      speechSynthesis.addEventListener("voiceschanged", loadVoices);
    } else {
      speechSynthesis.onvoiceschanged = loadVoices;
    }

    return () => {
      if (typeof speechSynthesis.removeEventListener === "function") {
        speechSynthesis.removeEventListener("voiceschanged", loadVoices);
      } else {
        speechSynthesis.onvoiceschanged = null;
      }
    };
  }, [isSupported, startSpeech]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      if (isSupported) {
        window.speechSynthesis.cancel();
      }
    };
  }, [isSupported]);

  const speak = useCallback(
    (text: string) => {
      if (!isSupported || !text.trim()) {
        setError("Text-to-speech not supported");
        if (onError) onError("Text-to-speech not supported");
        return;
      }

      setError(null);
      let availableVoices = voicesRef.current;
      if (availableVoices.length === 0) {
        availableVoices = window.speechSynthesis.getVoices();
        voicesRef.current = availableVoices;
      }

      startSpeech(text, availableVoices);

      if (availableVoices.length === 0) {
        window.setTimeout(() => {
          if (!window.speechSynthesis.speaking) {
            if (voicesRef.current.length === 0) {
              setError("No speech voices available");
            }
            pendingTextRef.current = text;
            window.speechSynthesis.getVoices();
          }
        }, 300);
      }
    },
    [isSupported, onError, startSpeech]
  );

  const stop = useCallback(() => {
    if (!isSupported) return;
    if (startTimeoutRef.current !== null) {
      window.clearTimeout(startTimeoutRef.current);
      startTimeoutRef.current = null;
    }
    window.speechSynthesis.cancel();
    setIsSpeaking(false);
  }, [isSupported]);

  const pause = useCallback(() => {
    if (!isSupported) return;
    window.speechSynthesis.pause();
  }, [isSupported]);

  const resume = useCallback(() => {
    if (!isSupported) return;
    window.speechSynthesis.resume();
  }, [isSupported]);

  const setRate = useCallback((newRate: number) => {
    if (utteranceRef.current) {
      utteranceRef.current.rate = newRate;
    }
  }, []);

  const setPitch = useCallback((newPitch: number) => {
    if (utteranceRef.current) {
      utteranceRef.current.pitch = newPitch;
    }
  }, []);

  const setVolume = useCallback((newVolume: number) => {
    if (utteranceRef.current) {
      utteranceRef.current.volume = newVolume;
    }
  }, []);

  const setVoice = useCallback((voice: SpeechSynthesisVoice) => {
    if (utteranceRef.current) {
      utteranceRef.current.voice = voice;
    }
  }, []);

  return {
    isSpeaking,
    isSupported,
    error,
    speak,
    stop,
    pause,
    resume,
    voices,
    setVoice,
    setRate,
    setPitch,
    setVolume,
  };
}
