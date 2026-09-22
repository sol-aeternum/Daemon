'use client';

import { Settings } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { IconButton } from './ui/IconButton';

export function ChatHeaderActions({
  conversationId,
}: {
  conversationId: string | null;
}) {
  const router = useRouter();
  const href = conversationId
    ? `/settings/profile?from=${encodeURIComponent(conversationId)}`
    : '/settings/profile';

  return (
    <IconButton
      aria-label="Open settings"
      title="Settings"
      onClick={() => router.push(href)}
    >
      <Settings className="h-5 w-5" aria-hidden="true" />
    </IconButton>
  );
}
