"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import { startEnrollment } from "@/lib/auth";
import {
  X,
  Loader2,
  AlertCircle,
  Copy,
  Check,
  Smartphone,
  Clock,
} from "lucide-react";

interface EnrollmentModalProps {
  isOpen: boolean;
  onClose: () => void;
}

type EnrollmentState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; pendingId: string; code: string; expiresAtMs: number };

function formatEnrollmentCode(raw: string): string {
  const digits = raw.replace(/\D/g, "");
  if (digits.length === 8) {
    return `${digits.slice(0, 4)}-${digits.slice(4)}`;
  }
  return raw;
}

function toMs(ts: number): number {
  return ts > 1e12 ? ts : ts * 1000;
}

function formatTimeLeft(ms: number): string {
  if (ms <= 0) return "Expired";
  const totalSeconds = Math.ceil(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export default function EnrollmentModal({
  isOpen,
  onClose,
}: EnrollmentModalProps) {
  const [state, setState] = useState<EnrollmentState>({ status: "loading" });
  const [timeLeft, setTimeLeft] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onCloseRef = useRef(onClose);
  const cancelledRef = useRef(false);

  onCloseRef.current = onClose;

  const loadEnrollment = useCallback(
    async (shouldCancel?: () => boolean) => {
      setState({ status: "loading" });
      const result = await startEnrollment();
      if (shouldCancel?.()) return;
      if (
        !result.success ||
        !result.pendingId ||
        !result.code ||
        result.expiresAt == null
      ) {
        setState({
          status: "error",
          message: result.error || "Failed to start enrollment",
        });
        return;
      }
      const expiresAtMs = toMs(result.expiresAt);
      const formattedCode = formatEnrollmentCode(result.code);
      setState({
        status: "ready",
        pendingId: result.pendingId,
        code: formattedCode,
        expiresAtMs,
      });
      setTimeLeft(Math.max(0, expiresAtMs - Date.now()));
    },
    []
  );

  useEffect(() => {
    if (!isOpen) {
      setState({ status: "loading" });
      setTimeLeft(0);
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      return;
    }

    cancelledRef.current = false;
    loadEnrollment(() => cancelledRef.current).catch(() => {
      if (!cancelledRef.current) {
        setState({
          status: "error",
          message: "Failed to start enrollment",
        });
      }
    });

    return () => {
      cancelledRef.current = true;
    };
  }, [isOpen, loadEnrollment]);

  useEffect(() => {
    if (state.status !== "ready") return;

    const { expiresAtMs } = state;
    timerRef.current = setInterval(() => {
      setTimeLeft(() => {
        const next = expiresAtMs - Date.now();
        return next <= 0 ? 0 : next;
      });
    }, 1000);

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [state]);

  useEffect(() => {
    if (!isOpen) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onCloseRef.current();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  const handleCopy = useCallback(async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      setTimeout(() => {
        setCopied((current) => (current === label ? null : current));
      }, 2000);
    } catch {
      // Clipboard unavailable — silently ignore
    }
  }, []);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-modal flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-bg-overlay" onClick={onClose} />
      <div className="relative w-full max-w-md bg-bg-secondary rounded-xl border border-border-primary shadow-xl animate-scale">
        <div className="p-6">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-accent-subtle flex items-center justify-center">
                <Smartphone className="w-5 h-5 text-accent-primary" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-text-primary">
                  Add new device
                </h3>
                <p className="text-xs text-text-muted">
                  Scan the QR code or enter the code
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="p-1 rounded-md hover:bg-bg-tertiary text-text-muted hover:text-text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus/50"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {state.status === "loading" && (
            <div className="flex flex-col items-center justify-center py-10 gap-3">
              <Loader2 className="w-8 h-8 text-accent-primary animate-spin" />
              <p className="text-sm text-text-secondary">
                Generating enrollment code...
              </p>
            </div>
          )}

          {state.status === "error" && (
            <div className="flex flex-col items-center justify-center py-8 gap-3">
              <AlertCircle className="w-8 h-8 text-status-error" />
              <p className="text-sm text-text-secondary text-center">
                {state.message}
              </p>
              <button
                type="button"
                onClick={loadEnrollment}
                className="mt-2 px-4 py-2 text-sm font-medium text-accent-primary hover:bg-accent-subtle rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-accent-primary/50"
              >
                Try again
              </button>
            </div>
          )}

          {state.status === "ready" && (
            <div className="space-y-5">
              <div className="flex flex-col items-center">
                <div className="p-4 bg-white rounded-xl border border-border-primary">
                  <QRCodeSVG
                    value={`daemon-enroll://${state.pendingId}#${state.code}`}
                    size={192}
                    level="M"
                  />
                </div>
              </div>

              <div className="text-center">
                <p className="text-xs text-text-muted mb-1">Enrollment code</p>
                <p className="text-3xl font-mono font-bold tracking-wider text-text-primary">
                  {state.code}
                </p>
              </div>

              <div className="flex items-center justify-center gap-2 text-sm text-status-warning">
                <Clock className="w-4 h-4" />
                <span>
                  {timeLeft > 0
                    ? `Expires in ${formatTimeLeft(timeLeft)}`
                    : "Expired"}
                </span>
              </div>

              <div className="space-y-1">
                <p className="text-xs text-text-muted">Pending ID</p>
                <div className="flex items-center gap-2 p-2.5 bg-bg-primary border border-border-primary rounded-md">
                  <code className="flex-1 text-xs text-text-secondary font-mono break-all">
                    {state.pendingId}
                  </code>
                  <button
                    type="button"
                    onClick={() => handleCopy(state.pendingId, "pendingId")}
                    className="p-1.5 rounded-md hover:bg-bg-tertiary text-text-muted hover:text-text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus/50 flex-shrink-0"
                    aria-label="Copy pending ID"
                    title="Copy pending ID"
                  >
                    {copied === "pendingId" ? (
                      <Check className="w-4 h-4 text-status-success" />
                    ) : (
                      <Copy className="w-4 h-4" />
                    )}
                  </button>
                </div>
              </div>

              <div className="space-y-1">
                <p className="text-xs text-text-muted">Fallback payload</p>
                <div className="flex items-center gap-2 p-2.5 bg-bg-primary border border-border-primary rounded-md">
                  <code className="flex-1 text-xs text-text-secondary font-mono break-all">
                    {`daemon-enroll://${state.pendingId}#${state.code}`}
                  </code>
                  <button
                    type="button"
                    onClick={() =>
                      handleCopy(
                        `daemon-enroll://${state.pendingId}#${state.code}`,
                        "payload"
                      )
                    }
                    className="p-1.5 rounded-md hover:bg-bg-tertiary text-text-muted hover:text-text-primary transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus/50 flex-shrink-0"
                    aria-label="Copy fallback payload"
                    title="Copy fallback payload"
                  >
                    {copied === "payload" ? (
                      <Check className="w-4 h-4 text-status-success" />
                    ) : (
                      <Copy className="w-4 h-4" />
                    )}
                  </button>
                </div>
              </div>

              <div className="p-3 rounded-md bg-bg-tertiary border border-border-primary">
                <p className="text-xs text-text-secondary leading-relaxed">
                  Open the Daemon app on your new device and choose{" "}
                  <strong>Enroll device</strong>. Scan the QR code above, or
                  enter the 8-digit code manually. The code expires automatically
                  for security.
                </p>
              </div>
            </div>
          )}

          <div className="flex items-center justify-end gap-3 mt-6 pt-4 border-t border-border-primary">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary hover:bg-bg-tertiary rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus/50"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
