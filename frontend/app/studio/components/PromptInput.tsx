"use client";

import { useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import { useStudio } from "../StudioProvider";
import { useImageGeneration } from "../hooks/useImageGeneration";

interface PromptInputProps {
  mode?: "image" | "video";
  onGenerateVideo?: () => Promise<void> | void;
  videoGenerateDisabled?: boolean;
  videoGenerateDisabledReason?: string | null;
  videoButtonLabel?: string;
}

export function PromptInput({
  mode = "image",
  onGenerateVideo,
  videoGenerateDisabled = false,
  videoGenerateDisabledReason = null,
  videoButtonLabel,
}: PromptInputProps) {
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

  const disabled =
    mode === "image"
      ? prompt.trim().length === 0 || selectedModels.length === 0 || isGenerating
      : prompt.trim().length === 0 || isGenerating || videoGenerateDisabled;

  return (
    <section className="rounded-2xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-[var(--color-text-secondary)]">Prompt</h2>

      <textarea
        ref={textareaRef}
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder={mode === "video" ? "Describe your video..." : "Describe your image..."}
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
          if (mode === "image") {
            void generate();
            return;
          }
          if (onGenerateVideo) {
            void onGenerateVideo();
          }
        }}
      >
        <Sparkles className="h-4 w-4" />
        {isGenerating ? "Generating..." : mode === "video" ? (videoButtonLabel ?? "Generate video") : "Generate"}
      </button>

      {mode === "video" && videoGenerateDisabledReason && (
        <p className="mt-2 text-xs text-[var(--color-status-warning)]">{videoGenerateDisabledReason}</p>
      )}
    </section>
  );
}
