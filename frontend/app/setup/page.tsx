"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { refreshAccessToken, completeSetup } from "../../lib/auth";
import { Sparkles, Shield, AlertCircle } from "lucide-react";

export default function SetupPage() {
  const router = useRouter();
  const [isChecking, setIsChecking] = useState(true);
  const [setupToken, setSetupToken] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function checkAuth() {
      const result = await refreshAccessToken().catch(() => null);
      if (!cancelled && result?.success) {
        router.push("/");
        return;
      }
      if (!cancelled) {
        setIsChecking(false);
      }
    }

    void checkAuth();

    return () => {
      cancelled = true;
    };
  }, [router]);

  async function handleSubmit(e: { preventDefault: () => void }) {
    e.preventDefault();
    setError(null);

    const token = setupToken.trim();
    if (!token) {
      setError("Please enter the setup token.");
      return;
    }

    setIsSubmitting(true);
    try {
      const result = await completeSetup(
        token,
        displayName.trim() || undefined
      );
      if (result.success) {
        router.push("/");
      } else {
        setError(result.error || "Setup failed. Please try again.");
      }
    } catch {
      setError("Network error. Please check your connection and try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  if (isChecking) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-[var(--color-bg-tertiary)]">
        <div className="flex flex-col items-center gap-4">
          <div className="relative">
            <div className="absolute inset-0 bg-[var(--color-accent-primary)] blur-xl opacity-30 rounded-full animate-pulse" />
            <div className="relative w-12 h-12 rounded-xl bg-gradient-to-br from-[var(--color-accent-primary)] to-[var(--color-accent-hover)] flex items-center justify-center shadow-lg">
              <Sparkles className="w-6 h-6 text-white animate-spin" style={{ animationDuration: "2s" }} />
            </div>
          </div>
          <p className="text-sm text-[var(--color-text-muted)]">Checking session...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-[var(--color-bg-tertiary)] px-4 py-12">
      <div className="w-full max-w-md space-y-8">
      <div className="flex flex-col items-center text-center space-y-4">
          <div className="relative">
            <div className="absolute inset-0 bg-[var(--color-accent-primary)] blur-xl opacity-30 rounded-full" />
            <div className="relative w-14 h-14 rounded-2xl bg-gradient-to-br from-[var(--color-accent-primary)] to-[var(--color-accent-hover)] flex items-center justify-center shadow-lg">
              <Sparkles className="w-7 h-7 text-white" />
            </div>
          </div>
          <div>
            <h1 className="text-2xl font-bold text-[var(--color-text-primary)] tracking-tight">
              Welcome to Daemon
            </h1>
            <p className="text-sm text-[var(--color-text-muted)] mt-1">
              Complete first-boot setup to get started
            </p>
          </div>
        </div>

      <form onSubmit={handleSubmit} className="space-y-5">
          <div className="space-y-4">
            <div>
              <label
                htmlFor="setup-token"
                className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
              >
                Setup Token
              </label>
              <input
                id="setup-token"
                type="text"
                autoComplete="off"
                placeholder="Paste your setup token here"
                value={setupToken}
                onChange={(e) => setSetupToken(e.target.value)}
                disabled={isSubmitting}
                className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>

            <div>
              <label
                htmlFor="display-name"
                className="block text-sm font-medium text-[var(--color-text-secondary)] mb-1.5"
              >
                Display Name <span className="text-[var(--color-text-muted)] font-normal">(optional)</span>
              </label>
              <input
                id="display-name"
                type="text"
                autoComplete="off"
                placeholder="e.g. MacBook Pro, Work Laptop"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                disabled={isSubmitting}
                className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-3 py-2.5 text-[var(--color-text-primary)] placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-accent-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
              />
            </div>
          </div>

          {error && (
            <div className="flex items-start gap-2.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2.5">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
              <p className="text-sm text-red-300">{error}</p>
            </div>
          )}

          <button
            type="submit"
            disabled={isSubmitting || !setupToken.trim()}
            className="w-full rounded-xl bg-[var(--color-accent-primary)] px-4 py-3 text-sm font-semibold text-white shadow-sm hover:bg-[var(--color-accent-hover)] focus:outline-none focus:ring-2 focus:ring-[var(--color-accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--color-bg-tertiary)] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isSubmitting ? "Setting up..." : "Complete Setup"}
          </button>
        </form>

      <div className="flex items-start gap-3 rounded-xl border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3">
          <Shield className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-accent-primary)]" />
          <div className="space-y-1">
            <p className="text-xs font-medium text-[var(--color-text-secondary)]">
              Why a form, not a URL?
            </p>
            <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
              Pasting the token into this form avoids leakage through browser history,
              Referer headers, access logs, and bookmarks. The token is sent in a
              POST body only.
            </p>
            <p className="text-xs text-[var(--color-text-muted)] leading-relaxed">
              Server logs remain sensitive — the startup token is printed there at
              first boot. Treat your logs as confidential.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
