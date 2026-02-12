import { WifiOff } from "lucide-react";

export function OfflineIndicator() {
  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-amber-500 text-white px-4 py-2 text-center shadow-md flex items-center justify-center gap-2 animate-in slide-in-from-top duration-300">
      <WifiOff className="w-4 h-4" />
      <span className="text-sm font-medium">You are offline. Retrying connection...</span>
    </div>
  );
}
