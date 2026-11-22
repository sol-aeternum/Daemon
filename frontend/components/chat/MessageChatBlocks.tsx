'use client';

import { useMemo } from 'react';
import type { ChatEvent } from '@/lib/events';
import { useChatBlocks } from '@/lib/chatBlocks';
import { ChatBlockCard } from './ChatBlockView';

/** Chat blocks returned by one assistant message, in call order. */
export function MessageChatBlocks({
  events,
  onOpen,
}: {
  events: ChatEvent[];
  onOpen: (blockKey: string) => void;
}) {
  const groups = useMemo(() => [events], [events]);
  const blocks = useChatBlocks(groups);
  if (blocks.length === 0) return null;
  return (
    <>
      {blocks.map((block) => (
        <ChatBlockCard
          key={block.key}
          block={block}
          onOpen={() => onOpen(block.key)}
        />
      ))}
    </>
  );
}
