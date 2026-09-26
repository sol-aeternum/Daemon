'use client';

import { useState, useRef, useEffect } from 'react';
import { useTheme } from 'next-themes';
import {
  Settings,
  LogOut,
  Sun,
  Moon,
  Monitor,
  ChevronUp,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/components/AuthProvider';
import { useClientMounted } from '@/hooks/useClientMounted';
import { useEntitlements } from '@/hooks/useEntitlements';
import { PLAN_ORDER, PLAN_SURFACE, TRIAL_SURFACE } from '@/lib/entitlements';
import type { EntitlementsResult } from '@/hooks/useEntitlements';

interface AccountWidgetProps {
  displayName?: string;
}

function PlanStatusLine({ plan }: { plan: EntitlementsResult }) {
  if (plan.status === 'loading') {
    return (
      <p
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]"
        aria-live="polite"
        aria-busy="true"
      >
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        Checking plan...
      </p>
    );
  }

  if (plan.status === 'error' || !plan.entitlements) {
    return (
      <p
        className="text-xs text-[var(--color-text-muted)]"
        aria-live="polite"
        role="status"
      >
        Plan unavailable
      </p>
    );
  }

  return (
    <p
      className="truncate text-xs text-[var(--color-text-muted)]"
      aria-live="polite"
    >
      {PLAN_SURFACE[plan.entitlements.plan].label}
    </p>
  );
}

function PlanDetails({ plan }: { plan: EntitlementsResult }) {
  const { entitlements, status, error, refresh } = plan;

  return (
    <section
      className="border-t border-[var(--color-border-muted)] px-3 py-2"
      aria-labelledby="account-plan-heading"
    >
      <p
        id="account-plan-heading"
        className="mb-1 text-xs uppercase tracking-wide text-[var(--color-text-muted)]"
      >
        Plan
      </p>

      {status === 'loading' && (
        <p
          className="text-xs text-[var(--color-text-muted)]"
          aria-live="polite"
          aria-busy="true"
        >
          Checking plan...
        </p>
      )}

      {status === 'error' && (
        <div className="text-xs" role="status">
          <p className="text-[var(--color-text-secondary)]">{error}</p>
          <button
            type="button"
            onClick={() => {
              void refresh();
            }}
            className="mt-1 inline-flex min-h-touch items-center gap-1 rounded text-[var(--color-accent-primary)] hover:text-[var(--color-accent-hover)]"
          >
            <RefreshCw className="h-3 w-3" aria-hidden="true" />
            Retry
          </button>
        </div>
      )}

      {status === 'ready' && entitlements && (
        <div className="space-y-2">
          <div>
            <p className="text-sm font-medium text-[var(--color-text-primary)]">
              {PLAN_SURFACE[entitlements.plan].headline}
            </p>
            <p className="mt-0.5 text-xs text-[var(--color-text-secondary)]">
              {PLAN_SURFACE[entitlements.plan].blurb}
            </p>
          </div>

          {entitlements.trial && (
            <p
              className="text-xs text-[var(--color-text-secondary)]"
              role="status"
            >
              <span className="font-medium text-[var(--color-text-primary)]">
                {TRIAL_SURFACE[entitlements.trial.state].status}.
              </span>{' '}
              {TRIAL_SURFACE[entitlements.trial.state].detail}
            </p>
          )}

          <ul className="space-y-1">
            {PLAN_ORDER.map((planId) => {
              const isCurrent = planId === entitlements.plan;
              return (
                <li key={planId} className="flex items-baseline gap-2 text-xs">
                  <span
                    className={
                      isCurrent
                        ? 'font-medium text-[var(--color-text-primary)]'
                        : 'text-[var(--color-text-muted)]'
                    }
                  >
                    {PLAN_SURFACE[planId].headline}
                  </span>
                  {isCurrent && (
                    <span className="text-xs uppercase tracking-wide text-[var(--color-text-muted)]">
                      Current plan
                    </span>
                  )}
                </li>
              );
            })}
          </ul>

          <p className="text-xs text-[var(--color-text-muted)]">
            Plan changes and billing are not available in this build.
          </p>
        </div>
      )}
    </section>
  );
}

