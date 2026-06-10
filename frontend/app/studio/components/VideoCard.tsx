'use client';

import { AlertCircle, Download, Loader2, Video } from 'lucide-react';
import { VideoPlayer } from '@/components/VideoPlayer';
import type { StudioGeneration } from '../types';

interface VideoCardProps {
  generation: StudioGeneration;
}

export function VideoCard({ generation }: VideoCardProps) {
  const isLoading =
    generation.status === 'queued' || generation.status === 'generating';
  const isError = generation.status === 'error';
  const durationLabel =
    typeof generation.durationSeconds === 'number'
      ? `${generation.durationSeconds}s`
      : '-';

  return (
    <article className="overflow-hidden rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)]">
      <div className="aspect-video bg-[var(--color-bg-tertiary)]">
        {generation.videoUrl ? (
          <VideoPlayer
            src={generation.videoUrl}
            duration={generation.durationSeconds}
            className="h-full w-full rounded-none border-0"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
            {isLoading ? (
              <Loader2 className="h-6 w-6 animate-spin" />
            ) : isError ? (
              <AlertCircle className="h-6 w-6" />
            ) : (
              <Video className="h-6 w-6" />
            )}
          </div>
        )}
      </div>

      <div className="space-y-2 p-3 text-xs">
        <p className="truncate text-sm font-medium text-[var(--color-text-primary)]">
          {generation.modelName || generation.modelId}
        </p>

        <div className="flex items-center justify-between text-[var(--color-text-muted)]">
          <span>{generation.status}</span>
          <span>{durationLabel}</span>
        </div>

        <div className="text-[var(--color-text-muted)]">
          Cost:{' '}
          {typeof generation.costEstimate === 'number'
            ? `${generation.costEstimate} credits`
            : '-'}
        </div>

        {generation.refunded && (
          <p className="text-[var(--color-status-success)]">Credits refunded</p>
        )}

        {isError && (
          <p className="text-[var(--color-status-warning)]">
            {generation.error || 'Video generation failed'}
          </p>
        )}

        {generation.videoUrl && (
          <a
            href={generation.videoUrl}
            download={`${generation.modelId}-${generation.id}.mp4`}
            className="inline-flex items-center justify-center rounded-md border border-[var(--color-border-primary)] px-2 py-1 text-[var(--color-text-primary)]"
          >
            <Download className="h-3 w-3" />
          </a>
        )}
      </div>
    </article>
  );
}
