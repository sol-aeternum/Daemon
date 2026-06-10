interface CouncilProgressEvent {
  type: 'council_progress';
  stage: string;
  current_round: number;
  total_rounds: number;
  models_complete: number;
  models_total: number;
}

interface CouncilProgressProps {
  event: CouncilProgressEvent;
}

export function CouncilProgress({ event }: CouncilProgressProps) {
  const { stage, current_round, total_rounds, models_complete, models_total } =
    event;

  const progressPercentage =
    models_total > 0 ? (models_complete / models_total) * 100 : 0;

  return (
    <div className="bg-[var(--color-bg-secondary)] rounded-lg shadow-lg border border-[var(--color-border-primary)] overflow-hidden w-full transition-all duration-300 ease-in-out">
      <div className="p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="text-xl">🏛️</span>
            <span className="font-medium text-sm text-[var(--color-text-secondary)]">
              Council Deliberation
            </span>
          </div>
          <span className="text-xs text-[var(--color-text-muted)]">
            Round {current_round}/{total_rounds}
          </span>
        </div>

        <div className="text-xs text-[var(--color-text-secondary)] mb-3">
          {stage}
        </div>

        <div className="flex items-center justify-between text-xs text-[var(--color-text-muted)] mb-1">
          <span>Model responses</span>
          <span>
            {models_complete}/{models_total}
          </span>
        </div>

        <div className="w-full bg-[var(--color-border-primary)] rounded-full h-1.5">
          <div
            className="h-1.5 rounded-full transition-all duration-500 bg-[var(--color-accent-primary)]"
            style={{ width: `${progressPercentage}%` }}
          />
        </div>
      </div>
    </div>
  );
}
