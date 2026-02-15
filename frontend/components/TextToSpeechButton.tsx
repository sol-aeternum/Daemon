"use client";

import { useRef, useState, useCallback, useEffect } from "react";
import { Volume2, VolumeX } from "lucide-react";
import { useLocalStorage } from "../hooks/useLocalStorage";

type TtsSettings = {
  enabled: boolean;
  voice: string;
  model: string;
  speed: number;
  format: string;
};

const DEFAULT_TTS_SETTINGS: TtsSettings = {
  enabled: true,
  voice: "Xb7hH8MSUJpSbSDYk0k2",
  model: "eleven_flash_v2_5",
  speed: 1.0,
  format: "mp3",
};

const globalAudioRef = { current: null as HTMLAudioElement | null };
const globalPlayingTextRef = { current: null as string | null };

interface TextToSpeechButtonProps {
  text: string;
  streamingText?: AsyncIterable<string>;
}

export function TextToSpeechButton({ text, streamingText }: TextToSpeechButtonProps) {
  const { value: storedSettings } = useLocalStorage<TtsSettings>(
    "tts_settings",
    DEFAULT_TTS_SETTINGS
  );
  const settings = storedSettings || DEFAULT_TTS_SETTINGS;
  const [isPlaying, setIsPlaying] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isProcessingRef = useRef(false);

  useEffect(() => {
    const checkGlobalState = () => {
      const isThisTextPlaying = globalPlayingTextRef.current === text && globalAudioRef.current !== null;
      setIsPlaying(isThisTextPlaying);
    };
    
    checkGlobalState();
    const interval = setInterval(checkGlobalState, 100);
    return () => clearInterval(interval);
  }, [text]);

  if (!settings.enabled) return null;

  const stopGlobalAudio = () => {
    if (globalAudioRef.current) {
      globalAudioRef.current.pause();
      globalAudioRef.current.currentTime = 0;
      globalAudioRef.current.onended = null;
      globalAudioRef.current.onerror = null;
      globalAudioRef.current = null;
    }
    globalPlayingTextRef.current = null;
    setIsPlaying(false);
  };

  const playStreaming = useCallback(async () => {
    if (!streamingText) return;
    
    const rawSettings = localStorage.getItem("tts_settings");
    const currentSettings = rawSettings ? JSON.parse(rawSettings) : DEFAULT_TTS_SETTINGS;
    
    try {
      const tokenResponse = await fetch("/api/audio/token");
      if (!tokenResponse.ok) throw new Error("Failed to get audio token");
      const { token } = await tokenResponse.json();
      
      const voice = currentSettings.voice ?? DEFAULT_TTS_SETTINGS.voice;
      const model = currentSettings.model ?? DEFAULT_TTS_SETTINGS.model;
      const speed = currentSettings.speed ?? DEFAULT_TTS_SETTINGS.speed;
      
      const ws = new WebSocket(
        `wss://api.elevenlabs.io/v1/text-to-speech/${voice}/stream-input?model_id=${model}`
      );
      
      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error("WebSocket failed"));
        setTimeout(() => reject(new Error("Timeout")), 10000);
      });
      
      ws.send(JSON.stringify({
        text: " ",
        voice_settings: { stability: 0.5, similarity_boost: 0.75 },
        xi_api_key: token,
      }));
      
      const audioContext = new AudioContext();
      const audioQueue: AudioBuffer[] = [];
      let isPlayingAudio = false;
      
      const playQueue = async () => {
        if (isPlayingAudio || audioQueue.length === 0) return;
        isPlayingAudio = true;
        
        while (audioQueue.length > 0) {
          const buffer = audioQueue.shift();
          if (!buffer) continue;
          
          const source = audioContext.createBufferSource();
          source.buffer = buffer;
          source.playbackRate.value = speed;
          source.connect(audioContext.destination);
          
          await new Promise<void>((resolve) => {
            source.onended = () => resolve();
            source.start();
          });
        }
        
        isPlayingAudio = false;
      };
      
      ws.onmessage = async (event) => {
        const message = JSON.parse(event.data);
        
        if (message.audio) {
          const audioData = Uint8Array.from(atob(message.audio), c => c.charCodeAt(0));
          try {
            const audioBuffer = await audioContext.decodeAudioData(audioData.buffer);
            audioQueue.push(audioBuffer);
            playQueue();
          } catch (e) {
            console.error("Audio decode failed:", e);
          }
        }
        
        if (message.isFinal) {
          ws.close();
        }
      };
      
      ws.onerror = () => setError("Streaming error");
      ws.onclose = () => {
        setIsPlaying(false);
        isProcessingRef.current = false;
        setIsLoading(false);
      };
      
      let buffer = "";
      const SENTENCE_ENDERS = /[.!?\n]+/;
      
      for await (const chunk of streamingText) {
        buffer += chunk;
        const match = buffer.match(SENTENCE_ENDERS);
        
        if (match) {
          const splitIndex = match.index! + match[0].length;
          const sentence = buffer.slice(0, splitIndex).trim();
          buffer = buffer.slice(splitIndex);
          
          if (sentence && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ text: sentence + " " }));
          }
        }
      }
      
      if (buffer.trim() && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ text: buffer.trim() + " " }));
      }
      
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ text: "" }));
      }
      
    } catch (err) {
      setError(err instanceof Error ? err.message : "Streaming failed");
      setIsPlaying(false);
      isProcessingRef.current = false;
      setIsLoading(false);
    }
  }, [streamingText]);

  const playCached = useCallback(async () => {
    const rawSettings = localStorage.getItem("tts_settings");
    const currentSettings = rawSettings ? JSON.parse(rawSettings) : DEFAULT_TTS_SETTINGS;

    try {
      const response = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          voice: currentSettings.voice ?? DEFAULT_TTS_SETTINGS.voice,
          model: currentSettings.model ?? DEFAULT_TTS_SETTINGS.model,
          speed: currentSettings.speed ?? DEFAULT_TTS_SETTINGS.speed,
          format: currentSettings.format ?? DEFAULT_TTS_SETTINGS.format,
          cache: true,
        }),
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data?.detail || "TTS failed");

      const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const audioPath = data.audio_url || `${apiUrl}${data.audio_path}`;
      if (!audioPath) throw new Error("Audio URL missing");

      const audio = new Audio(audioPath);
      globalAudioRef.current = audio;
      globalPlayingTextRef.current = text;
      setIsPlaying(true);

      audio.playbackRate = currentSettings.speed ?? DEFAULT_TTS_SETTINGS.speed;
      audio.onended = () => {
        if (globalPlayingTextRef.current === text) {
          globalAudioRef.current = null;
          globalPlayingTextRef.current = null;
        }
        setIsPlaying(false);
        isProcessingRef.current = false;
      };
      audio.onerror = () => {
        setError("Playback failed");
        if (globalPlayingTextRef.current === text) {
          globalAudioRef.current = null;
          globalPlayingTextRef.current = null;
        }
        setIsPlaying(false);
        isProcessingRef.current = false;
      };
      await audio.play();
    } catch (err) {
      setError(err instanceof Error ? err.message : "TTS failed");
      setIsPlaying(false);
      isProcessingRef.current = false;
    } finally {
      setIsLoading(false);
    }
  }, [text]);

  const handleClick = async () => {
    if (!text?.trim()) return;

    if (isPlaying) {
      stopGlobalAudio();
      return;
    }

    if (globalAudioRef.current) stopGlobalAudio();
    if (isLoading || isProcessingRef.current) return;

    isProcessingRef.current = true;
    setIsLoading(true);
    setError(null);

    if (streamingText) {
      await playStreaming();
    } else {
      await playCached();
    }
  };

  const label = error ? `TTS: ${error}` : isPlaying ? "Stop TTS" : "Play TTS";

   return (
     <button
       type="button"
       onClick={handleClick}
       title={label}
       className={`ml-2 inline-flex items-center gap-1 rounded px-1 py-0.5 text-xs transition-colors ${
         error ? "text-red-500 hover:text-red-600" : "text-gray-400 hover:text-gray-200"
       }`}
     >
      {isPlaying ? <VolumeX className="h-3.5 w-3.5" /> : <Volume2 className="h-3.5 w-3.5" />}
      {isLoading ? "Loading..." : null}
    </button>
  );
}
