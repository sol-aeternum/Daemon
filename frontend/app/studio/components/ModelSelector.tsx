'use client';

import { useEffect, useMemo, useState } from 'react';
import { useStudio } from '../StudioProvider';
import type { StudioModel } from '../types';
import { ensureAuthHeader } from '@/lib/auth';

export function ModelSelector() {
  const {
    availableModels,
    setAvailableModels,
    selectedModels,
    addModel,
    removeModel,
  } = useStudio();
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setIsLoading(true);
      setError(null);
      try {
        // Availability is decided by the authenticated session on the backend;
        // the client never asserts a plan, tier, or capability hint.
        const authHeader = await ensureAuthHeader();
        const headers = new Headers();
        if (authHeader) headers.set('Authorization', authHeader);
        const response = await fetch('/api/images/models', {
          headers,
          cache: 'no-store',
        });
        if (!response.ok) {
          throw new Error(`Failed to load models (${response.status})`);
        }
        const json = (await response.json()) as { models?: StudioModel[] };
        if (mounted) {
          setAvailableModels(Array.isArray(json.models) ? json.models : []);
        }
      } catch (err) {
        if (mounted) {
          setError(
            err instanceof Error ? err.message : 'Failed to load models',
          );
        }
      } finally {
        if (mounted) {
          setIsLoading(false);
        }
      }
    };

    void load();

    return () => {
      mounted = false;
    };
  }, [setAvailableModels]);

  const grouped = useMemo(() => {
    const groups = availableModels.reduce<Record<string, StudioModel[]>>(
      (acc, model) => {
        const key = model.provider;
        if (!acc[key]) {
          acc[key] = [];
        }
        acc[key].push(model);
        return acc;
      },
      {},
    );

    const getCostScore = (model: StudioModel): number => {
      if (
        model.input_cost_per_million != null &&
        model.output_cost_per_million != null
      ) {
        return model.input_cost_per_million + model.output_cost_per_million;
      }
      if (model.flat_image_price_usd != null) {
        return model.flat_image_price_usd;
      }
      if (model.first_megapixel_price_usd != null) {
        const additional = model.additional_megapixel_price_usd ?? 0;
        return model.first_megapixel_price_usd + additional * 2;
      }
      if (model.resolution_prices_usd) {
        const prices = Object.values(model.resolution_prices_usd);
        return prices.length > 0 ? Math.max(...prices) : 0;
      }
      return 0;
    };

    const providerAvgCost: Record<string, number> = {};
    for (const [provider, models] of Object.entries(groups)) {
      const totalCost = models.reduce((sum, m) => sum + getCostScore(m), 0);
      providerAvgCost[provider] = totalCost / models.length;
    }

    const sortedProviders = Object.keys(groups).sort(
      (a, b) => (providerAvgCost[a] ?? 0) - (providerAvgCost[b] ?? 0),
    );

    const sortedGroups: Record<string, StudioModel[]> = {};
    for (const provider of sortedProviders) {
      sortedGroups[provider] = groups[provider].sort(
        (a, b) => getCostScore(a) - getCostScore(b),
      );
    }

    return sortedGroups;
  }, [availableModels]);

  return (
    <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">
          Models
        </h2>
        <span className="rounded-md bg-[var(--color-bg-hover)] px-2 py-1 text-xs text-[var(--color-text-secondary)]">
          {selectedModels.length} active
        </span>
      </div>

      {isLoading && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Loading model catalog...
        </p>
      )}
      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="max-h-72 space-y-3 overflow-y-auto pr-1">
        {Object.entries(grouped).map(([provider, models]) => (
          <div key={provider} className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
              {provider}
            </p>
            {models.map((model) => {
              const isLocked = model.is_locked ?? false;
              const checked = selectedModels.includes(model.id);
              const disabled =
                isLocked || (!checked && selectedModels.length >= 4);

              return (
                <label
                  key={model.id}
                  className={`flex cursor-pointer items-start gap-2 rounded-lg border px-2 py-2 transition-colors ${
                    checked
                      ? 'border-[var(--color-accent)] bg-[var(--color-bg-hover)]'
                      : 'border-[var(--color-border-primary)]'
                  } ${disabled ? 'cursor-not-allowed opacity-60' : 'hover:bg-[var(--color-bg-hover)]'}`}
                  title={
                    isLocked
                      ? 'Not available on your account'
                      : model.pricing_info
                  }
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
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                      {model.name}
                    </p>
                    <p className="text-xs text-[var(--color-text-muted)]">
                      {model.pricing_info}
                    </p>
                    {isLocked && (
                      <p className="mt-1 text-xs text-amber-400">
                        🔒 Not available on your account
                      </p>
                    )}
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
