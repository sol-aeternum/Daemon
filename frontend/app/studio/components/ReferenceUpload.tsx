'use client';

import { useRef, useState } from 'react';
import Image from 'next/image';
import { Upload } from 'lucide-react';
import { useStudio } from '../StudioProvider';
import { ensureAuthHeader } from '@/lib/auth';
import { useAuthenticatedImageUrl } from '@/hooks/useAuthenticatedImageUrl';

export function ReferenceUpload() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const { referenceImage, setReferenceImage, clearReference } = useStudio();
  const [isUploading, setIsUploading] = useState(false);
  const [urlValue, setUrlValue] = useState('');
  const [error, setError] = useState<string | null>(null);

  const uploadFile = async (file: File) => {
    setIsUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append('file', file);

      const authHeader = await ensureAuthHeader();
      const headers = new Headers();
      if (authHeader) headers.set('Authorization', authHeader);

      const response = await fetch('/api/images/upload-reference', {
        method: 'POST',
        headers,
        body: form,
      });
      if (!response.ok) {
        throw new Error(`Upload failed (${response.status})`);
      }

      const payload = (await response.json()) as {
        reference_id: string;
        image_url: string;
      };
      setReferenceImage({ id: payload.reference_id, url: payload.image_url });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to upload image');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
        References
      </h2>

      <div className="space-y-3">
        <button
          type="button"
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-border-primary)] px-3 py-4 text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-60"
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading}
        >
          <Upload className="h-4 w-4" />
          {isUploading ? 'Uploading...' : 'Upload reference image'}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          accept="image/png,image/jpeg,image/webp,image/gif"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) {
              void uploadFile(file);
            }
            event.currentTarget.value = '';
          }}
        />

        <div className="space-y-2">
          <label className="text-xs font-medium text-[var(--color-text-muted)]">
            Or paste image URL
          </label>
          <div className="flex gap-2">
            <input
              type="url"
              value={urlValue}
              onChange={(event) => setUrlValue(event.target.value)}
              placeholder="https://..."
              className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] px-2 py-2 text-xs text-[var(--color-text-primary)]"
            />
            <button
              type="button"
              className="rounded-md border border-[var(--color-border-primary)] px-3 py-2 text-xs text-[var(--color-text-primary)]"
              disabled={!urlValue.trim()}
              onClick={() => {
                const normalized = urlValue.trim();
                setReferenceImage({ id: `url:${normalized}`, url: normalized });
                setUrlValue('');
              }}
            >
              Use
            </button>
          </div>
        </div>

        {referenceImage && (
          <div className="space-y-2 rounded-lg border border-[var(--color-border-primary)] p-2">
            <ReferenceImagePreview url={referenceImage.url} />
            <button
              type="button"
              className="w-full rounded-md border border-[var(--color-border-primary)] px-2 py-1 text-xs text-[var(--color-text-secondary)]"
              onClick={clearReference}
            >
              Clear reference
            </button>
          </div>
        )}

        {error && <p className="text-xs text-red-400">{error}</p>}
      </div>
    </section>
  );
}

function ReferenceImagePreview({ url }: { url: string }) {
  const { displayUrl, loading, error } = useAuthenticatedImageUrl(url);

  if (loading) {
    return (
      <div className="h-24 w-full rounded-md bg-[var(--color-bg-tertiary)] animate-pulse" />
    );
  }
  if (error || !displayUrl) {
    return (
      <div className="h-24 w-full rounded-md bg-[var(--color-bg-tertiary)] flex items-center justify-center text-[var(--color-text-muted)] text-xs">
        Failed to load
      </div>
    );
  }
  return (
    <div className="relative h-24 w-full">
      <Image
        src={displayUrl}
        alt="Reference"
        fill
        unoptimized
        sizes="320px"
        className="rounded-md object-cover"
      />
    </div>
  );
}
