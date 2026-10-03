'use client';

import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  ensureAuthHeader,
  getAuthGeneration,
  subscribeAuthGeneration,
} from '../lib/auth';
import { MAX_TTS_TEXT_CODE_POINTS } from '../lib/constants';

/**
 * AudioPlaybackProvider owns the whole buffered read-aloud lifecycle:
 * synthesis POST, protected download, HTMLAudioElement creation and playback,
 * under one monotonically increasing request generation.
 *
 * Invariants this component is responsible for:
 *
 * - Exactly one active owner. Selecting another request, stopping, a committed
 *   conversation/auth scope change, owner unmount or content change invalidates
 *   the previous owner BEFORE aborting, pausing or revoking anything.
 * - Every await, media event, rejected promise and catch/finally re-checks the
 *   captured owner identity and auth generation. A stale continuation is a
 *   no-op: it never creates media, never updates state and never stops a
 *   newer owner.
 * - The POST and the protected download share the owner's AbortSignal.
 * - Speed is synthesized once on the server; playback rate is left at 1.
 */

export { MAX_TTS_TEXT_CODE_POINTS } from '../lib/constants';

export const TTS_TEXT_TOO_LONG_MESSAGE = `Speech is limited to ${MAX_TTS_TEXT_CODE_POINTS} characters`;

export const TTS_STALE_CONVERSATION_MESSAGE =
  'This message is no longer part of the current conversation';

export const TTS_STREAMING_MESSAGE =
  'Speech is available once the response is complete';

export type TtsPhase =
  | 'idle'
  | 'synthesizing'
  | 'downloading'
  | 'starting'
  | 'playing'
  | 'error';

export interface TtsOwner {
  messageId: string;
  conversationId: string | null;
  /** Guards a cleanup so it cannot cancel a newer request for the same message. */
  requestId?: number | null;
}

export interface TtsRequest extends TtsOwner {
  /** Frozen at click time; mutable rendered text is never the identity. */
  text: string;
  voice: string;
  speed: number;
  format: string;
}

export interface TtsScope {
  conversationId: string | null;
  authGeneration: number;
}

interface TtsState {
  phase: TtsPhase;
  messageId: string | null;
  conversationId: string | null;
  errorMessage: string | null;
}

interface AudioPlaybackContextValue {
  phase: TtsPhase;
  ownerMessageId: string | null;
  ownerConversationId: string | null;
  errorMessage: string | null;
  /** Returns the new request id, or null when the request is refused. */
  startSpeech: (request: TtsRequest) => number | null;
  stopSpeech: (owner: TtsOwner) => void;
}

const IDLE_STATE: TtsState = {
  phase: 'idle',
  messageId: null,
  conversationId: null,
  errorMessage: null,
};

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

/** Raw Unicode code points, matching the server's Python `len()` bound. */
export function countTtsTextCodePoints(text: string): number {
  return Array.from(text).length;
}

class TtsRequestError extends Error {}

function describeDetail(detail: unknown): string | null {
  if (typeof detail === 'string' && detail.trim()) return detail.trim();
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === 'string') return item.trim();
        if (!item || typeof item !== 'object') return '';
        const record = item as Record<string, unknown>;
        const message =
          typeof record.msg === 'string'
            ? record.msg
            : typeof record.code === 'string'
              ? record.code
              : '';
        const location = Array.isArray(record.loc)
          ? record.loc.filter((part) => typeof part === 'string').join('.')
          : '';
        if (!message) return '';
        return location ? `${location}: ${message}` : message;
      })
      .filter(Boolean);
    return parts.length ? parts.join('; ') : null;
  }
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>;
    if (typeof record.msg === 'string' && record.msg.trim()) {
      return record.msg.trim();
    }
    if (typeof record.code === 'string' && record.code.trim()) {
      return record.code.trim();
    }
    if (typeof record.detail === 'string' && record.detail.trim()) {
      return record.detail.trim();
    }
  }
  return null;
}

