'use client';

import { useTheme } from '@/lib/theme';
import { useEffect, useState } from 'react';
import { SkeletonLine, SkeletonBlock, SkeletonCircle } from '@/components/ui/Skeleton';
import { Palette, Monitor, Sun, Moon, Check } from 'lucide-react';

type ThemeOption = 'dark' | 'light' | 'system';

interface ThemeConfig {
  id: ThemeOption;
  label: string;
  description: string;
  icon: typeof Sun;
  preview: {
    bg: string;
    sidebar: string;
    card: string;
    accent: string;
    text: string;
  };
}

const themes: ThemeConfig[] = [
  {
    id: 'dark',
    label: 'Dark',
    description: 'Easy on the eyes in low light',
    icon: Moon,
    preview: {
      bg: 'var(--color-bg-primary)',
      sidebar: 'var(--color-bg-tertiary)',
      card: 'var(--color-bg-secondary)',
      accent: 'var(--color-accent-primary)',
      text: 'var(--color-text-primary)',
    },
  },
  {
    id: 'light',
    label: 'Light',
    description: 'Clean and crisp for daytime',
    icon: Sun,
    preview: {
      bg: 'var(--color-bg-inverse)',
      sidebar: 'var(--color-bg-secondary)',
      card: 'var(--color-bg-tertiary)',
      accent: 'var(--color-accent-primary)',
      text: 'var(--color-text-inverse)',
    },
  },
  {
    id: 'system',
    label: 'System',
    description: 'Follows your OS preference',
    icon: Monitor,
    preview: {
      bg: 'linear-gradient(135deg, var(--color-bg-inverse) 50%, var(--color-bg-primary) 50%)',
      sidebar: 'linear-gradient(135deg, var(--color-bg-secondary) 50%, var(--color-bg-tertiary) 50%)',
      card: 'linear-gradient(135deg, var(--color-bg-tertiary) 50%, var(--color-bg-secondary) 50%)',
      accent: 'var(--color-accent-primary)',
      text: 'var(--color-text-muted)',
    },
  },
];

export default function AppearanceTab() {
  const { theme, setTheme, resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  const handleThemeChange = (newTheme: ThemeOption) => {
    setTheme(newTheme);
  };

  if (!mounted) {
    return (
      <div className="animate-fade-in">
        <div className="flex items-center gap-3 mb-6">
          <SkeletonCircle size={40} />
          <div className="space-y-2">
            <SkeletonLine width={128} height={20} />
            <SkeletonLine width={192} height={16} />
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="rounded-xl border border-border-primary bg-bg-secondary p-4 space-y-3"
            >
              <SkeletonBlock className="w-full aspect-video rounded-lg" />
              <div className="flex items-center gap-3">
                <SkeletonCircle size={20} />
                <div className="flex-1 space-y-1">
                  <SkeletonLine width={64} height={16} />
                  <SkeletonLine width={96} height={12} />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const currentTheme = (theme as ThemeOption) || 'system';

  return (
    <div className="animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6 pb-6 border-b border-border-primary">
        <div className="w-10 h-10 rounded-full bg-accent-subtle flex items-center justify-center">
          <Palette className="w-5 h-5 text-accent-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Appearance</h2>
          <p className="text-sm text-text-muted">
            Customize the look and feel of your workspace
          </p>
        </div>
      </div>

      {/* Theme Selection */}
      <section className="space-y-5">
        <div className="flex items-center gap-2">
          <Palette className="w-5 h-5 text-accent-primary" />
          <h3 className="text-base font-semibold text-text-primary">Theme</h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {themes.map((themeOption) => {
            const Icon = themeOption.icon;
            const isSelected = currentTheme === themeOption.id;

            return (
              <button
                key={themeOption.id}
                onClick={() => handleThemeChange(themeOption.id)}
                className={`group relative rounded-xl border-2 p-4 text-left transition-all duration-200 ${
                  isSelected
                    ? 'border-accent-primary bg-accent-subtle'
                    : 'border-border-primary bg-bg-secondary hover:border-border-focus hover:bg-bg-hover'
                }`}
                aria-pressed={isSelected}
              >
                {/* Selection Indicator */}
                {isSelected && (
                  <div className="absolute top-3 right-3 w-5 h-5 rounded-full bg-accent-primary flex items-center justify-center">
                    <Check className="w-3 h-3 text-white" />
                  </div>
                )}

                {/* Visual Preview Swatch */}
                <div
                  className="w-full aspect-video rounded-lg mb-4 overflow-hidden shadow-sm"
                  style={{ background: themeOption.preview.bg }}
                >
                  <div className="flex h-full">
                    {/* Sidebar preview */}
                    <div
                      className="w-1/4 h-full"
                      style={{ background: themeOption.preview.sidebar }}
                    />
                    {/* Main content preview */}
                    <div className="flex-1 p-2 space-y-2">
                      <div
                        className="w-3/4 h-2 rounded"
                        style={{ background: themeOption.preview.card }}
                      />
                      <div
                        className="w-1/2 h-2 rounded"
                        style={{ background: themeOption.preview.card }}
                      />
                      <div className="pt-2">
                        <div
                          className="w-8 h-4 rounded"
                          style={{ background: themeOption.preview.accent }}
                        />
                      </div>
                    </div>
                  </div>
                </div>

                {/* Label and Description */}
                <div className="flex items-start gap-3">
                  <div
                    className={`w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 mt-0.5 transition-colors ${
                      isSelected
                        ? 'border-accent-primary bg-accent-primary'
                        : 'border-border-primary group-hover:border-border-focus'
                    }`}
                  >
                    {isSelected && <div className="w-2 h-2 rounded-full bg-white" />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <Icon
                        className={`w-4 h-4 ${
                          isSelected ? 'text-accent-primary' : 'text-text-muted'
                        }`}
                      />
                      <span
                        className={`font-medium ${
                          isSelected ? 'text-text-primary' : 'text-text-secondary'
                        }`}
                      >
                        {themeOption.label}
                      </span>
                    </div>
                    <p className="text-xs text-text-muted mt-0.5">
                      {themeOption.description}
                    </p>
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {/* Current Theme Info */}
        <div className="mt-6 p-4 rounded-lg bg-bg-secondary border border-border-primary">
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <Monitor className="w-4 h-4 text-accent-primary" />
            <span>
              Currently using{' '}
              <span className="font-medium text-text-primary capitalize">
                {resolvedTheme || 'dark'}
              </span>{' '}
              theme
              {currentTheme === 'system' && (
                <span className="text-text-muted">
                  {' '}
                  (system preference)
                </span>
              )}
            </span>
          </div>
        </div>
      </section>
    </div>
  );
}
