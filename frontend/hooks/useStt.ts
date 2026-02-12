"use client";

import { useRef, useCallback, useState, useEffect } from "react";

interface UseSttOptions {
  onTranscript?: (text: string) => void;
  onPartialTranscript?: (text: string) => void;
  onError?: (error: Error) => void;
  language?: string;
}

interface UseSttState {
  isRecording: boolean;
  isConnecting: boolean;
  error: Error | null;
}

export function useStt(options: UseSttOptions = {}) {
  const { onTranscript, onPartialTranscript, onError, language = "en" } = options;
  
  const [state, setState] = useState<UseSttState>({
    isRecording: false,
    isConnecting: false,
    error: null,
  });
  
  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const stop = useCallback(() => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;

    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        message_type: "input_audio_chunk",
        audio_base_64: "",
        sample_rate: 16000,
        commit: true,
      }));
    }
    
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.close();
    }
    wsRef.current = null;
    
    mediaRecorderRef.current?.stop();
    mediaRecorderRef.current = null;
    
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = null;
    
    audioContextRef.current?.close();
    audioContextRef.current = null;
    
    setState({ isRecording: false, isConnecting: false, error: null });
  }, []);

  const start = useCallback(async () => {
    stop();
    
    setState({ isRecording: false, isConnecting: true, error: null });
    abortControllerRef.current = new AbortController();
    
    try {
      // Get mic permission
      const stream = await navigator.mediaDevices.getUserMedia({ 
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        }
      });
      streamRef.current = stream;
      
      // Get token
      const tokenResponse = await fetch("/api/audio/scribe-token");
      if (!tokenResponse.ok) throw new Error("Failed to get Scribe token");
      const { token } = await tokenResponse.json();
      
      // Connect to ElevenLabs Scribe
      const wsUrl = new URL("wss://api.elevenlabs.io/v1/speech-to-text/realtime");
      wsUrl.searchParams.set("model_id", "scribe_v2_realtime");
      wsUrl.searchParams.set("token", token);
      wsUrl.searchParams.set("audio_format", "pcm_16000");
      wsUrl.searchParams.set("sample_rate", "16000");
      wsUrl.searchParams.set("language_code", language);
      wsUrl.searchParams.set("commit_strategy", "manual");

      const ws = new WebSocket(wsUrl.toString());
      wsRef.current = ws;
      
      await new Promise<void>((resolve, reject) => {
        ws.onopen = () => resolve();
        ws.onerror = () => reject(new Error("WebSocket connection failed"));
        setTimeout(() => reject(new Error("Connection timeout")), 10000);
      });
      
      ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        const msgType = message.message_type || message.type;
        const text = message.text || message.transcript;

        if (msgType === "partial_transcript" && text) {
          onPartialTranscript?.(text);
        } else if (msgType === "committed_transcript" && text) {
          onTranscript?.(text);
        } else if (message.error) {
          const error = new Error(message.error);
          setState(prev => ({ ...prev, error }));
          onError?.(error);
        }
      };
      
      ws.onerror = () => {
        const error = new Error("STT WebSocket error");
        setState(prev => ({ ...prev, error, isRecording: false, isConnecting: false }));
        onError?.(error);
      };

      ws.onclose = () => {
        setState(prev => ({ ...prev, isRecording: false, isConnecting: false }));
      };
      
      // Setup audio recording
      const audioContext = new AudioContext({ sampleRate: 16000 });
      if (audioContext.state === "suspended") {
        await audioContext.resume();
      }
      audioContextRef.current = audioContext;
      
      const source = audioContext.createMediaStreamSource(stream);
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      
      processor.onaudioprocess = (e) => {
        if (ws.readyState !== WebSocket.OPEN) return;
        
        const inputData = e.inputBuffer.getChannelData(0);
        const pcmData = new Int16Array(inputData.length);
        
        // Convert float32 to int16 PCM
        for (let i = 0; i < inputData.length; i++) {
          const s = Math.max(-1, Math.min(1, inputData[i]));
          pcmData[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }
        
        // Base64 encode
        const base64 = btoa(String.fromCharCode(...new Uint8Array(pcmData.buffer)));
        
        ws.send(JSON.stringify({
          message_type: "input_audio_chunk",
          audio_base_64: base64,
          sample_rate: 16000,
          commit: false,
        }));
      };
      
      source.connect(processor);
      processor.connect(audioContext.destination);
      
      setState({ isRecording: true, isConnecting: false, error: null });
      
    } catch (error) {
      const err = error instanceof Error ? error : new Error(String(error));
      setState({ isRecording: false, isConnecting: false, error: err });
      onError?.(err);
      stop();
    }
  }, [language, onTranscript, onPartialTranscript, onError, stop]);

  useEffect(() => {
    return () => stop();
  }, [stop]);

  return {
    ...state,
    start,
    stop,
  };
}
