/**
 * Presentation for persisted memory provenance (`memories.source_type`).
 *
 * Status icons and labels must match what the backend actually stores. The
 * persisted, user-visible source values are `extracted`, `user_created`
 * (everything saved either from Settings or by Daemon's memory_write tool),
 * `import`, and the legacy values `manual` and `conversation` from earlier
 * schema generations. Anything else — including values that may exist only
 * in very old databases — falls back to a neutral, explicitly unrecognized
 * presentation instead of masquerading as a known source.
 */

import {
  CircleQuestionMark,
  FileUp,
  MessageSquare,
  PenLine,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';

export interface MemorySourcePresentation {
  icon: LucideIcon;
  /** Human description used as visible text and tooltip detail. */
  label: string;
  /** False for persisted values this schema generation never writes. */
  recognized: boolean;
}

const KNOWN_SOURCES: Record<string, MemorySourcePresentation> = {
  extracted: {
    icon: Sparkles,
    label: 'Extracted from conversations',
    recognized: true,
  },
  manual: {
    icon: PenLine,
    label: 'Added by you (legacy)',
    recognized: true,
  },
  user_created: {
    icon: PenLine,
    label: 'Added by you',
    recognized: true,
  },
  import: {
    icon: FileUp,
    label: 'Imported',
    recognized: true,
  },
  conversation: {
    icon: MessageSquare,
    label: 'From a conversation (legacy)',
    recognized: true,
  },
};

export function getMemorySourcePresentation(
  sourceType: string | null | undefined,
): MemorySourcePresentation {
  if (sourceType && Object.hasOwn(KNOWN_SOURCES, sourceType)) {
    const known = KNOWN_SOURCES[sourceType];
    if (known) return known;
  }
  return {
    icon: CircleQuestionMark,
    label: 'Unknown source',
    recognized: false,
  };
}

/** Badge tooltip: names the real provenance, flags unrecognized values. */
export function memorySourceTitle(
  sourceType: string | null | undefined,
): string {
  const presentation = getMemorySourcePresentation(sourceType);
  if (presentation.recognized) return `Source: ${presentation.label}`;
  return `Source: ${sourceType || 'unknown'} (unrecognized)`;
}
