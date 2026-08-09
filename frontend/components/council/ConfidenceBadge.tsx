'use client';

interface ConfidenceBadgeProps {
  score: number | null;
  size?: 'sm' | 'md';
}

export function ConfidenceBadge({ score, size = 'md' }: ConfidenceBadgeProps) {
  if (score === null || score === undefined) {
    return null;
  }

  const getColorClasses = (score: number) => {
    if (score <= 3) {
      return 'bg-red-500/20 text-red-400 border-red-500/30';
    }
    if (score <= 6) {
      return 'bg-amber-500/20 text-amber-400 border-amber-500/30';
    }
    if (score <= 8) {
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
    }
    return 'bg-green-500/20 text-green-400 border-green-500/30';
  };

  const sizeClasses =
    size === 'sm' ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm';

  return (
    <span
      className={`inline-flex items-center rounded-full border font-medium ${sizeClasses} ${getColorClasses(
        score,
      )}`}
    >
      {score}/10
    </span>
  );
}
