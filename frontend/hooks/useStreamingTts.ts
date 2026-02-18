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
  isConnecting: boolean;
  error: Error | null;
}

export function useStreamingTts(options: StreamingTtsOptions = {}) {
  const { voice = "Xb7hH8MSUJpSbSDYk0k2", model = "eleven_flash_v2_5", speed = 1.0, onStart, onStop, onError } = options;
  
  const [state, setState] = useState<StreamingTtsState>({
    isStreaming: false,
    isPlaying: false,
    isConnecting: false,
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
    
    setState({ isStreaming: false, isPlaying: false, isConnecting: false, error: null });
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

  const startStreaming = useCallback(async () => {
    stop();

    setState({ isStreaming: false, isPlaying: false, isConnecting: true, error: null });
    onStart?.();

    abortControllerRef.current = new AbortController();

    try {
      // Fetch token for WebSocket auth
      const storedDaemonKey = localStorage.getItem("daemon_api_key")?.trim();
      const tokenResponse = await fetch("/api/audio/token", {
        headers: storedDaemonKey ? { Authorization: `Bearer ${storedDaemonKey}` } : undefined,
      });
      
      if (!tokenResponse.ok) {
        throw new Error("Failed to get audio token");
      }
      
      const { token } = await tokenResponse.json();
      
      // Connect to ElevenLabs WebSocket with scoped token
      const ws = new WebSocket(
        `wss://api.elevenlabs.io/v1/text-to-speech/${voice}/stream-input?model_id=${model}&single_use_token=${token}`
      );
      wsRef.current = ws;
      
      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error("WebSocket connection failed"));
        
        // Timeout after 10 seconds
        setTimeout(() => reject(new Error("WebSocket connection timeout")), 10000);
      });
      
      // Send initial config
      ws.send(JSON.stringify({
        text: " ",
        voice_settings: { stability: 0.5, similarity_boost: 0.75 },
      }));

      setState((prev) => ({ ...prev, isStreaming: true, isConnecting: false }));
      
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
          setState((prev) => ({ ...prev, isStreaming: false }));
        }
      };
      
      ws.onerror = () => {
        setState((prev) => ({ ...prev, error: new Error("WebSocket error") }));
        onError?.(new Error("WebSocket error"));
      };
      
      ws.onclose = () => {
        setState((prev) => ({ ...prev, isStreaming: false, isPlaying: false, isConnecting: false }));
      };
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      setState((prev) => ({ ...prev, error: err, isStreaming: false, isConnecting: false }));
      onError?.(err);
    }
  }, [voice, model, stop, processAudioQueue, onStart, onError]);

  const sendTextChunk = useCallback((chunk: string) => {
    if (!chunk) return;
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    textBufferRef.current += chunk;
    const SENTENCE_ENDERS = /[.!?\n]+/;
    let match = textBufferRef.current.match(SENTENCE_ENDERS);
    while (match) {
      const splitIndex = match.index! + match[0].length;
      const sentence = textBufferRef.current.slice(0, splitIndex).trim();
      textBufferRef.current = textBufferRef.current.slice(splitIndex);

      if (sentence) {
        wsRef.current.send(JSON.stringify({ text: `${sentence} ` }));
      }

      match = textBufferRef.current.match(SENTENCE_ENDERS);
    }
  }, []);

  const flushBuffer = useCallback(() => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const remaining = textBufferRef.current.trim();
    if (remaining) {
      wsRef.current.send(JSON.stringify({ text: `${remaining} ` }));
      textBufferRef.current = "";
    }
    wsRef.current.send(JSON.stringify({ text: "" }));
  }, []);

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
    sendTextChunk,
    flushBuffer,
  };
}
