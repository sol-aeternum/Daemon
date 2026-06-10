'use client';

import { Download, Image as ImageIcon, Loader2 } from 'lucide-react';
import Image from 'next/image';
import type { StudioGeneration } from '../types';
import {
  useAuthenticatedImageUrl,
  isProtectedPath,
} from '@/hooks/useAuthenticatedImageUrl';
import { ensureAuthHeader } from '@/lib/auth';

interface ImageCardProps {
  generation: StudioGeneration;
  onOpen: (generation: StudioGeneration) => void;
  onUseAsReference: (generation: StudioGeneration) => void;
}

export function ImageCard({
  generation,
  onOpen,
  onUseAsReference,
}: ImageCardProps) {
  const isLoading =
    generation.status === 'queued' || generation.status === 'generating';
  const isError = generation.status === 'error';
  const {
    displayUrl,
    loading: imageLoading,
    error: imageError,
  } = useAuthenticatedImageUrl(generation.imageUrl);

  const handleDownload = async () => {
    if (!generation.imageUrl) return;
    let objectUrl: string | null = null;
    let cleanupAnchor: HTMLAnchorElement | null = null;
    try {
      const apiBaseUrl =
        process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const fullUrl = generation.imageUrl.startsWith('/')
        ? `${apiBaseUrl}${generation.imageUrl}`
        : generation.imageUrl;
      const isProtected = isProtectedPath(generation.imageUrl);
      const headers: HeadersInit = {};
      if (isProtected) {
        const authHeader = await ensureAuthHeader();
        if (authHeader) headers['Authorization'] = authHeader;
      }
      const response = await fetch(fullUrl, { headers });
      if (!response.ok) throw new Error(`Download failed: ${response.status}`);
      const blob = await response.blob();
      objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = `${generation.modelId}-${generation.id}.png`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      document.body.appendChild(link);
      cleanupAnchor = link;
      link.click();
    } finally {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      if (cleanupAnchor && cleanupAnchor.parentNode) {
        cleanupAnchor.parentNode.removeChild(cleanupAnchor);
      }
    }
  };

  return (
    <article className="overflow-hidden rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)]">
      <div className="relative aspect-square bg-[var(--color-bg-tertiary)]">
        {generation.imageUrl ? (
          imageLoading ? (
            <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
              <Loader2 className="h-6 w-6 animate-spin" />
            </div>
          ) : imageError ? (
            <div className="flex h-full items-center justify-center text-[var(--color-text-muted)] text-sm">
              Failed to load
            </div>
          ) : displayUrl ? (
            <Image
              src={displayUrl}
              alt={generation.prompt}
              fill
              unoptimized
              sizes="(min-width: 1024px) 25vw, (min-width: 640px) 50vw, 100vw"
              className="h-full w-full object-cover cursor-pointer"
              onClick={() => onOpen(generation)}
            />
          ) : null
        ) : (
          <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
            {isLoading ? (
              <Loader2 className="h-6 w-6 animate-spin" />
            ) : (
              <ImageIcon className="h-6 w-6" />
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
          <span>
            {generation.generationTimeMs
              ? `${generation.generationTimeMs}ms`
              : '-'}
          </span>
        </div>
        <div className="text-[var(--color-text-muted)]">
          Cost:{' '}
          {typeof generation.costEstimate === 'number'
            ? `$${generation.costEstimate.toFixed(4)}`
            : '-'}
        </div>

        {isError && (
          <p className="text-red-400">
            {generation.error || 'Generation failed'}
          </p>
        )}

        {generation.imageUrl && (
          <div className="flex gap-2">
            <button
              type="button"
              className="flex-1 rounded-md border border-[var(--color-border-primary)] px-2 py-1 text-[var(--color-text-primary)]"
              onClick={() => onUseAsReference(generation)}
            >
              Use reference
            </button>
            <button
              type="button"
              onClick={handleDownload}
              className="inline-flex items-center justify-center rounded-md border border-[var(--color-border-primary)] px-2 py-1 text-[var(--color-text-primary)]"
            >
              <Download className="h-3 w-3" />
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
