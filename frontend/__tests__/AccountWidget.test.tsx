import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const mockLogout = vi.fn();
const mockPush = vi.fn();

vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: mockPush,
    replace: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => '/',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('next-themes', () => ({
  useTheme: () => ({
    theme: 'system',
    setTheme: vi.fn(),
    themes: ['light', 'dark', 'system'],
    resolvedTheme: 'system',
    systemTheme: 'light',
  }),
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
}));

vi.mock('@/components/AuthProvider', () => ({
  useAuth: () => ({
    isAuthenticated: true,
    accessToken: 'token',
    authHeader: 'Bearer token',
    refreshAuth: vi.fn(),
    logout: mockLogout,
    setAccessToken: vi.fn(),
  }),
}));

import { AccountWidget } from '@/components/AccountWidget';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AccountWidget logout', () => {
  it('calls useAuth().logout when the Log out button is clicked', async () => {
    render(<AccountWidget displayName="Test User" tier="Pro" />);

    fireEvent.click(screen.getByRole('button', { name: /test user/i }));

    const logoutButton = await screen.findByRole('button', {
      name: /log out/i,
    });
    fireEvent.click(logoutButton);

    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalledTimes(1);
    });
  });

  it('closes the dropdown when Log out is clicked', async () => {
    render(<AccountWidget displayName="Test User" tier="Pro" />);

    fireEvent.click(screen.getByRole('button', { name: /test user/i }));
    const logoutButton = await screen.findByRole('button', {
      name: /log out/i,
    });
    fireEvent.click(logoutButton);

    await waitFor(() => {
      expect(mockLogout).toHaveBeenCalled();
    });
  });
});
