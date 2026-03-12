"use client";

import { useMemo } from "react";
import { useStudio } from "../StudioProvider";

const OPTIONS = [
  { value: "1K", label: "Standard" },
  { value: "2K", label: "High" },
  { value: "4K", label: "Ultra" },
];

export function ResolutionPicker() {
  const { resolution, setResolution, selectedModels, availableModels } = useStudio();

  const selected = useMemo(
    () => availableModels.filter((model) => selectedModels.includes(model.id)),
    [availableModels, selectedModels],
  );

  return (
    <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Resolution</h2>
      <div className="grid grid-cols-3 gap-2">
        {OPTIONS.map((option) => {
          const unsupported = selected.find(
            (model) => model.supports_resolution && !model.supported_resolutions.includes(option.value),
          );
          const disabled = Boolean(unsupported);
          const active = resolution === option.value;

          return (
            <button
              key={option.value}
              type="button"
              className={`rounded-lg border px-2 py-2 text-xs transition-colors ${
                active
                  ? "border-[var(--color-accent)] bg-[var(--color-bg-hover)] text-[var(--color-text-primary)]"
                  : "border-[var(--color-border-primary)] text-[var(--color-text-secondary)]"
              } ${disabled ? "cursor-not-allowed opacity-50" : "hover:bg-[var(--color-bg-hover)]"}`}
              disabled={disabled}
              title={unsupported ? `${unsupported.name} does not support ${option.value}` : option.label}
              onClick={() => setResolution(option.value)}
            >
              <span className="block font-medium">{option.label}</span>
              <span className="block text-[10px]">{option.value}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
