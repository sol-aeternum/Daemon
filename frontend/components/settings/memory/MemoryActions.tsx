'use client';

import { useState } from 'react';
import { Download, Loader2, Plus } from 'lucide-react';
import {
  MAX_USER_MEMORY_LENGTH,
  USER_MEMORY_CATEGORIES,
  type MemoryExport,
  type UserMemoryCategory,
} from '@/hooks/useMemories';

type Outcome = { kind: 'success' | 'error'; message: string } | null;

const CATEGORY_LABELS: Record<UserMemoryCategory, string> = {
  fact: 'Fact',
  preference: 'Preference',
  project: 'Project',
  correction: 'Correction',
};

interface MemoryActionsProps {
  createMemory: (
    content: string,
    category: UserMemoryCategory,
  ) => Promise<{ ok: true; id: string } | { ok: false; error: string }>;
  exportMemories: () => Promise<MemoryExport>;
  /** Called after a memory is saved so lists and counts can refresh. */
  onSaved: () => void;
}

function downloadJson(data: MemoryExport, filename: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function MemoryActions({
  createMemory,
  exportMemories,
  onSaved,
}: MemoryActionsProps) {
  const [content, setContent] = useState('');
  const [category, setCategory] = useState<UserMemoryCategory>('fact');
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);

  const trimmedLength = content.trim().length;
  const tooLong = trimmedLength > MAX_USER_MEMORY_LENGTH;

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving || trimmedLength === 0 || tooLong) return;
    setSaving(true);
    setOutcome(null);
    const result = await createMemory(content, category);
    setSaving(false);
    if (result.ok) {
      setContent('');
      setOutcome({
        kind: 'success',
        message:
          'Saved to memory. If it repeated something already remembered, the two were merged.',
      });
      onSaved();
    } else {
      setOutcome({ kind: 'error', message: result.error });
    }
  };

  const handleExport = async () => {
    if (exporting) return;
    setExporting(true);
    setOutcome(null);
    try {
      const data = await exportMemories();
      const day = data.exported_at.slice(0, 10);
      downloadJson(data, `daemon-memories-${day}.json`);
      const count = data.memories.length;
      setOutcome({
        kind: 'success',
        message: `Exported ${count.toLocaleString()} active ${
          count === 1 ? 'memory' : 'memories'
        }.`,
      });
    } catch {
      setOutcome({
        kind: 'error',
        message: "Couldn't export memories. Please try again.",
      });
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="bg-bg-secondary rounded-lg border border-border-primary p-4 space-y-4">
      <form onSubmit={handleSave} className="space-y-3">
        <label
          htmlFor="memory-add-content"
          className="block text-sm font-medium text-text-primary"
        >
          Add a memory
        </label>
        <p className="text-xs text-text-muted">
          Tell Daemon something to remember, in your own words.
        </p>
        <textarea
          id="memory-add-content"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          rows={3}
          placeholder="e.g. I prefer metric units"
          aria-describedby="memory-add-count"
          aria-invalid={tooLong}
          className="w-full rounded-md border border-border-primary bg-bg-tertiary px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-border-focus/50"
        />
        <div className="flex flex-wrap items-center gap-3">
          <label htmlFor="memory-add-category" className="sr-only">
            Category
          </label>
          <select
            id="memory-add-category"
            value={category}
            onChange={(event) =>
              setCategory(event.target.value as UserMemoryCategory)
            }
            className="rounded-md border border-border-primary bg-bg-tertiary px-3 py-2 text-sm text-text-primary focus:outline-none focus:ring-2 focus:ring-border-focus/50"
          >
            {USER_MEMORY_CATEGORIES.map((value) => (
              <option key={value} value={value}>
                {CATEGORY_LABELS[value]}
              </option>
            ))}
          </select>
          <span
            id="memory-add-count"
            className={`text-xs ${tooLong ? 'text-status-error' : 'text-text-muted'}`}
          >
            {trimmedLength.toLocaleString()} /{' '}
            {MAX_USER_MEMORY_LENGTH.toLocaleString()}
          </span>
          <button
            type="submit"
            disabled={saving || trimmedLength === 0 || tooLong}
            className="ml-auto inline-flex items-center gap-2 rounded-md bg-accent-primary px-4 py-2 text-sm font-medium text-[var(--color-text-on-accent)] transition-colors hover:bg-accent-primary/90 disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-border-focus/50"
          >
            {saving ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            <span>{saving ? 'Saving…' : 'Save memory'}</span>
          </button>
        </div>
      </form>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-primary pt-4">
        <div>
          <p className="text-sm font-medium text-text-primary">
            Export memories
          </p>
          <p className="text-xs text-text-muted">
            Download your active memories as JSON: text, category and dates
            only.
          </p>
        </div>
        <button
          type="button"
          onClick={handleExport}
          disabled={exporting}
          className="inline-flex items-center gap-2 rounded-md border border-border-primary px-4 py-2 text-sm font-medium text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary disabled:cursor-not-allowed disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-border-focus/50"
        >
          {exporting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Download className="h-4 w-4" />
          )}
          <span>{exporting ? 'Exporting…' : 'Export JSON'}</span>
        </button>
      </div>

      {outcome && (
        <p
          role={outcome.kind === 'error' ? 'alert' : 'status'}
          data-testid="memory-action-outcome"
          className={`text-sm ${
            outcome.kind === 'error'
              ? 'text-status-error'
              : 'text-status-success'
          }`}
        >
          {outcome.message}
        </p>
      )}
    </div>
  );
}
