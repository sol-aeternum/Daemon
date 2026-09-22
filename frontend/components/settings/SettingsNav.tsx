'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { User, Mic, Palette, Brain, Monitor, Sparkles } from 'lucide-react';

const items = [
  { href: '/settings/profile', label: 'Profile', icon: User },
  { href: '/settings/voice', label: 'Voice', icon: Mic },
  { href: '/settings/appearance', label: 'Appearance', icon: Palette },
  { href: '/settings/memory', label: 'Memory', icon: Brain },
  { href: '/settings/devices', label: 'Devices', icon: Monitor },
  { href: '/settings/skills', label: 'Skills', icon: Sparkles },
];

export function SettingsNav() {
  const pathname = usePathname();
  const from = useSearchParams().get('from');

  return (
    <nav
      aria-label="Settings"
      className="flex flex-col gap-2 md:flex-row md:flex-wrap"
    >
      {items.map(({ href, label, icon: Icon }) => (
        <Link
          key={href}
          href={from ? `${href}?from=${encodeURIComponent(from)}` : href}
          aria-current={pathname === href ? 'page' : undefined}
          className={`inline-flex min-h-[44px] w-full items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors md:w-auto ${
            pathname === href
              ? 'bg-[var(--color-accent-subtle)] text-[var(--color-text-primary)]'
              : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]'
          }`}
        >
          <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
          {label}
        </Link>
      ))}
    </nav>
  );
}
