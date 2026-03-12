"use client";

import { useEffect, useMemo, useState } from "react";
import { useStudio } from "../StudioProvider";
import type { StudioModel } from "../types";

const USER_TIER = "starter" as const;
const TIER_ORDER: Record<StudioModel["tier_minimum"], number> = {
  free: 0,
  starter: 1,
  pro: 2,
  max: 3,
  byok: 4,
};

export function ModelSelector() {
  const { availableModels, setAvailableModels, selectedModels, addModel, removeModel } = useStudio();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const response = await fetch("/api/images/models?tier=max");
        if (!response.ok) {
          throw new Error(`Failed to load models (${response.status})`);
        }
        const json = (await response.json()) as { models?: StudioModel[] };
        if (mounted) {
          setAvailableModels(Array.isArray(json.models) ? json.models : []);
        }
      } catch (err) {
        if (mounted) {
          setError(err instanceof Error ? err.message : "Failed to load models");
        }
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    };

    if (availableModels.length === 0) {
      void load();
    }

    return () => {
      mounted = false;
    };
  }, [availableModels.length, setAvailableModels]);

  const grouped = useMemo(() => {
    return availableModels.reduce<Record<string, StudioModel[]>>((acc, model) => {
      const key = model.provider;
      if (!acc[key]) {
        acc[key] = [];
      }
      acc[key].push(model);
      return acc;
    }, {});
  }, [availableModels]);

  return (
    <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Models</h2>
        <span className="rounded-md bg-[var(--color-bg-hover)] px-2 py-1 text-xs text-[var(--color-text-secondary)]">
          {selectedModels.length} active
        </span>
      </div>

      {isLoading && <p className="text-xs text-[var(--color-text-muted)]">Loading model catalog...</p>}
      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="max-h-72 space-y-3 overflow-y-auto pr-1">
        {Object.entries(grouped).map(([provider, models]) => (
          <div key={provider} className="space-y-1">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">{provider}</p>
            {models.map((model) => {
              const isLocked = TIER_ORDER[model.tier_minimum] > TIER_ORDER[USER_TIER];
              const checked = selectedModels.includes(model.id);
              const disabled = isLocked || (!checked && selectedModels.length >= 4);

              return (
                <label
                  key={model.id}
                  className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2 py-2 transition-colors ${
                    checked
                      ? "border-[var(--color-accent)] bg-[var(--color-bg-hover)]"
                      : "border-[var(--color-border-primary)]"
                  } ${disabled ? "cursor-not-allowed opacity-60" : "hover:bg-[var(--color-bg-hover)]"}`}
                  title={isLocked ? `Requires ${model.tier_minimum} tier` : model.pricing_info}
                >
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={checked}
                    disabled={disabled}
                    onChange={(event) => {
                      if (event.target.checked) {
                        addModel(model.id);
                      } else {
                        removeModel(model.id);
                      }
                    }}
                  />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-[var(--color-text-primary)]">{model.name}</p>
                    <p className="text-xs text-[var(--color-text-muted)]">{model.pricing_info}</p>
                    {isLocked && <p className="text-[10px] text-amber-400">Upgrade to {model.tier_minimum}</p>}
                  </div>
                </label>
              );
            })}
          </div>
        ))}
      </div>
    </section>
  );
}
