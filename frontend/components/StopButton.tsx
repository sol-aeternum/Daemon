'use client';

import { Square } from 'lucide-react';

type StopButtonProps = {
  onStop: () => void;
};

export function StopButton({ onStop }: StopButtonProps) {
  return (
    <button
      type="button"
      aria-label="Stop generating"
      title="Stop generating"
      onClick={onStop}
      className="flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--color-status-stop)] text-[var(--color-text-on-status)] shadow-stop transition-all duration-200 hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-border-focus)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-bg-secondary)]"
    >
      <Square className="h-4 w-4 fill-current" aria-hidden="true" />
    </button>
  );
}
