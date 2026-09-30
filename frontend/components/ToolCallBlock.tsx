import { useState, useRef, useEffect, useId } from 'react';
import Image from 'next/image';
import { ChatEvent, isToolCallEvent, isToolResultEvent } from '../lib/events';
import { ensureAuthHeader } from '../lib/auth';
import {
  Download,
  Maximize2,
  X,
  Loader2,
  ChevronRight,
  ChevronDown,
  Check,
  Volume2,
  Play,
  Pause,
  Palette,
} from 'lucide-react';
import { VideoPlayer } from './VideoPlayer';
import { useAuthenticatedImageUrl } from '../hooks/useAuthenticatedImageUrl';
import { buildMessageCitationSources } from '../lib/messageSources';
import {
  buildToolActivitySummary,
  pairToolExecutions,
  parseToolResultPayload,
  extractToolFailure,
  DEFAULT_PILL_PREVIEW_COUNT,
  type ToolSource,
} from '../lib/toolActivity';

type JsonRecord = Record<string, unknown>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === 'object' && value !== null;
}

function sanitizeProtectedArtifactPaths(text: string): string {
  return text
    .replace(/\/generated-files\/[^\s"'`\\]+/g, '[protected generated file]')
    .replace(/\/generated-audio\/[^\s"'`\\]+/g, '[protected generated audio]')
    .replace(/\/generated-images\/[^\s"'`\\]+/g, '[protected generated image]')
    .replace(/\/api\/images\/[^\s"'`\\]+/g, '[protected image]');
}

function getDownloadSafeStem(
  value: string | null | undefined,
  fallback: string,
): string {
  const stem = (value || fallback)
    .replace(/\.[a-z0-9]+$/i, '')
    .replace(/[^a-z0-9_-]+/gi, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
  return stem || fallback;
}

function getSpawnMode(call: ChatEvent): string | null {
  if (!isToolCallEvent(call) || call.name !== 'spawn_agent') {
    return null;
  }

  const callArgs = call.arguments;
  if (!isRecord(callArgs)) {
    return null;
  }

  const context = callArgs.context;
  if (!isRecord(context)) {
    return null;
  }

  const mode = context.mode;
  return typeof mode === 'string' ? mode : null;
}

type ToolResultEvent = ChatEvent & {
  type: 'tool_result';
  name: string;
  result: unknown;
};

function deriveImagePath(
  result: ToolResultEvent | null,
  _isSpawnVideoCall: boolean,
): string | null {
  if (!result) return null;
  try {
    const raw = result.result;
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    const imgPath = parsed?.data?.image_path ?? parsed?.image_path;
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    if (
      typeof imgPath === 'string' &&
      imgPath.startsWith('/generated-images/')
    ) {
      return `${apiUrl}${imgPath}`;
    }
  } catch {}
  return null;
}

export interface ToolExecution {
  call: ChatEvent;
  result?: ChatEvent;
}

interface ToolCallBlockProps {
  execution: ToolExecution;
}

export function ToolCallBlock({ execution }: ToolCallBlockProps) {
  const { call: rawCall, result: rawResult } = execution;
  const [isExpanded, setIsExpanded] = useState(false);
  const inspectorId = useId();
  const [isLightboxOpen, setIsLightboxOpen] = useState(false);
  const [imageBlobUrl, setImageBlobUrl] = useState<string | null>(null);
  const [imageBlobLoadError, setImageBlobLoadError] = useState(false);

  const result = rawResult && isToolResultEvent(rawResult) ? rawResult : null;

  const spawnMode = getSpawnMode(rawCall);
  const isVideoRequested = spawnMode === 'video';
  const isAudioRequested = spawnMode === 'audio';

  const imagePath = deriveImagePath(result, isVideoRequested);

  useEffect(() => {
    if (!imagePath) return;
    let revoked = false;
    const controller = new AbortController();

    async function loadBlob() {
      if (!imagePath) return;
      try {
        const authHeader = await ensureAuthHeader();
        if (revoked) return;
        const headers: HeadersInit = {};
        if (authHeader) headers['Authorization'] = authHeader;
        const res = await fetch(imagePath as string, {
          headers,
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`fetch ${res.status}`);
        const blob = await res.blob();
        if (revoked) return;
        const objectUrl = URL.createObjectURL(blob);
        setImageBlobUrl(objectUrl);
        setImageBlobLoadError(false);
      } catch {
        if (!revoked) setImageBlobLoadError(true);
      }
    }

    loadBlob();
    return () => {
      revoked = true;
      controller.abort();
      setImageBlobUrl((current) => {
        if (current) URL.revokeObjectURL(current);
        return null;
      });
    };
  }, [imagePath]);

  // Dismiss the open image lightbox with Escape so the global Stop shortcut
  // is not left inert. The lightbox carries `data-stop-shortcut-block`, so
  // without this Escape does nothing while the lightbox is open.
  useEffect(() => {
    if (!isLightboxOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsLightboxOpen(false);
        event.stopPropagation();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [isLightboxOpen]);

  if (!isToolCallEvent(rawCall)) {
    return null;
  }

  const call = rawCall;
  const isSpawnVideoCall = call.name === 'spawn_agent' && isVideoRequested;

  const resultText = (() => {
    if (!result) return '';
    if (typeof result.result === 'string') return result.result;
    try {
      return JSON.stringify(result.result, null, 2);
    } catch {
      return String(result.result);
    }
  })();

  // 1. Loading State (Call exists, Result missing)
  if (!result) {
    if (
      call.name === 'spawn_agent' &&
      spawnMode &&
      ['image', 'video', 'audio'].includes(spawnMode)
    ) {
      return (
        <div className="flex items-center gap-2 text-[var(--color-text-muted)] text-sm py-2 px-1 animate-pulse">
          <Loader2 className="w-4 h-4 animate-spin" />
          <span>
            {isVideoRequested
              ? 'Generating video...'
              : isAudioRequested
                ? 'Creating sound effect...'
                : 'Creating image...'}
          </span>
        </div>
      );
    }
  }

  // 2. Result State
  let errorMessage = extractToolFailure(parseToolResultPayload(result?.result));
  let isError = Boolean(errorMessage);
  let audioPath: string | null = null;
  let videoPath: string | null = null;
  let videoDuration: number | null = null;
  let refunded: boolean | null = null;
  let prompt: string | null = null;

  try {
    const raw = result?.result;
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw;
    if (parsed?.error || parsed?.success === false) {
      isError = true;
      if (typeof parsed?.error === 'string') {
        errorMessage = parsed.error;
      } else if (typeof parsed?.data?.error === 'string') {
        errorMessage = parsed.data.error;
      } else {
        errorMessage =
          'Tool call failed. Continuing with best available information.';
      }
    }

    const imgPath = parsed?.data?.image_path ?? parsed?.image_path;
    const audPath = parsed?.data?.audio_path ?? parsed?.audio_path;
    const vidPath = isSpawnVideoCall
      ? (parsed?.data?.video_path ?? parsed?.video_path)
      : undefined;
    const vidUrl = isSpawnVideoCall
      ? (parsed?.data?.video_url ??
        parsed?.video_url ??
        parsed?.data?.url ??
        parsed?.url)
      : undefined;
    const dur =
      parsed?.data?.duration_seconds ??
      parsed?.duration_seconds ??
      parsed?.data?.duration ??
      parsed?.duration;
    const refundFlag = parsed?.data?.refunded ?? parsed?.refunded;

    if (typeof dur === 'number' && Number.isFinite(dur)) {
      videoDuration = dur;
    }
    if (typeof refundFlag === 'boolean') {
      refunded = refundFlag;
    }

    prompt = parsed?.data?.prompt ?? parsed?.prompt;

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

    if (
      typeof imgPath === 'string' &&
      imgPath.startsWith('/generated-images/')
    ) {
      isError = false;
    }

    if (
      typeof audPath === 'string' &&
      audPath.startsWith('/generated-audio/')
    ) {
      audioPath = audPath;
      isError = false;
    }

    if (
      typeof vidPath === 'string' &&
      vidPath.startsWith('/generated-videos/')
    ) {
      videoPath = `${apiUrl}${vidPath}`;
      isError = false;
    }

    if (!videoPath && typeof vidUrl === 'string') {
      if (vidUrl.startsWith('/generated-videos/')) {
        videoPath = `${apiUrl}${vidUrl}`;
      } else if (
        vidUrl.startsWith('http://') ||
        vidUrl.startsWith('https://') ||
        vidUrl.startsWith('data:')
      ) {
        videoPath = vidUrl;
      }

      if (videoPath) {
        isError = false;
      }
    }
  } catch {
    const lowerResult = resultText.toLowerCase();
    isError =
      lowerResult.includes('error') && !lowerResult.includes('"error": null');
    if (isError) {
      errorMessage =
        'Tool call failed. Continuing with best available information.';
    }
  }

  // Image Result UI
  if (videoPath) {
    return (
      <div className="my-2 max-w-3xl">
        <div className="text-sm text-[var(--color-text-muted)] mb-2 font-medium flex items-center gap-2">
          <Check className="w-4 h-4 text-[var(--color-status-success)]" />
          <span>Video created</span>
          {videoDuration !== null && (
            <>
              <span className="text-[var(--color-border-secondary)]">•</span>
              <span>{videoDuration}s</span>
            </>
          )}
        </div>
        <VideoPlayer src={videoPath} duration={videoDuration ?? undefined} />
      </div>
    );
  }

  if (isVideoRequested && isError) {
    return (
      <div className="my-2 rounded-xl border border-[var(--color-status-warning)]/40 bg-[var(--color-status-warning-bg)]/30 p-3 max-w-2xl">
        <div className="text-sm font-medium text-[var(--color-status-warning)] mb-1">
          Video generation failed
        </div>
        <div className="text-sm text-[var(--color-text-secondary)]">
          {errorMessage ??
            'Video generation failed. Continuing with best available information.'}
        </div>
        {refunded !== null && (
          <div className="text-xs text-[var(--color-text-muted)] mt-2">
            {refunded ? 'Credits refunded.' : 'Refund not confirmed.'}
          </div>
        )}
      </div>
    );
  }

  // Image Result UI
  if (imagePath) {
    const studioHref = `/studio?image=${encodeURIComponent(imagePath)}${
      prompt ? `&prompt=${encodeURIComponent(prompt)}` : ''
    }`;
    const imageDownloadName = `${getDownloadSafeStem(imagePath.split('/').pop(), 'generated-image')}.png`;

    return (
      <div className="my-2">
        {prompt && (
          <div className="text-sm text-[var(--color-text-muted)] mb-2 font-medium flex items-center gap-2">
            <Check className="w-4 h-4 text-[var(--color-status-success)]" />
            <span>Image created</span>
            <span className="text-[var(--color-border-secondary)]">•</span>
            <span className="truncate max-w-md" title={prompt}>
              {prompt}
            </span>
          </div>
        )}

        <div className="relative group rounded-xl overflow-hidden border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] shadow-sm max-w-md transition-all hover:shadow-md">
          {imageBlobUrl && !imageBlobLoadError ? (
            <Image
              src={imageBlobUrl}
              alt={prompt || 'Generated image'}
              width={768}
              height={512}
              unoptimized
              className="h-auto max-h-96 w-full cursor-pointer object-cover transition-opacity hover:opacity-95"
              onClick={() => setIsLightboxOpen(true)}
            />
          ) : imageBlobLoadError ? (
            <div className="w-full h-48 flex items-center justify-center bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)] text-sm">
              Failed to load image
            </div>
          ) : (
            <div className="w-full h-48 flex items-center justify-center bg-[var(--color-bg-secondary)] animate-pulse">
              <Loader2 className="w-6 h-6 text-[var(--color-text-muted)] animate-spin" />
            </div>
          )}

          <div className="absolute top-2 right-2 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
            <a
              href={studioHref}
              className="p-1.5 bg-media/50 hover:bg-media/70 text-on-media rounded-md backdrop-blur-sm transition-colors"
              title="Open in Studio"
              onClick={(e) => e.stopPropagation()}
            >
              <Palette className="w-4 h-4" />
            </a>
            <button
              onClick={(e) => {
                e.stopPropagation();
                setIsLightboxOpen(true);
              }}
              className="p-1.5 bg-media/50 hover:bg-media/70 text-on-media rounded-md backdrop-blur-sm transition-colors"
              title="Expand"
            >
              <Maximize2 className="w-4 h-4" />
            </button>
            {imageBlobUrl && !imageBlobLoadError ? (
              <a
                href={imageBlobUrl}
                download={imageDownloadName}
                className="p-1.5 bg-media/50 hover:bg-media/70 text-on-media rounded-md backdrop-blur-sm transition-colors"
                title="Download"
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-4 h-4" />
              </a>
            ) : null}
          </div>
        </div>

        {isLightboxOpen && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Generated image preview"
            data-stop-shortcut-block="true"
            className="fixed inset-0 z-lightbox flex items-center justify-center bg-media/95 backdrop-blur-sm p-4 animate-in fade-in duration-200"
            onClick={() => setIsLightboxOpen(false)}
          >
            <button
              onClick={() => setIsLightboxOpen(false)}
              className="absolute top-4 right-4 p-2 text-on-media/70 hover:text-on-media bg-on-media/10 hover:bg-on-media/20 rounded-full transition-colors"
            >
              <X className="w-6 h-6" />
            </button>

            {imageBlobUrl && !imageBlobLoadError ? (
              <Image
                src={imageBlobUrl}
                alt={prompt || 'Full resolution image'}
                width={1600}
                height={1200}
                unoptimized
                className="max-h-full max-w-full rounded-lg object-contain shadow-2xl"
                onClick={(e) => e.stopPropagation()}
              />
            ) : imageBlobLoadError ? (
              <div className="text-on-media/70 text-sm">
                Failed to load image
              </div>
            ) : (
              <Loader2 className="w-8 h-8 text-on-media/50 animate-spin" />
            )}

            {imageBlobUrl && !imageBlobLoadError && (
              <div
                className="absolute bottom-6 left-1/2 -translate-x-1/2 flex gap-4"
                onClick={(e) => e.stopPropagation()}
              >
                <a
                  href={imageBlobUrl}
                  download={imageDownloadName}
                  className="flex items-center gap-2 px-4 py-2 bg-[var(--color-bg-inverse)] text-[var(--color-text-inverse)] rounded-full font-medium hover:bg-[var(--color-bg-hover)] transition-colors shadow-lg"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Download className="w-4 h-4" />
                  Download
                </a>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  // Audio Result UI
  if (audioPath) {
    return <AudioPlayerBlock audioPath={audioPath} prompt={prompt} />;
  }

  // Standard Tool Result UI
  return (
    <div
      className={`border rounded-lg my-2 overflow-hidden ${
        isError
          ? 'bg-[var(--color-status-warning-bg)]/35 border-[var(--color-status-warning)]/40'
          : 'bg-[var(--color-bg-tertiary)] border-[var(--color-border-primary)]'
      }`}
    >
      <button
        type="button"
        aria-expanded={isExpanded}
        aria-controls={inspectorId}
        onClick={() => setIsExpanded(!isExpanded)}
        className={`w-full min-h-touch px-4 py-2 flex items-center gap-2 justify-between text-left transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)] ${
          isError
            ? 'hover:bg-[var(--color-status-warning-bg)]/60'
            : 'hover:bg-[var(--color-bg-hover)]'
        }`}
      >
        <div className="flex min-w-0 items-center gap-2">
          {!result ? (
            <Loader2
              aria-hidden
              className="w-4 h-4 shrink-0 animate-spin motion-reduce:animate-none"
            />
          ) : (
            <span
              aria-hidden
              className={`w-2 h-2 rounded-full ${isError ? 'bg-[var(--color-status-warning)]' : 'bg-[var(--color-status-success)]'}`}
            ></span>
          )}
          <div className="flex min-w-0 flex-col">
            <span
              className={`text-sm font-medium break-all ${isError ? 'text-[var(--color-status-warning)]' : 'text-[var(--color-text-secondary)]'}`}
            >
              {result ? call.name : `Running ${call.name}...`}
            </span>
          </div>
        </div>
        <ChevronRight
          aria-hidden
          className={`w-4 h-4 shrink-0 transition-transform motion-reduce:transition-none ${isExpanded ? 'rotate-90' : ''} ${
            isError
              ? 'text-[var(--color-status-warning)]'
              : 'text-[var(--color-text-muted)]'
          }`}
        />
      </button>
      <div
        id={inspectorId}
        hidden={!isExpanded}
        className="min-w-0 px-4 pb-3 space-y-2"
      >
        {isExpanded && (
          <>
            {isError && (
              <div className="text-xs text-[var(--color-status-warning)] bg-[var(--color-status-warning-bg)]/45 border border-[var(--color-status-warning)]/35 rounded p-2">
                {errorMessage ??
                  'Tool call failed. Continuing with best available information.'}
              </div>
            )}
            <div className="text-xs text-[var(--color-text-muted)] font-medium">
              Input:
            </div>
            <pre className="text-xs text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)] rounded p-2 overflow-x-auto">
              {sanitizeProtectedArtifactPaths(
                JSON.stringify(call.arguments, null, 2),
              )}
            </pre>
            <div className="text-xs text-[var(--color-text-muted)] font-medium">
              Output:
            </div>
            <pre
              className={`text-xs rounded p-2 overflow-x-auto overflow-y-auto max-h-80 whitespace-pre-wrap break-words ${
                isError
                  ? 'text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)] border border-[var(--color-status-warning)]/35'
                  : 'text-[var(--color-text-secondary)] bg-[var(--color-bg-secondary)] border border-[var(--color-border-primary)]'
              }`}
            >
              {result
                ? sanitizeProtectedArtifactPaths(resultText)
                : 'Awaiting result…'}
            </pre>
          </>
        )}
      </div>
    </div>
  );
}

interface ToolCallLogProps {
  events: ChatEvent[];
}

/**
 * Grouped per-response tool activity. All executions (including spawn
 * artifacts while expanded) collapse into one summary row by default.
 * Expanding reveals every action in call order, with media behavior intact.
 */
export function ToolCallLog({ events }: ToolCallLogProps) {
  const [isGroupExpanded, setIsGroupExpanded] = useState(false);
  const groupPanelId = useId();
  const executions = pairToolExecutions(events);

  if (executions.length === 0) return null;

  const summary = buildToolActivitySummary(executions);
  const sources = buildMessageCitationSources(events);
  const labels = [...summary.segments];
  if (summary.runningCount) labels.push(`Working… (${summary.runningCount})`);
  if (summary.errorCount)
    labels.push(
      `${summary.errorCount} issue${summary.errorCount === 1 ? '' : 's'}`,
    );

  return (
    <div className="min-w-0 max-w-full">
      <button
        type="button"
        aria-expanded={isGroupExpanded}
        aria-controls={groupPanelId}
        aria-label={`Tool activity: ${labels.join(' · ') || 'Tools used'}. ${isGroupExpanded ? 'Hide' : 'Show'} activity`}
        onClick={() => setIsGroupExpanded((previous) => !previous)}
        className="flex min-h-touch max-w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text-secondary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)]"
      >
        {summary.runningCount > 0 ? (
          <Loader2
            aria-hidden
            className="w-4 h-4 shrink-0 animate-spin motion-reduce:animate-none"
          />
        ) : (
          <span
            aria-hidden
            className={`shrink-0 w-2 h-2 rounded-full ${summary.errorCount ? 'bg-[var(--color-status-warning)]' : 'bg-[var(--color-status-success)]'}`}
          />
        )}
        <span className="min-w-0 break-words">
          {labels.join(' · ') || 'Tools used'}
        </span>
        <ChevronDown
          aria-hidden
          className={`w-4 h-4 shrink-0 ${isGroupExpanded ? 'rotate-180' : ''}`}
        />
      </button>
      <ToolSourcePills sources={sources} />
      <div id={groupPanelId} hidden={!isGroupExpanded}>
        {isGroupExpanded && (
          <ol className="min-w-0 space-y-2">
            {executions.map((execution, idx) => (
              <li key={executionId(execution, idx)} className="min-w-0">
                <ToolCallBlock execution={execution} />
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function executionId(execution: ToolExecution, idx: number): string {
  const callId = isToolCallEvent(execution.call)
    ? execution.call.tool_call_id
    : undefined;
  // A result arriving must not remount the inspector or reset user expansion.
  return `${callId ?? 'noid'}-${idx}`;
}

function ToolSourcePills({ sources }: { sources: ToolSource[] }) {
  const [showAll, setShowAll] = useState(false);
  const panelId = useId();
  if (!sources.length) return null;
  const link = (source: ToolSource) => (
    <a
      key={source.url}
      href={source.url}
      target="_blank"
      rel="noopener noreferrer"
      aria-label={
        source.title ? `${source.title} (${source.domain})` : source.url
      }
      title={source.title || source.url}
      className="inline-flex min-h-8 max-w-full items-center rounded-full border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-2 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)]"
    >
      <span className="max-w-56 truncate">{source.domain}</span>
    </a>
  );
  const rest = sources.slice(DEFAULT_PILL_PREVIEW_COUNT);
  return (
    <div
      aria-label="Sources"
      className="my-1 flex min-w-0 flex-wrap items-center gap-1.5"
    >
      {sources.slice(0, DEFAULT_PILL_PREVIEW_COUNT).map(link)}
      {rest.length > 0 && (
        <>
          <button
            type="button"
            aria-expanded={showAll}
            aria-controls={panelId}
            onClick={() => setShowAll((previous) => !previous)}
            className="min-h-8 rounded-full px-2 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-bg-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-primary)]"
          >
            {showAll ? 'Fewer sources' : `+${rest.length} more sources`}
          </button>
          <div id={panelId} hidden={!showAll} className="w-full">
            <div className="flex flex-wrap gap-1.5">{rest.map(link)}</div>
          </div>
        </>
      )}
    </div>
  );
}

// Audio Player Component
interface AudioPlayerBlockProps {
  audioPath: string;
  prompt: string | null;
}

function AudioPlayerBlock({ audioPath, prompt }: AudioPlayerBlockProps) {
  const [isPlaying, setIsPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const { displayUrl, loading, error } = useAuthenticatedImageUrl(audioPath);
  const audioDownloadName = `${getDownloadSafeStem(prompt || audioPath.split('/').pop(), 'sound-effect')}.mp3`;

  const handlePlayPause = () => {
    if (!audioRef.current) return;
    if (isPlaying) {
      audioRef.current.pause();
    } else {
      audioRef.current.play();
    }
    setIsPlaying(!isPlaying);
  };

  const handleTimeUpdate = () => {
    if (audioRef.current) {
      const progress =
        (audioRef.current.currentTime / audioRef.current.duration) * 100;
      setProgress(progress);
    }
  };

  const handleEnded = () => {
    setIsPlaying(false);
    setProgress(0);
  };

  return (
    <div className="my-2">
      {prompt && (
        <div className="text-sm text-[var(--color-text-muted)] mb-2 font-medium flex items-center gap-2">
          <Check className="w-4 h-4 text-[var(--color-status-success)]" />
          <span>Sound effect created</span>
          <span className="text-[var(--color-border-secondary)]">•</span>
          <span className="truncate max-w-md" title={prompt}>
            {prompt}
          </span>
        </div>
      )}

      <div className="flex items-center gap-3 p-3 bg-[var(--color-bg-tertiary)] rounded-xl border border-[var(--color-border-primary)] max-w-md">
        {displayUrl && !loading && !error ? (
          <audio
            ref={audioRef}
            src={displayUrl}
            onTimeUpdate={handleTimeUpdate}
            onEnded={handleEnded}
            preload="metadata"
          />
        ) : null}

        <button
          onClick={handlePlayPause}
          disabled={loading || error || !displayUrl}
          className="flex-shrink-0 w-10 h-10 flex items-center justify-center bg-[var(--color-accent-primary)] hover:bg-[var(--color-accent-hover)] text-[var(--color-text-on-accent)] rounded-full transition-colors"
          title={
            error
              ? 'Audio failed to load'
              : loading
                ? 'Loading audio'
                : isPlaying
                  ? 'Pause'
                  : 'Play'
          }
        >
          {isPlaying ? (
            <Pause className="w-5 h-5" />
          ) : (
            <Play className="w-5 h-5 ml-0.5" />
          )}
        </button>

        <div className="flex-1 flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Volume2 className="w-4 h-4 text-[var(--color-text-muted)]" />
            <div className="flex-1 h-1.5 bg-[var(--color-border-primary)] rounded-full overflow-hidden">
              <div
                className="h-full bg-[var(--color-accent-primary)] transition-all duration-100"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        </div>

        {displayUrl && !loading && !error ? (
          <a
            href={displayUrl}
            download={audioDownloadName}
            className="flex-shrink-0 p-2 text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] rounded-lg transition-colors"
            title="Download"
            target="_blank"
            rel="noopener noreferrer"
          >
            <Download className="w-4 h-4" />
          </a>
        ) : (
          <button
            type="button"
            disabled
            className="flex-shrink-0 p-2 text-[var(--color-text-muted)]/50 rounded-lg cursor-not-allowed"
            title={error ? 'Audio failed to load' : 'Audio download loading'}
          >
            <Download className="w-4 h-4" />
          </button>
        )}
      </div>
    </div>
  );
}
