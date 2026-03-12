"use client";

import { Download, Image as ImageIcon, Loader2 } from "lucide-react";
import type { StudioGeneration } from "../types";

interface ImageCardProps {
  generation: StudioGeneration;
  onOpen: (generation: StudioGeneration) => void;
  onUseAsReference: (generation: StudioGeneration) => void;
}

export function ImageCard({ generation, onOpen, onUseAsReference }: ImageCardProps) {
  const isLoading = generation.status === "queued" || generation.status === "generating";
  const isError = generation.status === "error";

  return (
    <article className="overflow-hidden rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)]">
      <div className="aspect-square bg-[var(--color-bg-tertiary)]">
        {generation.imageUrl ? (
          <img
            src={generation.imageUrl}
            alt={generation.prompt}
            className="h-full w-full object-cover"
            onClick={() => onOpen(generation)}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-[var(--color-text-muted)]">
            {isLoading ? <Loader2 className="h-6 w-6 animate-spin" /> : <ImageIcon className="h-6 w-6" />}
          </div>
        )}
      </div>

      <div className="space-y-2 p-3 text-xs">
        <p className="truncate text-sm font-medium text-[var(--color-text-primary)]">
          {generation.modelName || generation.modelId}
        </p>
        <div className="flex items-center justify-between text-[var(--color-text-muted)]">
          <span>{generation.status}</span>
          <span>{generation.generationTimeMs ? `${generation.generationTimeMs}ms` : "-"}</span>
        </div>
        <div className="text-[var(--color-text-muted)]">
          Cost: {typeof generation.costEstimate === "number" ? `$${generation.costEstimate.toFixed(4)}` : "-"}
        </div>

        {isError && <p className="text-red-400">{generation.error || "Generation failed"}</p>}

        {generation.imageUrl && (
          <div className="flex gap-2">
            <button
              type="button"
              className="flex-1 rounded-md border border-[var(--color-border-primary)] px-2 py-1 text-[var(--color-text-primary)]"
              onClick={() => onUseAsReference(generation)}
            >
              Use reference
            </button>
            <a
              href={generation.imageUrl}
              download={`${generation.modelId}-${generation.id}.png`}
              className="inline-flex items-center justify-center rounded-md border border-[var(--color-border-primary)] px-2 py-1 text-[var(--color-text-primary)]"
            >
              <Download className="h-3 w-3" />
            </a>
          </div>
        )}
      </div>
    </article>
  );
}
