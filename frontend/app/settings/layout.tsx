'use client';

import { Suspense } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { ConversationList, type SidebarSection } from '@/components/ConversationList';
import { useConversationHistory } from '@/hooks/useConversationHistory';

const settingsNav = [
  { href: '/settings/profile', label: 'Profile' },
  { href: '/settings/voice', label: 'Voice' },
  { href: '/settings/appearance', label: 'Appearance' },
  { href: '/settings/memory', label: 'Memory' },
  { href: '/settings/skills', label: 'Skills' },
];

function SettingsSidebar() {
  const router = useRouter();
  const {
    conversations,
    currentId,
    isLoaded,
    updateConversation,
    createConversation,
    deleteConversation,
    switchConversation,
    searchQuery,
    setSearchQuery,
  } = useConversationHistory();

  const handleSidebarNavigate = (section: SidebarSection) => {
    if (section === 'home') {
      router.push('/');
      return;
    }

    router.push(`/${section}`);
  };

  return (
    <ConversationList
      conversations={conversations}
      currentId={currentId}
      onSelect={switchConversation}
      onUpdate={updateConversation}
      onNewChat={createConversation}
      onDelete={deleteConversation}
      searchQuery={searchQuery}
      setSearchQuery={setSearchQuery}
      isLoading={!isLoaded}
      onNavigate={handleSidebarNavigate}
      onGoHome={() => handleSidebarNavigate('home')}
    />
  );
}

function ChatBackLink() {
  const searchParams = useSearchParams();
  const fromConversationId = searchParams.get('from');
  const href = fromConversationId ? `/?id=${fromConversationId}` : '/';

  return (
    <Link
      href={href}
      className="inline-flex min-h-[44px] items-center rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]"
    >
      ← Chat
    </Link>
  );
}

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div className="flex h-screen overflow-hidden flex-col md:flex-row">
      <Suspense
        fallback={
          <aside className="hidden h-screen w-[260px] border-r border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] md:block" />
        }
      >
        <SettingsSidebar />
      </Suspense>

      <main className="flex-1 min-w-0 overflow-y-auto p-4 sm:p-6">
        <div className="mx-auto w-full max-w-4xl">
          <div className="mb-4">
            <Suspense
              fallback={
                <Link
                  href="/"
                  className="inline-flex min-h-[44px] items-center rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]"
                >
                  ← Chat
                </Link>
              }
            >
              <ChatBackLink />
            </Suspense>
          </div>

          <aside className="mb-4 w-full rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-3 md:mb-6 md:p-4">
            <div className="mb-2 px-2 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)] md:mb-3">
              Settings
            </div>
            <nav className="flex gap-2 overflow-x-auto pb-1 md:flex-wrap md:overflow-visible md:pb-0">
              {settingsNav.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`inline-flex min-h-[44px] items-center rounded-md px-3 py-2 text-sm whitespace-nowrap transition-colors ${
                    pathname === item.href
                      ? 'bg-[var(--color-accent-subtle)] text-[var(--color-text-primary)]'
                      : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]'
                  }`}
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </aside>

          <section className="w-full">{children}</section>
        </div>
      </main>
    </div>
  );
}
