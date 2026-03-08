"use client";

import { useEffect, useState } from "react";

type ConnectionStatus = "connected" | "disconnected" | "reconnecting";

interface ConnectionStatusProps {
  status: ConnectionStatus;
  onReconnect?: () => void;
}

export function ConnectionStatus({ status, onReconnect }: ConnectionStatusProps) {
  const [showReconnect, setShowReconnect] = useState(false);

  useEffect(() => {
    if (status === "disconnected") {
      const timer = setTimeout(() => setShowReconnect(true), 2000);
      return () => clearTimeout(timer);
    } else {
      setShowReconnect(false);
    }
  }, [status]);

  if (status === "connected") {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-status-success)]">
        <span className="w-2 h-2 rounded-full bg-[var(--color-status-success)]"></span>
        <span>Connected</span>
      </div>
    );
  }

  if (status === "reconnecting") {
    return (
      <div className="flex items-center gap-2 text-sm text-[var(--color-status-warning)]">
        <span className="w-2 h-2 rounded-full bg-[var(--color-status-warning)] animate-pulse"></span>
        <span>Reconnecting...</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 text-sm text-[var(--color-status-error)]">
        <span className="w-2 h-2 rounded-full bg-[var(--color-status-error)]"></span>
        <span>Disconnected</span>
      </div>
      {showReconnect && onReconnect && (
        <button
          onClick={onReconnect}
          className="min-h-[44px] rounded bg-[var(--color-accent-primary)] px-3 py-1 text-xs text-white transition-colors hover:bg-[var(--color-accent-hover)]"
        >
          Retry
        </button>
      )}
    </div>
  );
}
