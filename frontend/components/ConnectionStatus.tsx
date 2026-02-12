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
      <div className="flex items-center gap-2 text-sm text-green-600">
        <span className="w-2 h-2 bg-green-500 rounded-full"></span>
        <span>Connected</span>
      </div>
    );
  }

  if (status === "reconnecting") {
    return (
      <div className="flex items-center gap-2 text-sm text-yellow-600">
        <span className="w-2 h-2 bg-yellow-500 rounded-full animate-pulse"></span>
        <span>Reconnecting...</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 text-sm text-red-600">
        <span className="w-2 h-2 bg-red-500 rounded-full"></span>
        <span>Disconnected</span>
      </div>
      {showReconnect && onReconnect && (
        <button
          onClick={onReconnect}
          className="text-xs bg-blue-600 text-white px-2 py-1 rounded hover:bg-blue-700 transition-colors"
        >
          Retry
        </button>
      )}
    </div>
  );
}
