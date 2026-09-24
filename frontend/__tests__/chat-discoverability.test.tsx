import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WelcomeScreen } from '../components/WelcomeScreen';
import { ChatHeaderActions } from '../components/ChatHeaderActions';
import { ChatActivityStatus } from '../components/ChatActivityStatus';
import { SettingsNav } from '../components/settings/SettingsNav';
import { IconButton } from '../components/ui/IconButton';
import type { ChatEvent } from '../lib/events';

const { push, location } = vi.hoisted(() => ({
  push: vi.fn(),
  location: { pathname: '/settings/profile', search: 'from=conversation-1' },
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  usePathname: () => location.pathname,
  useSearchParams: () => new URLSearchParams(location.search),
}));

describe('chat discoverability', () => {
  beforeEach(() => {
    push.mockClear();
    location.pathname = '/settings/profile';
    location.search = 'from=conversation-1';
  });

  it('starts Council directly while other welcome shortcuts only prepare a draft', () => {
    const setInput = vi.fn();
    const onDeliberate = vi.fn();
    render(<WelcomeScreen setInput={setInput} onDeliberate={onDeliberate} />);
    expect(screen.getAllByRole('button')).toHaveLength(5);
    fireEvent.click(screen.getByRole('button', { name: /Deliberate/ }));
    expect(onDeliberate).toHaveBeenCalledTimes(1);
    expect(setInput).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Research' }));
    expect(setInput).toHaveBeenCalledWith('I need to research...');
    expect(onDeliberate).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['conversation & 1', '/settings/profile?from=conversation%20%26%201'],
    [null, '/settings/profile'],
  ])('opens settings from conversation %s', (conversationId, expected) => {
    render(<ChatHeaderActions conversationId={conversationId} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }));
    expect(push).toHaveBeenCalledWith(expected);
  });

  it('preserves the return conversation across all settings sections and marks the current page', () => {
    location.pathname = '/settings/voice';
    location.search = 'from=conversation%20%26%201';
    render(<SettingsNav />);
    const nav = screen.getByRole('navigation', { name: 'Settings' });
    const links = within(nav).getAllByRole('link');
    expect(links).toHaveLength(6);
    for (const link of links) {
      expect(link.getAttribute('href')).toContain(
        '?from=conversation%20%26%201',
      );
    }
    expect(within(nav).getByRole('link', { current: 'page' }).textContent).toBe(
      'Voice',
    );
  });

  it('shows routing and active agents, excludes terminal agents, and clears when idle', () => {
    const events: ChatEvent[] = [
      { type: 'routing', model: 'provider/current-model' },
      {
        type: 'agent_spawn',
        agent: 'a',
        agentType: 'research',
        task: 'Sources',
      },
      {
        type: 'agent_spawn',
        agent: 'b',
        agentType: 'research',
        task: 'Review',
      },
      {
        type: 'agent_spawn',
        agent: 'c',
        agentType: 'research',
        task: 'Compare',
      },
      { type: 'agent_complete', agent: 'b', result: 'Done' },
      { type: 'agent_status', agent: 'c', status: 'error' },
    ];
    const { rerender } = render(
      <ChatActivityStatus events={events} isLoading />,
    );
    expect(screen.getByRole('status').textContent).toContain('current-model');
    expect(screen.getByRole('status').textContent).toContain(
      'orchestrating · 1 active',
    );
    rerender(<ChatActivityStatus events={[]} isLoading />);
    expect(screen.getByRole('status').textContent).toContain('Choosing model…');
    expect(screen.getByRole('status').textContent).not.toContain(
      'current-model',
    );
    rerender(<ChatActivityStatus events={events} isLoading={false} />);
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('uses a named non-submit icon button and respects disabled state', () => {
    const onClick = vi.fn();
    render(
      <IconButton aria-label="Test action" disabled onClick={onClick}>
        +
      </IconButton>,
    );
    const button = screen.getByRole('button', { name: 'Test action' });
    expect(button.getAttribute('type')).toBe('button');
    fireEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });
});