/** POST failure text: server error objects and Pydantic detail arrays. */
export function describeTtsFailure(status: number, payload: unknown): string {
  const detail = describeDetail(
    payload && typeof payload === 'object'
      ? (payload as Record<string, unknown>).detail
      : undefined,
  );
  return detail ? `${detail} (${status})` : `Speech unavailable (${status})`;
}

/** Protected download failure text; a 401 here is still a visible failure. */
export function describeAudioDownloadFailure(status: number): string {
  if (status === 401) return 'Speech audio request was not authorized (401)';
  if (status === 403) return 'Speech audio request was forbidden (403)';
  if (status === 404) return 'Speech audio is no longer available (404)';
  return `Speech audio download failed (${status})`;
}

export const TTS_DECODE_ERROR_MESSAGE = 'Speech audio could not be decoded';
export const TTS_PLAY_ERROR_MESSAGE = 'Speech playback could not start';

function describeUnexpectedFailure(error: unknown): string {
  if (error instanceof TtsRequestError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return 'Speech unavailable';
}

async function readJsonSafely(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

interface AudioPlaybackProviderProps {
  children: ReactNode;
  scope?: TtsScope;
}

interface ActiveSpeech {
  requestId: number;
  messageId: string;
  conversationId: string | null;
  authGeneration: number;
  controller: AbortController;
  audio: HTMLAudioElement | null;
  objectUrl: string | null;
}

export function AudioPlaybackProvider({
  children,
  scope,
}: AudioPlaybackProviderProps) {
  const resolvedScope: TtsScope = useMemo(
    () => scope ?? { conversationId: null, authGeneration: 0 },
    [scope],
  );

  const [state, setState] = useState<TtsState>(IDLE_STATE);
  const activeRef = useRef<ActiveSpeech | null>(null);
  const audioHostRef = useRef<HTMLDivElement | null>(null);
  const scopeRef = useRef<TtsScope>(resolvedScope);
  const nextRequestIdRef = useRef(0);
  const mountedRef = useRef(true);
  const authGenerationRef = useRef(resolvedScope.authGeneration);

  const publishIdle = useCallback(() => {
    if (!mountedRef.current) return;
    setState(IDLE_STATE);
  }, []);

  /**
   * Detach and free exactly one owner's resources. Only the current owner can
   * be released, so a late cleanup cannot cancel its successor.
   */
  const releaseActive = useCallback((active: ActiveSpeech) => {
    if (activeRef.current !== active) return;
    activeRef.current = null;
    active.controller.abort();

    const { audio, objectUrl } = active;
    active.audio = null;
    active.objectUrl = null;

    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.oncanplay = null;
      audio.onplaying = null;
      audio.onpause = null;
      audio.onstalled = null;
      try {
        audio.pause();
      } catch {
        // A detached element cannot pause; nothing else to release.
      }
      try {
        // Cancel queued native play events and release decoder resources before
        // unparenting. Otherwise an extension's late play listener could see a
        // retired player as detached and try to replay it after Stop.
        audio.removeAttribute('src');
        audio.load();
      } catch {
        // Continue DOM/blob cleanup even if a browser cannot reset its player.
      }
      // Owner identity and media handlers have already been retired. Removing
      // this element cannot turn an intentional Stop into a playback error.
      audio.remove();
    }
    if (objectUrl) {
      try {
        URL.revokeObjectURL(objectUrl);
      } catch {
        // Already revoked by the environment.
      }
    }
  }, []);

  /** Invalidate the current owner and return the provider to idle. */
  const cancelSpeech = useCallback(() => {
    const active = activeRef.current;
    if (active) releaseActive(active);
    publishIdle();
  }, [publishIdle, releaseActive]);

  const publishPhase = useCallback(
    (active: ActiveSpeech, phase: TtsPhase, errorMessage: string | null) => {
      if (activeRef.current !== active) return;
      if (!mountedRef.current) return;
      setState({
        phase,
        messageId: active.messageId,
        conversationId: active.conversationId,
        errorMessage,
      });
    },
    [],
  );

  const publishError = useCallback(
    (
      messageId: string,
      conversationId: string | null,
      errorMessage: string,
    ) => {
      if (!mountedRef.current) return;
      setState({ phase: 'error', messageId, conversationId, errorMessage });
    },
    [],
  );

  const failActive = useCallback(
    (active: ActiveSpeech, message: string) => {
      if (activeRef.current !== active) return;
      releaseActive(active);
      publishError(active.messageId, active.conversationId, message);
    },
    [publishError, releaseActive],
  );

  const runSpeech = useCallback(
    async (active: ActiveSpeech, request: TtsRequest) => {
      const signal = active.controller.signal;
      // Auth generation is captured per request: a sign-in/logout or remote
      // invalidation retires the request instead of reporting a stale failure.
      const guard = () =>
        mountedRef.current &&
        activeRef.current === active &&
        scopeRef.current.conversationId === active.conversationId &&
        scopeRef.current.authGeneration === active.authGeneration &&
        getAuthGeneration() === active.authGeneration;

      try {
        const authHeader = await ensureAuthHeader();
        if (!guard()) return;

        const postHeaders: Record<string, string> = {
          'Content-Type': 'application/json',
        };
        if (authHeader) postHeaders.Authorization = authHeader;

        const response = await fetch('/api/tts', {
          method: 'POST',
          headers: postHeaders,
          signal,
          body: JSON.stringify({
            text: request.text,
            voice: request.voice,
            speed: request.speed,
            format: request.format,
            cache: true,
          }),
        });
        if (!guard()) return;

        const payload = await readJsonSafely(response);
        if (!guard()) return;
        if (!response.ok) {
          throw new TtsRequestError(
            describeTtsFailure(response.status, payload),
          );
        }

        const audioPath =
          payload && typeof payload === 'object'
            ? (payload as Record<string, unknown>).audio_path
            : undefined;
        if (typeof audioPath !== 'string' || !audioPath) {
          throw new TtsRequestError('Speech audio was not returned');
        }

        // TTS artifacts are always authenticated, including production builds
        // without an explicit API URL. Never treat a returned URL as public media.
        if (
          !/^\/generated-audio\/[A-Za-z0-9_-]+\.(?:mp3|wav|opus)$(?![\s\S])/.test(
            audioPath,
          )
        ) {
          throw new TtsRequestError('Speech audio path was invalid');
        }
        const apiUrl = (
          process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
        ).replace(/\/+$/, '');
        const protectedUrl = `${apiUrl}${audioPath}`;

        publishPhase(active, 'downloading', null);
        const downloadHeader = await ensureAuthHeader();
        if (!guard()) return;

        const downloadHeaders: Record<string, string> = {};
        if (downloadHeader) downloadHeaders.Authorization = downloadHeader;

        const download = await fetch(protectedUrl, {
          headers: downloadHeaders,
          signal,
          redirect: 'error',
        });
        if (!guard()) return;
        if (!download.ok) {
          throw new TtsRequestError(
            describeAudioDownloadFailure(download.status),
          );
        }
        const blob = await download.blob();
        if (!guard()) return;

        const source = URL.createObjectURL(blob);
        active.objectUrl = source;

        if (!guard()) return;

        const audio = new Audio(source);
        active.audio = audio;
        const host = audioHostRef.current;
        if (!host?.isConnected) {
          throw new TtsRequestError(TTS_PLAY_ERROR_MESSAGE);
        }
        // KDE Plasma Integration briefly inserts/removes otherwise detached
        // players when play fires. That removal rejects the pending play promise.
        // Give each owner a stable document parent BEFORE playback instead.
        host.appendChild(audio);
        publishPhase(active, 'starting', null);

        audio.onplaying = () => {
          if (!guard()) return;
          publishPhase(active, 'playing', null);
        };
        audio.onended = () => {
          if (!guard()) return;
          releaseActive(active);
          publishIdle();
        };
        audio.onerror = () => {
          if (!guard()) return;
          failActive(active, TTS_DECODE_ERROR_MESSAGE);
        };

        let playPromise: Promise<void>;
        try {
          // Speed is applied on the server only; playback rate stays 1.
          playPromise = audio.play();
        } catch (error) {
          if (!guard()) return;
          failActive(
            active,
            error instanceof Error && error.message
              ? `${TTS_PLAY_ERROR_MESSAGE}: ${error.message}`
              : TTS_PLAY_ERROR_MESSAGE,
          );
          return;
        }
        void Promise.resolve(playPromise).then(
          () => {
            if (guard()) publishPhase(active, 'playing', null);
          },
          (error: unknown) => {
            if (!guard()) return;
            failActive(
              active,
              error instanceof Error && error.message
                ? `${TTS_PLAY_ERROR_MESSAGE}: ${error.message}`
                : TTS_PLAY_ERROR_MESSAGE,
            );
          },
        );
      } catch (error) {
        // A superseded owner must not report or stop its successor.
        if (!guard()) return;
        if (signal.aborted) return;
        failActive(active, describeUnexpectedFailure(error));
      }
    },
    [failActive, publishIdle, publishPhase, releaseActive],
  );

  const startSpeech = useCallback(
    (request: TtsRequest): number | null => {
      // Invalidate the previous owner before any abort/pause/revoke.
      cancelSpeech();

      const currentScope = scopeRef.current;
      // Do not synthesize text still rendered from the retired sign-in while
      // React has yet to commit the new authentication scope.
      if (currentScope.authGeneration !== getAuthGeneration()) return null;
      if (request.conversationId !== currentScope.conversationId) {
        publishError(
          request.messageId,
          request.conversationId,
          TTS_STALE_CONVERSATION_MESSAGE,
        );
        return null;
      }
      if (countTtsTextCodePoints(request.text) > MAX_TTS_TEXT_CODE_POINTS) {
        publishError(
          request.messageId,
          request.conversationId,
          TTS_TEXT_TOO_LONG_MESSAGE,
        );
        return null;
      }

      nextRequestIdRef.current += 1;
      const active: ActiveSpeech = {
        requestId: nextRequestIdRef.current,
        messageId: request.messageId,
        conversationId: request.conversationId,
        authGeneration: getAuthGeneration(),
        controller: new AbortController(),
        audio: null,
        objectUrl: null,
      };
      activeRef.current = active;
      publishPhase(active, 'synthesizing', null);
      void runSpeech(active, request);
      return active.requestId;
    },
    [cancelSpeech, publishError, publishPhase, runSpeech],
  );

  const stopSpeech = useCallback(
    (owner: TtsOwner) => {
      const active = activeRef.current;
      if (!active) return;
      if (active.messageId !== owner.messageId) return;
      if (active.conversationId !== owner.conversationId) return;
      if (owner.requestId != null && active.requestId !== owner.requestId) {
        return;
      }
      cancelSpeech();
    },
    [cancelSpeech],
  );

  useLayoutEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const active = activeRef.current;
      if (active) releaseActive(active);
    };
  }, [releaseActive]);

  // Imperative generation subscription: already-playing audio stops on an auth
  // change even before React rerenders the scope.
  useLayoutEffect(
    () =>
      subscribeAuthGeneration(() => {
        const generation = getAuthGeneration();
        if (generation === authGenerationRef.current) return;
        authGenerationRef.current = generation;
        cancelSpeech();
      }),
    [cancelSpeech],
  );

  useLayoutEffect(() => {
    scopeRef.current = resolvedScope;
  }, [resolvedScope]);

  // Committed scope change invalidates prior work without remounting ChatContent.
  useLayoutEffect(() => {
    cancelSpeech();
  }, [
    cancelSpeech,
    resolvedScope.authGeneration,
    resolvedScope.conversationId,
  ]);

  const value = useMemo<AudioPlaybackContextValue>(
    () => ({
      phase: state.phase,
      ownerMessageId: state.messageId,
      ownerConversationId: state.conversationId,
      errorMessage: state.errorMessage,
      startSpeech,
      stopSpeech,
    }),
    [state, startSpeech, stopSpeech],
  );

  return (
    <AudioPlaybackContext.Provider value={value}>
      <div ref={audioHostRef} hidden aria-hidden="true" />
      {children}
    </AudioPlaybackContext.Provider>
  );
}
