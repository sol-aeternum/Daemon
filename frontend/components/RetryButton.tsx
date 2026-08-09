import { RefreshCw } from 'lucide-react';

interface RetryButtonProps {
  onRetry: () => void;
  isLoading?: boolean;
}

export function RetryButton({ onRetry, isLoading }: RetryButtonProps) {
  return (
    <button
      onClick={onRetry}
      disabled={isLoading}
      className="p-2 rounded-full bg-[var(--color-status-error-bg)] text-[var(--color-status-error)] hover:bg-[var(--color-status-error-bg)]/80 transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--color-status-error)] disabled:opacity-50"
      title="Retry sending message"
      type="button"
    >
      <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
    </button>
  );
}
