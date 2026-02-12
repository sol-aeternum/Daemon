import { RefreshCw } from "lucide-react";

interface RetryButtonProps {
  onRetry: () => void;
  isLoading?: boolean;
}

export function RetryButton({ onRetry, isLoading }: RetryButtonProps) {
  return (
    <button
      onClick={onRetry}
      disabled={isLoading}
      className="p-2 rounded-full bg-red-100 text-red-600 hover:bg-red-200 transition-colors focus:outline-none focus:ring-2 focus:ring-red-500 disabled:opacity-50"
      title="Retry sending message"
      type="button"
    >
      <RefreshCw className={`w-4 h-4 ${isLoading ? "animate-spin" : ""}`} />
    </button>
  );
}
