"use client";

import { Mic, MicOff, Loader2 } from "lucide-react";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";

interface VoiceInputButtonProps {
  onTranscript: (text: string) => void;
  disabled?: boolean;
}

export function VoiceInputButton({ onTranscript, disabled }: VoiceInputButtonProps) {
  const { isListening, isSupported, startListening, stopListening, error } =
    useSpeechRecognition({
      onResult: (transcript, isFinal) => {
        if (isFinal) {
          onTranscript(transcript);
          stopListening();
        }
      },
    });

  if (!isSupported) {
    return null;
  }

  return (
    <button
      type="button"
      onClick={isListening ? stopListening : startListening}
      disabled={disabled}
      className={`
        p-2 rounded-lg transition-colors
        ${isListening 
          ? "bg-red-500 text-white animate-pulse" 
          : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
        }
        ${disabled ? "opacity-50 cursor-not-allowed" : ""}
      `}
      title={isListening ? "Stop listening" : "Voice input"}
    >
      {isListening ? (
        <Loader2 className="w-5 h-5 animate-spin" />
      ) : (
        <Mic className="w-5 h-5" />
      )}
    </button>
  );
}
