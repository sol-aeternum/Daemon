"use client";

import { useState, useRef, useEffect } from "react";
import { useTheme } from "next-themes";
import { Settings, LogOut, Sun, Moon, Monitor, ChevronUp } from "lucide-react";
import { useRouter } from "next/navigation";

interface AccountWidgetProps {
  displayName?: string;
  tier?: string;
}

export function AccountWidget({
  displayName = "User",
  tier = "Pro",
}: AccountWidgetProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const { theme, setTheme } = useTheme();
  const router = useRouter();

  useEffect(() => {
    setMounted(true);
  }, []);

  // Close dropdown when clicking outside
  useEffect(() => {
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

    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  // Generate initials from display name
  const initials = displayName
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  // Generate a consistent color based on the display name
  const getAvatarColor = (name: string): string => {
    const colors = [
      "var(--color-accent-primary)",
      "var(--color-accent-hover)",
      "var(--color-status-info)",
      "var(--color-status-success)",
      "var(--color-status-warning)",
      "var(--color-status-error)",
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
    const conversationId = params.get("id");
    router.push(conversationId ? `/settings/profile?from=${conversationId}` : "/settings/profile");
    setIsOpen(false);
  };

  const handleLogout = () => {
    // Placeholder for logout action
    setIsOpen(false);
  };

  const toggleTheme = (newTheme: string) => {
    setTheme(newTheme);
  };

  // Prevent hydration mismatch
  if (!mounted) {
    return (
      <div className="border-t border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-semibold text-white shrink-0 animate-pulse"
               style={{ backgroundColor: avatarColor }}>
            {initials}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-[var(--color-text-primary)] truncate">
              {displayName}
            </p>
            <p className="text-xs text-[var(--color-text-muted)]">{tier}</p>
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
        className={`w-full border-t border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] px-4 py-3 flex items-center gap-3 hover:bg-[var(--color-bg-hover)] transition-colors ${
          isOpen ? "bg-[var(--color-bg-hover)]" : ""
        }`}
      >
        {/* Avatar */}
        <div
          className="w-9 h-9 rounded-full flex items-center justify-center text-sm font-semibold text-white shrink-0"
          style={{ backgroundColor: avatarColor }}
        >
          {initials}
        </div>

        {/* Name and tier */}
        <div className="flex-1 min-w-0 text-left">
          <p className="text-sm font-medium text-[var(--color-text-primary)] truncate">
            {displayName}
          </p>
          <p className="text-xs text-[var(--color-text-muted)]">{tier}</p>
        </div>

        {/* Chevron */}
        <ChevronUp
          className={`w-4 h-4 text-[var(--color-text-muted)] transition-transform duration-200 ${
            isOpen ? "" : "rotate-180"
          }`}
        />
      </button>

      {/* Dropdown menu - opens upward */}
      {isOpen && (
        <div
          className="absolute bottom-full left-0 right-0 mb-1 bg-[var(--color-bg-secondary)] rounded-lg shadow-lg border border-[var(--color-border-muted)] py-1 z-50 animate-fade-in"
          style={{ animationDuration: "150ms" }}
        >
          {/* Settings option */}
          <button
            onClick={handleSettings}
            className="flex w-full min-h-[44px] items-center gap-2 px-3 py-2 text-left text-sm text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-hover)]"
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
                onClick={() => toggleTheme("light")}
                className={`flex min-h-[44px] flex-1 items-center justify-center gap-1 rounded px-2 py-1.5 text-xs transition-colors ${
                  theme === "light"
                    ? "bg-[var(--color-accent-primary)] text-white"
                    : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                }`}
                title="Light theme"
              >
                <Sun className="w-3.5 h-3.5" />
                <span>Light</span>
              </button>
              <button
                onClick={() => toggleTheme("dark")}
                className={`flex min-h-[44px] flex-1 items-center justify-center gap-1 rounded px-2 py-1.5 text-xs transition-colors ${
                  theme === "dark"
                    ? "bg-[var(--color-accent-primary)] text-white"
                    : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
                }`}
                title="Dark theme"
              >
                <Moon className="w-3.5 h-3.5" />
                <span>Dark</span>
              </button>
              <button
                onClick={() => toggleTheme("system")}
                className={`flex min-h-[44px] flex-1 items-center justify-center gap-1 rounded px-2 py-1.5 text-xs transition-colors ${
                  theme === "system"
                    ? "bg-[var(--color-accent-primary)] text-white"
                    : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)]"
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
            className="mt-1 flex w-full min-h-[44px] items-center gap-2 border-t border-[var(--color-border-muted)] px-3 py-2 text-left text-sm text-[var(--color-status-error)] transition-colors hover:bg-[var(--color-status-error-bg)]"
          >
            <LogOut className="w-4 h-4" />
            Log out
          </button>
        </div>
      )}
    </div>
  );
}
