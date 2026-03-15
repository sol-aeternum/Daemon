"use client";

import { useMemo, useState } from "react";
import { ImageCard } from "./ImageCard";
import { ImageLightbox } from "./ImageLightbox";
import { VideoCard } from "./VideoCard";
import { useStudio } from "../StudioProvider";
import type { StudioGeneration } from "../types";

export function ImageGallery() {
  const { generations, setReferenceImage } = useStudio();
  const [lightboxItem, setLightboxItem] = useState<StudioGeneration | null>(null);

  const sorted = useMemo(
    () => [...generations].sort((a, b) => b.createdAt.localeCompare(a.createdAt)),
    [generations],
  );

  const handleUseAsReference = (generation: StudioGeneration) => {
    if (!generation.imageUrl || !generation.imageId) {
      return;
    }
    setReferenceImage({ id: generation.imageId, url: generation.imageUrl });
  };

  if (sorted.length === 0) {
    return (
      <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-8 text-center text-sm text-[var(--color-text-muted)]">
        No generations yet. Add a prompt and select at least one model.
      </section>
    );
  }

  return (
    <>
      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {sorted.map((generation) => (
          generation.mediaType === "video" || generation.videoUrl ? (
            <VideoCard key={generation.id} generation={generation} />
          ) : (
            <ImageCard
              key={generation.id}
              generation={generation}
              onOpen={setLightboxItem}
              onUseAsReference={handleUseAsReference}
            />
          )
        ))}
      </section>

      {lightboxItem && (
        <ImageLightbox
          generation={lightboxItem}
          onClose={() => setLightboxItem(null)}
          onUseAsReference={handleUseAsReference}
        />
      )}
    </>
  );
}
