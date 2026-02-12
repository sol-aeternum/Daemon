"use client";

import { useState, useCallback, useRef, useEffect } from "react";

// Web Speech API types
declare global {
  interface Window {
    webkitSpeechRecognition: typeof SpeechRecognition;
  }
}

interface UseSpeechRecognitionOptions {
  onResult?: (transcript: string, isFinal: boolean) => void;
  onError?: (error: string) => void;
  language?: string;
}

interface UseSpeechRecognitionReturn {
  isListening: boolean;
  transcript: string;
  isSupported: boolean;
  startListening: () => void;
  stopListening: () => void;
  error: string | null;
}

export function useSpeechRecognition(
  options: UseSpeechRecognitionOptions = {}
): UseSpeechRecognitionReturn {
  const { onResult, onError, language = "en-US" } = options;
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<SpeechRecognition | null>(null);

  // Check browser support
  const isSupported = typeof window !== "undefined" && 
    ("webkitSpeechRecognition" in window || "SpeechRecognition" in window);

  useEffect(() => {
    if (!isSupported) return;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognitionRef.current = new SpeechRecognition();
    const recognition = recognitionRef.current;

    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = language;

    recognition.onstart = () => {
      setIsListening(true);
      setError(null);
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let interimTranscript = "";
      let finalTranscript = "";

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const text = result[0].transcript;
        if (result.isFinal) {
          finalTranscript += text;
        } else {
          interimTranscript += text;
        }
      }

      const currentTranscript = finalTranscript || interimTranscript;
      setTranscript(currentTranscript);

      if (onResult) {
        onResult(currentTranscript, !!finalTranscript);
      }
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      const errorMessage = getErrorMessage(event.error);
      setError(errorMessage);
      setIsListening(false);
      if (onError) {
        onError(errorMessage);
      }
    };

    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
      }
    };
  }, [isSupported, language, onResult, onError]);

  const startListening = useCallback(() => {
    if (!recognitionRef.current || isListening) return;
    
    setTranscript("");
    setError(null);
    
    try {
      recognitionRef.current.start();
    } catch (err) {
      setError("Failed to start speech recognition");
      if (onError) {
        onError("Failed to start speech recognition");
      }
    }
  }, [isListening, onError]);

  const stopListening = useCallback(() => {
    if (!recognitionRef.current || !isListening) return;
    recognitionRef.current.stop();
  }, [isListening]);

  return {
    isListening,
    transcript,
    isSupported,
    startListening,
    stopListening,
    error,
  };
}

function getErrorMessage(error: string): string {
  const errorMessages: Record<string, string> = {
    "no-speech": "No speech detected. Try speaking again.",
    "audio-capture": "Microphone not available or denied.",
    "not-allowed": "Microphone permission denied.",
    "network": "Network error. Check your connection.",
    "aborted": "Speech recognition aborted.",
    "language-not-supported": "Language not supported.",
  };
  return errorMessages[error] || `Speech recognition error: ${error}`;
}
