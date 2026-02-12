"use client";

import { useRef, useCallback, useEffect, useState } from "react";

interface StreamingTtsOptions {
  voice?: string;
  model?: string;
  speed?: number;
  onStart?: () => void;
  onStop?: () => void;
  onError?: (error: Error) => void;
}

interface StreamingTtsState {
  isStreaming: boolean;
  isPlaying: boolean;
  error: Error | null;
}

export function useStreamingTts(options: StreamingTtsOptions = {}) {
  const { voice = "Xb7hH8MSUJpSbSDYk0k2", model = "eleven_flash_v2_5", speed = 1.0, onStart, onStop, onError } = options;
  
  const [state, setState] = useState<StreamingTtsState>({
    isStreaming: false,
    isPlaying: false,
    error: null,
  });
  
  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const textBufferRef = useRef<string>("");
  const audioQueueRef = useRef<AudioBuffer[]>([]);
  const isPlayingRef = useRef<boolean>(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.close();
    }
    wsRef.current = null;
    
    textBufferRef.current = "";
    audioQueueRef.current = [];
    isPlayingRef.current = false;
    
    setState({ isStreaming: false, isPlaying: false, error: null });
    onStop?.();
  }, [onStop]);

  const playAudioBuffer = useCallback(async (audioBuffer: AudioBuffer) => {
    if (!audioContextRef.current) {
      audioContextRef.current = new AudioContext();
    }
    
    const ctx = audioContextRef.current;
    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.playbackRate.value = speed;
    
    source.connect(ctx.destination);
    
    return new Promise<void>((resolve) => {
      source.onended = () => resolve();
      source.start();
      setState((prev) => ({ ...prev, isPlaying: true }));
    });
  }, [speed]);

  const processAudioQueue = useCallback(async () => {
    if (isPlayingRef.current || audioQueueRef.current.length === 0) return;
    
    isPlayingRef.current = true;
    
    while (audioQueueRef.current.length > 0) {
      const buffer = audioQueueRef.current.shift();
      if (buffer) {
        await playAudioBuffer(buffer);
      }
    }
    
    isPlayingRef.current = false;
    setState((prev) => ({ ...prev, isPlaying: false }));
  }, [playAudioBuffer]);

  const startStreaming = useCallback(async (textStream: AsyncIterable<string>) => {
    stop(); // Clean up any existing stream
    
    setState({ isStreaming: true, isPlaying: false, error: null });
    onStart?.();
    
    abortControllerRef.current = new AbortController();
    const { signal } = abortControllerRef.current;
    
    try {
      // Fetch token for WebSocket auth
      const tokenResponse = await fetch("/api/audio/token", {
        headers: { Authorization: `Bearer ${localStorage.getItem("daemon_api_key") || ""}` },
      });
      
      if (!tokenResponse.ok) {
        throw new Error("Failed to get audio token");
      }
      
      const { token } = await tokenResponse.json();
      
      // Connect to ElevenLabs WebSocket
      const ws = new WebSocket(
        `wss://api.elevenlabs.io/v1/text-to-speech/${voice}/stream-input?model_id=${model}`
      );
      wsRef.current = ws;
      
      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error("WebSocket connection failed"));
        
        // Timeout after 10 seconds
        setTimeout(() => reject(new Error("WebSocket connection timeout")), 10000);
      });
      
      // Send initial config with auth
      ws.send(JSON.stringify({
        text: " ",
        voice_settings: { stability: 0.5, similarity_boost: 0.75 },
        xi_api_key: token,
      }));
      
      ws.onmessage = async (event) => {
        const message = JSON.parse(event.data);
        
        if (message.audio) {
          // Decode base64 audio and add to queue
          const audioData = Uint8Array.from(atob(message.audio), c => c.charCodeAt(0));
          
          if (!audioContextRef.current) {
            audioContextRef.current = new AudioContext();
          }
          
          try {
            const audioBuffer = await audioContextRef.current.decodeAudioData(audioData.buffer);
            audioQueueRef.current.push(audioBuffer);
            processAudioQueue();
          } catch (e) {
            console.error("Failed to decode audio:", e);
          }
        }
        
        if (message.isFinal) {
          // Stream complete
          setState((prev) => ({ ...prev, isStreaming: false }));
        }
      };
      
      ws.onerror = () => {
        setState((prev) => ({ ...prev, error: new Error("WebSocket error") }));
        onError?.(new Error("WebSocket error"));
      };
      
      ws.onclose = () => {
        setState((prev) => ({ ...prev, isStreaming: false, isPlaying: false }));
      };
      
      // Stream text with sentence boundary buffering
      const SENTENCE_ENDERS = /[.!?\n]+/;
      
      for await (const chunk of textStream) {
        if (signal.aborted) break;
        
        textBufferRef.current += chunk;
        
        // Check if we have a complete sentence
        const match = textBufferRef.current.match(SENTENCE_ENDERS);
        if (match) {
          const splitIndex = match.index! + match[0].length;
          const sentence = textBufferRef.current.slice(0, splitIndex).trim();
          textBufferRef.current = textBufferRef.current.slice(splitIndex);
          
          if (sentence && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ text: sentence + " " }));
          }
        }
      }
      
      // Flush remaining buffer
      if (textBufferRef.current.trim() && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ text: textBufferRef.current.trim() + " " }));
      }
      
      // Signal end of stream
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ text: "" }));
      }
      
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      setState((prev) => ({ ...prev, error: err, isStreaming: false }));
      onError?.(err);
    }
  }, [voice, model, stop, processAudioQueue, onStart, onError]);

  useEffect(() => {
    return () => {
      stop();
      audioContextRef.current?.close();
    };
  }, [stop]);

  return {
    ...state,
    startStreaming,
    stop,
  };
}
