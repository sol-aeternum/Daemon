"use client";

import { useMemo } from "react";
import { useStudio } from "../StudioProvider";

const RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4", "21:9"];

export function AspectRatioPicker() {
  const { aspectRatio, setAspectRatio, availableModels, selectedModels } = useStudio();

  const selectedModelSet = useMemo(
    () => new Set(selectedModels),
    [selectedModels],
  );

  const selectedModelObjects = useMemo(
    () => availableModels.filter((model) => selectedModelSet.has(model.id)),
    [availableModels, selectedModelSet],
  );

  return (
    <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Aspect Ratio</h2>
      <div className="grid grid-cols-3 gap-2">
        {RATIOS.map((ratio) => {
          const unsupportedBy = selectedModelObjects.find(
            (model) => model.supports_aspect_ratio && !model.supported_aspect_ratios.includes(ratio),
          );
          const disabled = Boolean(unsupportedBy);
          const isSelected = aspectRatio === ratio;

          return (
            <button
              key={ratio}
              type="button"
              className={`rounded-lg border px-2 py-2 text-xs transition-colors ${
                isSelected
                  ? "border-[var(--color-accent)] bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]"
                  : "border-[var(--color-border-primary)] text-[var(--color-text-secondary)]"
              } ${disabled ? "cursor-not-allowed opacity-50" : "hover:bg-[var(--color-bg-hover)]"}`}
              disabled={disabled}
              title={unsupportedBy ? `${unsupportedBy.name} does not support ${ratio}` : ratio}
              onClick={() => setAspectRatio(ratio)}
            >
              {ratio}
            </button>
          );
        })}
      </div>
    </section>
  );
}
