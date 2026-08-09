'use client';

import { ReactNode, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ConversationList, type SidebarSection } from './ConversationList';
import { MobileHeader } from './MobileHeader';
import { useConversationHistoryContext } from './ConversationHistoryProvider';

interface SidebarShellProps {
  section: SidebarSection;
  title: string;
  children: ReactNode;
  headerAction?: ReactNode;
}

export function SidebarShell({
  section,
  title,
  children,
  headerAction,
}: SidebarShellProps) {
  const router = useRouter();
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const {
    conversations,
    currentId,
    switchConversation,
    deleteConversation,
    updateConversation,
    createConversation,
    searchQuery,
    setSearchQuery,
    isLoaded,
  } = useConversationHistoryContext();

  const handleNavigate = (target: SidebarSection) => {
    if (target === 'home') {
      router.push('/');
      setIsSidebarOpen(false);
      return;
    }

    router.push(`/${target}`);
    setIsSidebarOpen(false);
  };

  const handleNewChat = async () => {
    const createdId = await createConversation();
    setIsSidebarOpen(false);
    if (!createdId) {
      router.push('/');
    }
  };

  return (
    <div className="flex h-screen bg-[var(--color-bg-tertiary)] overflow-hidden">
      {isSidebarOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden transition-opacity"
          onClick={() => setIsSidebarOpen(false)}
        />
      )}

      <div
        className={`
          fixed inset-y-0 left-0 z-50 w-[260px] bg-[var(--color-bg-secondary)] transform transition-transform duration-300
          md:relative md:inset-auto md:z-0 md:w-auto md:translate-x-0
          ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}
        `}
      >
        <ConversationList
          conversations={conversations}
          currentId={currentId}
          onSelect={(id) => {
            switchConversation(id);
            setIsSidebarOpen(false);
          }}
          onDelete={deleteConversation}
          onUpdate={updateConversation}
          onNewChat={handleNewChat}
          searchQuery={searchQuery}
          setSearchQuery={setSearchQuery}
          isLoading={!isLoaded}
          activeSection={section}
          onNavigate={handleNavigate}
          onGoHome={() => handleNavigate('home')}
        />
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <MobileHeader
          title={title}
          onOpenSidebar={() => setIsSidebarOpen(true)}
        >
          {headerAction}
        </MobileHeader>

        <header className="hidden md:flex bg-[var(--color-bg-secondary)] border-b border-[var(--color-border-primary)] px-6 py-4 items-center justify-between">
          <h1 className="text-3xl font-semibold tracking-tight text-[var(--color-text-primary)]">
            {title}
          </h1>
          {headerAction}
        </header>

        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