export function AccountWidget({ displayName = 'User' }: AccountWidgetProps) {
  const [isOpen, setIsOpen] = useState(false);
  const mounted = useClientMounted();
  const dropdownRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const { theme, setTheme } = useTheme();
  const router = useRouter();
  const { logout } = useAuth();
  const plan = useEntitlements();

  // Dismiss the dropdown on outside click or Escape. This is a simple
  // disclosure, so no focus trap: the toggle keeps focus and aria-expanded
  // tells assistive tech whether the panel is showing.
  useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target as Node) &&
        buttonRef.current &&
        !buttonRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isOpen) {
        setIsOpen(false);
        buttonRef.current?.focus();
        event.stopPropagation();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  // Generate initials from display name
  const initials = displayName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  // Generate a consistent color based on the display name
  const getAvatarColor = (name: string): string => {
    const colors = [
      'var(--color-accent-primary)',
      'var(--color-accent-hover)',
      'var(--color-status-info)',
      'var(--color-status-success)',
      'var(--color-status-warning)',
      'var(--color-status-error)',
    ];
    let hash = 0;
    for (let i = 0; i < name.length; i++) {
      hash = name.charCodeAt(i) + ((hash << 5) - hash);
    }
    return colors[Math.abs(hash) % colors.length];
  };

  const avatarColor = getAvatarColor(displayName);

  const handleSettings = () => {
    const params = new URLSearchParams(window.location.search);
    const conversationId = params.get('id');
    router.push(
      conversationId
        ? `/settings/profile?from=${conversationId}`
        : '/settings/profile',
    );
    setIsOpen(false);
  };

  const handleLogout = () => {
    setIsOpen(false);
    void logout();
  };

  const toggleTheme = (newTheme: string) => {
    setTheme(newTheme);
  };

  // Prevent hydration mismatch
  if (!mounted) {
    return (
      <div className="border-t border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3">
        <div className="flex items-center gap-3">
          <div
            className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-semibold text-[var(--color-text-on-accent)] shrink-0 animate-pulse"
            style={{ backgroundColor: avatarColor }}
          >
            {initials}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-[var(--color-text-primary)] truncate">
              {displayName}
            </p>
            <PlanStatusLine plan={plan} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Main widget button */}
      <button
        ref={buttonRef}
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        aria-controls="account-menu"
        className={`w-full border-t border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3 flex items-center gap-3 hover:bg-[var(--color-bg-hover)] transition-colors ${
          isOpen ? 'bg-[var(--color-bg-hover)]' : ''
        }`}
      >
        {/* Avatar */}
        <div
          className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-semibold text-[var(--color-text-on-accent)] shrink-0"
          style={{ backgroundColor: avatarColor }}
        >
          {initials}
        </div>

        {/* Name and server-confirmed plan */}
        <div className="flex-1 min-w-0 text-left">
          <p className="text-sm font-medium text-[var(--color-text-primary)] truncate">
            {displayName}
          </p>
          <PlanStatusLine plan={plan} />
        </div>

        {/* Chevron */}
        <ChevronUp
          className={`w-4 h-4 text-[var(--color-text-muted)] transition-transform duration-200 ${
            isOpen ? '' : 'rotate-180'
          }`}
        />
      </button>

      {/* Dropdown menu - opens upward */}
      {isOpen && (
        <div
          data-stop-shortcut-block="true"
          id="account-menu"
          className="absolute bottom-full left-0 right-0 mb-1 bg-[var(--color-bg-secondary)] rounded-lg shadow-lg border border-[var(--color-border-muted)] py-1 z-50 animate-fade-in"
          style={{ animationDuration: '150ms' }}
        >
          <PlanDetails plan={plan} />

          {/* Settings option */}
          <button
            onClick={handleSettings}
            className="flex w-full min-h-touch items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)]"
          >
            <Settings className="w-4 h-4" />
            Settings
          </button>

          {/* Theme toggle section */}
          <div className="px-3 py-2 border-t border-[var(--color-border-muted)] mt-1">
            <p className="text-xs text-[var(--color-text-muted)] mb-2 uppercase tracking-wide">
              Theme
            </p>
            <div className="flex gap-1">
              <button
                onClick={() => toggleTheme('light')}
                className={`flex min-h-touch flex-1 items-center justify-center gap-1 rounded px-2 py-1.5 text-xs transition-colors ${
                  theme === 'light'
                    ? 'bg-[var(--color-accent-primary)] text-[var(--color-text-on-accent)]'
                    : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
                }`}
                title="Light theme"
              >
                <Sun className="w-3.5 h-3.5" />
                <span>Light</span>
              </button>
              <button
                onClick={() => toggleTheme('dark')}
                className={`flex min-h-touch flex-1 items-center justify-center gap-1 rounded px-2 py-1.5 text-xs transition-colors ${
                  theme === 'dark'
                    ? 'bg-[var(--color-accent-primary)] text-[var(--color-text-on-accent)]'
                    : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
                }`}
                title="Dark theme"
              >
                <Moon className="w-3.5 h-3.5" />
                <span>Dark</span>
              </button>
              <button
                onClick={() => toggleTheme('system')}
                className={`flex min-h-touch flex-1 items-center justify-center gap-1 rounded px-2 py-1.5 text-xs transition-colors ${
                  theme === 'system'
                    ? 'bg-[var(--color-accent-primary)] text-[var(--color-text-on-accent)]'
                    : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]'
                }`}
                title="System theme"
              >
                <Monitor className="w-3.5 h-3.5" />
                <span>Auto</span>
              </button>
            </div>
          </div>

          {/* Log out option */}
          <button
            onClick={handleLogout}
            className="mt-1 flex w-full min-h-touch items-center gap-2 border-t border-[var(--color-border-muted)] px-3 py-2 text-left text-sm text-[var(--color-status-error)] transition-colors hover:bg-[var(--color-status-error-bg)]"
          >
            <LogOut className="w-4 h-4" />
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
