'use client';

import { useEffect, useRef, useState } from 'react';
import { subscribeAuthGeneration } from '@/lib/auth';
import { Download, Loader2, Plus, Upload } from 'lucide-react';
import {
  MAX_USER_MEMORY_LENGTH,
  MemoryExportFormatError,
  MemoryRequestSupersededError,
  USER_MEMORY_CATEGORIES,
  type MemoryExport,
  type MemoryRequestOptions,
  type UserMemoryCategory,
  MAX_IMPORT_FILE_BYTES,
  parseMemoryImport,
  type ImportableMemory,
  type MemoryImportResult,
  type ParsedMemoryImport,
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
    options?: MemoryRequestOptions,
  ) => Promise<{ ok: true; id: string } | { ok: false; error: string }>;
  exportMemories: (options?: MemoryRequestOptions) => Promise<MemoryExport>;
  importMemories: (
    items: ImportableMemory[],
    options?: MemoryRequestOptions,
  ) => Promise<MemoryImportResult>;
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

function readFileText(file: File): Promise<string> {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

function plural(count: number, one: string, many: string): string {
  return `${count.toLocaleString()} ${count === 1 ? one : many}`;
}

function describeSkipped(parsed: ParsedMemoryImport): string {
  const parts: string[] = [];
  const { empty, tooLong, duplicate } = parsed.skipped;
  if (empty) parts.push(`${plural(empty, 'entry', 'entries')} without text`);
  if (tooLong)
    parts.push(
      `${plural(tooLong, 'entry', 'entries')} over ${MAX_USER_MEMORY_LENGTH.toLocaleString()} characters`,
    );
  if (duplicate)
    parts.push(`${plural(duplicate, 'duplicate', 'duplicates')} in the file`);
  return parts.length ? `Skipping ${parts.join(', ')}.` : '';
}

function describeImport(result: MemoryImportResult): string {
  const saved = `${plural(result.created, 'new memory', 'new memories')}, ${plural(
    result.merged,
    'merged with an existing one',
    'merged with existing ones',
  )}, ${plural(result.superseded, 'replaced an older version', 'replaced older versions')}`;
  if (result.error) {
    return `${result.error} Saved before stopping: ${saved} (${result.processed.toLocaleString()} of ${result.total.toLocaleString()} processed).`;
  }
  return `Imported ${plural(result.total, 'memory', 'memories')}: ${saved}.`;
}

export function MemoryActions({
  createMemory,
  exportMemories,
  importMemories,
  onSaved,
}: MemoryActionsProps) {
  const fileInput = useRef<HTMLInputElement>(null);
  const [pendingImport, setPendingImport] = useState<{
    name: string;
    parsed: ParsedMemoryImport;
  } | null>(null);
  const [importing, setImporting] = useState(false);
  const [content, setContent] = useState('');
  const [category, setCategory] = useState<UserMemoryCategory>('fact');
  const [saving, setSaving] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);

  // In-flight operations. Leaving the view or any sign-in change aborts them,
  // so a late response can never download, save or report for another scope.
  const operations = useRef(new Set<AbortController>());

  useEffect(() => {
    const active = operations.current;
    const abortAll = () => {
      for (const operation of active) operation.abort();
      active.clear();
    };
    const unsubscribe = subscribeAuthGeneration(() => {
      abortAll();
      setContent('');
      setCategory('fact');
      setOutcome(null);
      setSaving(false);
      setExporting(false);
      setPendingImport(null);
      setImporting(false);
    });
    return () => {
      unsubscribe();
      abortAll();
    };
  }, []);

  const beginOperation = () => {
    const operation = new AbortController();
    operations.current.add(operation);
    return operation;
  };

  const endOperation = (operation: AbortController) => {
    operations.current.delete(operation);
  };

  const trimmedLength = content.trim().length;
  const tooLong = trimmedLength > MAX_USER_MEMORY_LENGTH;

  const handleSave = async (event: React.FormEvent) => {
    event.preventDefault();
    if (saving || trimmedLength === 0 || tooLong) return;
    const operation = beginOperation();
    const submitted = content;
    setSaving(true);
    setOutcome(null);
    try {
      const result = await createMemory(submitted, category, {
        signal: operation.signal,
      });
      if (operation.signal.aborted) return;
      if (result.ok) {
        // Fields are locked while saving; still clear only the exact text sent.
        setContent((current) => (current === submitted ? '' : current));
        setOutcome({
          kind: 'success',
          message:
            'Saved to memory. If it repeated something already remembered, the two were merged.',
        });
        onSaved();
      } else {
        setOutcome({ kind: 'error', message: result.error });
      }
    } catch (error) {
      if (
        operation.signal.aborted ||
        error instanceof MemoryRequestSupersededError
      )
        return;
      setOutcome({
        kind: 'error',
        message: "Couldn't save the memory. Please try again.",
      });
    } finally {
      endOperation(operation);
      if (!operation.signal.aborted) setSaving(false);
    }
  };

  const handleFile = async (file: File | undefined) => {
    if (fileInput.current) fileInput.current.value = '';
    if (!file) return;
    setOutcome(null);
    setPendingImport(null);
    if (file.size > MAX_IMPORT_FILE_BYTES) {
      setOutcome({
        kind: 'error',
        message: `That file is larger than ${MAX_IMPORT_FILE_BYTES / (1024 * 1024)} MB.`,
      });
      return;
    }
    const operation = beginOperation();
    try {
      const parsed = parseMemoryImport(await readFileText(file));
      if (operation.signal.aborted) return;
      if (parsed.memories.length === 0) {
        setOutcome({
          kind: 'error',
          message: `Nothing to import. ${describeSkipped(parsed)}`.trim(),
        });
        return;
      }
      setPendingImport({ name: file.name, parsed });
    } catch (error) {
      if (operation.signal.aborted) return;
      setOutcome({
        kind: 'error',
        message:
          error instanceof Error ? error.message : "Couldn't read that file.",
      });
    } finally {
      endOperation(operation);
    }
  };

  const handleConfirmImport = async () => {
    if (!pendingImport || importing) return;
    const operation = beginOperation();
    const items = pendingImport.parsed.memories;
    setImporting(true);
    try {
      const result = await importMemories(items, { signal: operation.signal });
      if (operation.signal.aborted) return;
      setPendingImport(null);
      setOutcome({
        kind: result.error ? 'error' : 'success',
        message: describeImport(result),
      });
      if (result.created + result.merged + result.superseded > 0) onSaved();
    } catch (error) {
      if (
        operation.signal.aborted ||
        error instanceof MemoryRequestSupersededError
      )
        return;
      setPendingImport(null);
      setOutcome({ kind: 'error', message: "Couldn't finish the import." });
    } finally {
      endOperation(operation);
      if (!operation.signal.aborted) setImporting(false);
    }
  };

  const handleExport = async () => {
    if (exporting) return;
    const operation = beginOperation();
    setExporting(true);
    setOutcome(null);
    try {
      const data = await exportMemories({ signal: operation.signal });
      if (operation.signal.aborted) return;
      const day = data.exported_at.slice(0, 10);
      downloadJson(data, `daemon-memories-${day}.json`);
      const count = data.memories.length;
      setOutcome({
        kind: 'success',
        message: `Exported ${count.toLocaleString()} active ${
          count === 1 ? 'memory' : 'memories'
        }.`,
      });
    } catch (error) {
      if (
        operation.signal.aborted ||
        error instanceof MemoryRequestSupersededError
      )
        return;
      setOutcome({
        kind: 'error',
        message:
          error instanceof MemoryExportFormatError
            ? 'Daemon sent an unexpected export, so nothing was downloaded. Please try again.'
            : "Couldn't export memories. Please try again.",
      });
    } finally {
      endOperation(operation);
      if (!operation.signal.aborted) setExporting(false);
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
          onChange={(event) => {
            if (!saving) setContent(event.target.value);
          }}
          rows={3}
          readOnly={saving}
          aria-busy={saving}
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
            disabled={saving}
            onChange={(event) => {
              if (!saving)
                setCategory(event.target.value as UserMemoryCategory);
            }}
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

      <div className="space-y-3 border-t border-border-primary pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium text-text-primary">
              Import memories
            </p>
            <p className="text-xs text-text-muted">
              From a Daemon export or a JSON list of text and categories. You
              can review before anything is saved.
            </p>
          </div>
          <label
            className={`ml-auto inline-flex items-center gap-2 rounded-md border border-border-primary px-4 py-2 text-sm font-medium text-text-secondary transition-colors focus-within:ring-2 focus-within:ring-border-focus/50 ${
              importing
                ? 'cursor-not-allowed opacity-50'
                : 'cursor-pointer hover:bg-bg-tertiary hover:text-text-primary'
            }`}
          >
            <Upload className="h-4 w-4" />
            <span>Choose file</span>
            <input
              ref={fileInput}
              type="file"
              accept="application/json,.json"
              aria-label="Import memories from a JSON file"
              disabled={importing}
              className="sr-only"
              onChange={(event) => void handleFile(event.target.files?.[0])}
            />
          </label>
        </div>
        {pendingImport && (
          <div
            role="group"
            aria-label="Review import"
            className="rounded-md border border-border-primary bg-bg-tertiary p-3 text-sm"
          >
            <p className="text-text-primary">
              Ready to import{' '}
              {plural(
                pendingImport.parsed.memories.length,
                'memory',
                'memories',
              )}{' '}
              from <span className="font-medium">{pendingImport.name}</span>.
              Repeats of memories you already have will be merged.
            </p>
            {(describeSkipped(pendingImport.parsed) ||
              pendingImport.parsed.recategorized > 0) && (
              <p className="mt-1 text-xs text-text-muted">
                {describeSkipped(pendingImport.parsed)}
                {pendingImport.parsed.recategorized > 0 &&
                  ` ${plural(pendingImport.parsed.recategorized, 'entry', 'entries')} with an unknown category will be saved as facts.`}
              </p>
            )}
            <div className="mt-3 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingImport(null)}
                disabled={importing}
                className="rounded-md px-3 py-2 text-sm font-medium text-text-secondary hover:bg-bg-secondary hover:text-text-primary disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmImport}
                disabled={importing}
                className="inline-flex items-center gap-2 rounded-md bg-accent-primary px-3 py-2 text-sm font-medium text-[var(--color-text-on-accent)] hover:bg-accent-primary/90 disabled:opacity-50"
              >
                {importing && <Loader2 className="h-4 w-4 animate-spin" />}
                <span>{importing ? 'Importing…' : 'Import'}</span>
              </button>
            </div>
          </div>
        )}
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
