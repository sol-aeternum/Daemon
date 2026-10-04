import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import {
  CircleQuestionMark,
  FileUp,
  MessageSquare,
  PenLine,
  Sparkles,
} from 'lucide-react';

import { MemoryCard } from '../components/settings/memory/MemoryCard';
import type { Memory } from '../hooks/useMemories';
import {
  getMemorySourcePresentation,
  memorySourceTitle,
} from '../lib/memorySource';

afterEach(cleanup);

function memory(sourceType: string): Memory {
  return {
    id: 'm-1',
    content: 'Prefers dark mode',
    category: 'preference',
    status: 'active',
    source_type: sourceType,
    conversation_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    confirmed: true,
  };
}

describe('memory source presentation', () => {
  it('maps every actually persisted source value to its own icon and label', () => {
    const cases: Array<[string, { icon: unknown; label: string }]> = [
      ['extracted', { icon: Sparkles, label: 'Extracted from conversations' }],
      ['manual', { icon: PenLine, label: 'Added by you (legacy)' }],
      ['user_created', { icon: PenLine, label: 'Added by you' }],
      ['import', { icon: FileUp, label: 'Imported' }],
      [
        'conversation',
        { icon: MessageSquare, label: 'From a conversation (legacy)' },
      ],
    ];
    for (const [sourceType, expected] of cases) {
      const presentation = getMemorySourcePresentation(sourceType);
      expect(presentation.icon).toBe(expected.icon);
      expect(presentation.label).toBe(expected.label);
      expect(presentation.recognized).toBe(true);
      expect(memorySourceTitle(sourceType)).toBe(`Source: ${expected.label}`);
    }
  });

  it('falls back to a neutral unrecognized icon for values the schema never writes', () => {
    for (const sourceType of [
      'tool',
      'bootstrapped',
      'constructor',
      'toString',
      '__proto__',
      '',
      undefined,
    ]) {
      const presentation = getMemorySourcePresentation(sourceType);
      expect(presentation.icon).toBe(CircleQuestionMark);
      expect(presentation.recognized).toBe(false);
    }
    expect(memorySourceTitle('tool')).toBe('Source: tool (unrecognized)');
  });

  it('renders the persisted provenance honestly on memory cards', () => {
    render(<MemoryCard memory={memory('user_created')} onSelect={() => {}} />);
    expect(screen.getByTitle('Source: Added by you')).toBeDefined();
  });

  it.each(['bootstrapped', 'constructor', 'toString', '__proto__'])(
    'renders unrecognized source %s without crashing',
    (source) => {
      render(<MemoryCard memory={memory(source)} onSelect={() => {}} />);
      const badge = screen.getByTitle(`Source: ${source} (unrecognized)`);
      // Neutral unknown icon, not the extracted sparkles.
      expect(badge.textContent).toBeFalsy();
      expect(badge.querySelector('svg')).toBeDefined();
    },
  );
});
