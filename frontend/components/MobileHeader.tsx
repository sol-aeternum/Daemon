import React from "react";

interface MobileHeaderProps {
  title: string;
  onOpenSidebar: () => void;
  children?: React.ReactNode;
}

export function MobileHeader({ title, onOpenSidebar, children }: MobileHeaderProps) {
  return (
    <header className="md:hidden bg-white border-b px-4 py-3 flex items-center gap-3 sticky top-0 z-20 pt-[max(0.75rem,env(safe-area-inset-top))]">
      <button
        onClick={onOpenSidebar}
        className="p-2 -ml-2 text-gray-600 hover:bg-gray-100 rounded-lg touch-manipulation min-h-[44px] min-w-[44px] flex items-center justify-center"
        aria-label="Open menu"
      >
        <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>
      <h1 className="text-lg font-semibold truncate flex-1">
        {title}
      </h1>
      {children}
    </header>
  );
}
