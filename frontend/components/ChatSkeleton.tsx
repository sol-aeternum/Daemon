export default function ChatSkeleton() {
  return (
    <div className="flex h-screen bg-[var(--color-bg-tertiary)]">
      {/* Sidebar skeleton */}
      <div className="w-64 border-r border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4 space-y-3">
        <div className="h-8 bg-[var(--color-bg-secondary)] rounded animate-pulse" />
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div
              key={i}
              className="h-10 bg-[var(--color-bg-tertiary)] rounded animate-pulse"
            />
          ))}
        </div>
      </div>

      {/* Main area skeleton */}
      <div className="flex-1 flex flex-col">
        {/* Header skeleton */}
        <div className="h-14 border-b border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 flex items-center">
          <div className="h-6 w-32 bg-[var(--color-bg-secondary)] rounded animate-pulse" />
        </div>

        {/* Messages skeleton */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {[...Array(4)].map((_, i) => (
            <div
              key={i}
              className={`flex ${i % 2 === 0 ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[70%] p-3 rounded-lg ${
                  i % 2 === 0
                    ? 'bg-[var(--color-accent-primary)] text-white'
                    : 'bg-[var(--color-bg-tertiary)] text-[var(--color-text-primary)]'
                }`}
              >
                <div className="h-4 bg-[var(--color-border-secondary)]/50 rounded animate-pulse mb-2" />
                <div className="h-4 bg-[var(--color-border-secondary)]/50 rounded animate-pulse w-3/4" />
              </div>
            </div>
          ))}
        </div>

        {/* Input skeleton */}
        <div className="p-4 border-t border-[var(--color-border-primary)]">
          <div className="h-12 bg-[var(--color-bg-tertiary)] rounded-lg animate-pulse" />
        </div>
      </div>
    </div>
  );
}
