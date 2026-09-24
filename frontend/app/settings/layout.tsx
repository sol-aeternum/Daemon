'use client';

import { Suspense } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { SettingsNav } from '@/components/settings/SettingsNav';
import {
  ConversationList,
  type SidebarSection,
} from '@/components/ConversationList';
import { useConversationHistory } from '@/hooks/useConversationHistory';

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
      className="hidden md:flex"
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
  const href = fromConversationId
    ? `/?id=${encodeURIComponent(fromConversationId)}`
    : '/';

  return (
    <Link
      href={href}
      className="inline-flex min-h-touch items-center rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]"
    >
      ← Chat
    </Link>
  );
}

export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex h-screen overflow-hidden flex-col md:flex-row">
      <Suspense
        fallback={
          <aside className="hidden h-screen w-sidebar border-r border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] md:block" />
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
                  className="inline-flex min-h-touch items-center rounded-md px-3 py-2 text-sm text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]"
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
            <Suspense fallback={<div className="min-h-touch" />}>
              <SettingsNav />
            </Suspense>
          </aside>

          <section className="w-full">{children}</section>
        </div>
      </main>
    </div>
  );
}
