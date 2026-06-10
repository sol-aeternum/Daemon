'use client';

import { ExternalLink, X } from 'lucide-react';
import Image from 'next/image';
import type { StudioGeneration } from '../types';
import {
  useAuthenticatedImageUrl,
  isProtectedPath,
} from '@/hooks/useAuthenticatedImageUrl';
import { ensureAuthHeader } from '@/lib/auth';

interface ImageLightboxProps {
  generation: StudioGeneration;
  onClose: () => void;
  onUseAsReference: (generation: StudioGeneration) => void;
}

export function ImageLightbox({
  generation,
  onClose,
  onUseAsReference,
}: ImageLightboxProps) {
  const { displayUrl, loading, error } = useAuthenticatedImageUrl(
    generation.imageUrl ?? '',
  );

  if (!generation.imageUrl) {
    return null;
  }

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
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-5xl overflow-hidden rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border-primary)] px-4 py-3">
          <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">
            {generation.modelName || generation.modelId}
          </h3>
          <button
            type="button"
            className="text-[var(--color-text-secondary)]"
            onClick={onClose}
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="grid gap-4 p-4 md:grid-cols-[1fr_280px]">
          <div className="relative rounded-xl border border-[var(--color-border-primary)] bg-black/20 p-2">
            {loading ? (
              <div className="flex items-center justify-center h-64 text-[var(--color-text-muted)]">
                Loading...
              </div>
            ) : error ? (
              <div className="flex items-center justify-center h-64 text-[var(--color-text-muted)] text-sm">
                Failed to load image
              </div>
            ) : displayUrl ? (
              <Image
                src={displayUrl}
                alt={generation.prompt}
                width={1600}
                height={1000}
                unoptimized
                className="max-h-[70vh] w-full rounded-md object-contain"
              />
            ) : (
              <div className="flex items-center justify-center h-64 text-[var(--color-text-muted)] text-sm">
                Image unavailable
              </div>
            )}
          </div>

          <div className="space-y-3 text-xs text-[var(--color-text-secondary)]">
            <div>
              <p className="mb-1 text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
                Prompt
              </p>
              <p className="text-[var(--color-text-primary)]">
                {generation.prompt}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Stat label="Aspect" value={generation.aspectRatio} />
              <Stat label="Resolution" value={generation.resolution} />
              <Stat
                label="Time"
                value={
                  generation.generationTimeMs
                    ? `${generation.generationTimeMs} ms`
                    : '-'
                }
              />
              <Stat
                label="Cost"
                value={
                  typeof generation.costEstimate === 'number'
                    ? `$${generation.costEstimate.toFixed(4)}`
                    : '-'
                }
              />
            </div>
            <div className="space-y-2">
              <button
                type="button"
                onClick={handleDownload}
                className="block w-full rounded-md border border-[var(--color-border-primary)] px-3 py-2 text-center text-[var(--color-text-primary)]"
              >
                Download PNG
              </button>
              <button
                type="button"
                className="w-full rounded-md border border-[var(--color-border-primary)] px-3 py-2 text-[var(--color-text-primary)]"
                onClick={() => onUseAsReference(generation)}
              >
                Use as reference
              </button>
              <button
                type="button"
                className="w-full rounded-md border border-[var(--color-border-primary)] px-3 py-2 text-[var(--color-text-primary)]"
                onClick={() => navigator.clipboard.writeText(generation.prompt)}
              >
                Copy prompt
              </button>
              <button
                type="button"
                onClick={handleDownload}
                className="inline-flex w-full items-center justify-center gap-1 rounded-md border border-[var(--color-border-primary)] px-3 py-2 text-[var(--color-text-primary)]"
              >
                Open image <ExternalLink className="h-3 w-3" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-[var(--color-border-primary)] p-2">
      <p className="text-[10px] uppercase tracking-wide text-[var(--color-text-muted)]">
        {label}
      </p>
      <p className="mt-1 text-[var(--color-text-primary)]">{value}</p>
    </div>
  );
}
