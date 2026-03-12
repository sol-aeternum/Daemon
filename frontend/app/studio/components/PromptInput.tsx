"use client";

import { useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import { useStudio } from "../StudioProvider";
import { useImageGeneration } from "../hooks/useImageGeneration";

export function PromptInput() {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const { prompt, setPrompt, selectedModels, isGenerating, referenceImage, clearReference } = useStudio();
  const { generate } = useImageGeneration();

  useEffect(() => {
    const node = textareaRef.current;
    if (!node) {
      return;
    }
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 128)}px`;
  }, [prompt]);

  const disabled = prompt.trim().length === 0 || selectedModels.length === 0 || isGenerating;

  return (
    <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Prompt</h2>

      <textarea
        ref={textareaRef}
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder="Describe your image..."
        rows={2}
        className="w-full resize-none rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] px-3 py-2 text-sm text-[var(--color-text-primary)]"
      />
      <p className="mt-1 text-right text-[10px] text-[var(--color-text-muted)]">{prompt.length} chars</p>

      {referenceImage && (
        <div className="mt-3 rounded-lg border border-[var(--color-border-primary)] p-2">
          <img src={referenceImage.url} alt="Reference" className="h-24 w-full rounded-md object-cover" />
          <button
            type="button"
            className="mt-2 w-full rounded-md border border-[var(--color-border-primary)] px-2 py-1 text-xs text-[var(--color-text-secondary)]"
            onClick={clearReference}
          >
            Clear
          </button>
        </div>
      )}

      <button
        type="button"
        className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-primary)] px-4 py-3 text-sm font-semibold text-[var(--color-text-primary)] transition-colors hover:bg-[var(--color-bg-hover)] disabled:cursor-not-allowed disabled:opacity-60"
        disabled={disabled}
        onClick={() => {
          void generate();
        }}
      >
        <Sparkles className="h-4 w-4" />
        {isGenerating ? "Generating..." : "Generate"}
      </button>
    </section>
  );
}
