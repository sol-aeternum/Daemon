import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  render,
  screen,
  fireEvent,
  waitFor,
  act,
} from '@testing-library/react';

const mockListDevices = vi.fn();
const mockRevokeDevice = vi.fn();
const mockClearAuthState = vi.fn();

vi.mock('@/lib/auth', () => ({
  listDevices: (...args: unknown[]) => mockListDevices(...args),
  revokeDevice: (...args: unknown[]) => mockRevokeDevice(...args),
  clearAuthState: () => mockClearAuthState(),
}));

vi.mock('@/lib/format', () => ({
  formatRelativeTime: (date: string) => `relative(${date})`,
}));

vi.mock('@/components/settings/EnrollmentModal', () => ({
  default: function MockEnrollmentModal({ isOpen }: { isOpen: boolean }) {
    return isOpen ? (
      <div data-testid="enrollment-modal">EnrollmentModal</div>
    ) : null;
  },
}));

import DevicesTab from '@/components/settings/DevicesTab';

beforeEach(() => {
  vi.clearAllMocks();
  mockListDevices.mockResolvedValue({ success: true, devices: [] });
});

function mockWindowLocation() {
  const replace = vi.fn();
  const originalLocation = window.location;
  const locationSpy = vi.spyOn(window, 'location', 'get').mockReturnValue({
    ...originalLocation,
    replace,
  } as unknown as Location);
  return { replace, locationSpy };
}

function getConfirmRevokeButton() {
  const buttons = screen.getAllByRole('button', { name: /^revoke$/i });
  return buttons[buttons.length - 1];
}

describe('DevicesTab', () => {
  it('shows loading skeletons while fetching', async () => {
    mockListDevices.mockImplementation(() => new Promise(() => {}));
    render(<DevicesTab />);

    expect(
      document.querySelectorAll('.animate-fade-in').length,
    ).toBeGreaterThan(0);
    expect(document.querySelectorAll('.skeleton').length).toBeGreaterThan(0);
  });

  it('shows empty state with identity-friendly copy', async () => {
    mockListDevices.mockResolvedValue({ success: true, devices: [] });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText(/no active sessions found/i)).toBeTruthy();
    });
    expect(
      screen.getByText(/sign in on this or another device to get started/i),
    ).toBeTruthy();
  });

  it('shows error state with retry button', async () => {
    mockListDevices.mockResolvedValue({
      success: false,
      error: 'Network error',
    });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText(/network error/i)).toBeTruthy();
    });
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy();
  });

  it('renders multiple devices with correct labels and metadata', async () => {
    const devices = [
      {
        id: 'dev-web-1',
        device_name: 'Chrome on macOS',
        client_kind: 'web',
        created_at: '2026-06-01T10:00:00Z',
        last_seen_at: '2026-06-05T08:00:00Z',
        current: true,
        revoked: false,
      },
      {
        id: 'dev-ios-1',
        device_name: 'iPhone Safari',
        client_kind: 'ios',
        created_at: '2026-06-03T12:00:00Z',
        last_seen_at: null,
        current: false,
        revoked: false,
      },
      {
        id: 'dev-android-1',
        device_name: 'Pixel 7',
        client_kind: 'android',
        created_at: '2026-06-04T09:00:00Z',
        last_seen_at: '2026-06-04T09:30:00Z',
        current: false,
        revoked: false,
      },
    ];
    mockListDevices.mockResolvedValue({ success: true, devices });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText('Chrome on macOS')).toBeTruthy();
    });

    expect(screen.getByText('Current')).toBeTruthy();
    expect(screen.getByText('Web browser')).toBeTruthy();
    expect(screen.getByText('iOS')).toBeTruthy();
    expect(screen.getByText('Android')).toBeTruthy();
    expect(screen.getAllByText(/last seen/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/not seen yet/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/added relative/i).length).toBe(3);
  });

  it('shows current device with highlight styling', async () => {
    const devices = [
      {
        id: 'dev-current',
        device_name: 'Current Browser',
        client_kind: 'web',
        created_at: '2026-06-01T10:00:00Z',
        last_seen_at: '2026-06-05T08:00:00Z',
        current: true,
        revoked: false,
      },
    ];
    mockListDevices.mockResolvedValue({ success: true, devices });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText('Current Browser')).toBeTruthy();
    });

    expect(screen.getByText('Current')).toBeTruthy();
  });

  it('opens revoke modal with current-device copy', async () => {
    const devices = [
      {
        id: 'dev-current',
        device_name: 'Current Browser',
        client_kind: 'web',
        created_at: '2026-06-01T10:00:00Z',
        last_seen_at: '2026-06-05T08:00:00Z',
        current: true,
        revoked: false,
      },
    ];
    mockListDevices.mockResolvedValue({ success: true, devices });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText('Current Browser')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /revoke/i }));

    await waitFor(() => {
      expect(screen.getByText(/revoke device/i)).toBeTruthy();
    });

    expect(
      screen.getByText(
        /this will sign you out of this browser\. you can sign in again at any time\./i,
      ),
    ).toBeTruthy();
  });

  it('opens revoke modal with other-device copy', async () => {
    const devices = [
      {
        id: 'dev-other',
        device_name: 'Other Device',
        client_kind: 'android',
        created_at: '2026-06-01T10:00:00Z',
        last_seen_at: '2026-06-05T08:00:00Z',
        current: false,
        revoked: false,
      },
    ];
    mockListDevices.mockResolvedValue({ success: true, devices });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText('Other Device')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /revoke/i }));

    await waitFor(() => {
      expect(screen.getByText(/revoke device/i)).toBeTruthy();
    });

    expect(
      screen.getByText(
        /this will sign out that session and remove its access\. the user can sign in again to regain access\./i,
      ),
    ).toBeTruthy();
  });

  it('revoking current device clears auth and redirects to /setup', async () => {
    const { replace, locationSpy } = mockWindowLocation();

    const devices = [
      {
        id: 'dev-current',
        device_name: 'Current Browser',
        client_kind: 'web',
        created_at: '2026-06-01T10:00:00Z',
        last_seen_at: '2026-06-05T08:00:00Z',
        current: true,
        revoked: false,
      },
    ];
    mockListDevices.mockResolvedValue({ success: true, devices });
    mockRevokeDevice.mockResolvedValue({ success: true });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText('Current Browser')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /revoke/i }));

    await waitFor(() => {
      expect(screen.getByText(/revoke device/i)).toBeTruthy();
    });

    const confirmRevoke = getConfirmRevokeButton();
    await act(async () => {
      fireEvent.click(confirmRevoke);
    });

    await waitFor(() => {
      expect(mockRevokeDevice).toHaveBeenCalledWith('dev-current');
    });
    expect(mockClearAuthState).toHaveBeenCalled();
    expect(replace).toHaveBeenCalledWith('/setup');

    locationSpy.mockRestore();
  });

  it('revoking other device removes it from the list', async () => {
    const devices = [
      {
        id: 'dev-other',
        device_name: 'Other Device',
        client_kind: 'android',
        created_at: '2026-06-01T10:00:00Z',
        last_seen_at: '2026-06-05T08:00:00Z',
        current: false,
        revoked: false,
      },
    ];
    mockListDevices.mockResolvedValue({ success: true, devices });
    mockRevokeDevice.mockResolvedValue({ success: true });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText('Other Device')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /revoke/i }));

    await waitFor(() => {
      expect(screen.getByText(/revoke device/i)).toBeTruthy();
    });

    const confirmRevoke = getConfirmRevokeButton();
    await act(async () => {
      fireEvent.click(confirmRevoke);
    });

    await waitFor(() => {
      expect(mockRevokeDevice).toHaveBeenCalledWith('dev-other');
    });
    expect(mockClearAuthState).not.toHaveBeenCalled();

    await waitFor(() => {
      expect(screen.queryByText('Other Device')).toBeNull();
    });
  });

  it('keeps device in list when revoke fails', async () => {
    const devices = [
      {
        id: 'dev-fail',
        device_name: 'Failing Device',
        client_kind: 'web',
        created_at: '2026-06-01T10:00:00Z',
        last_seen_at: '2026-06-05T08:00:00Z',
        current: false,
        revoked: false,
      },
    ];
    mockListDevices.mockResolvedValue({ success: true, devices });
    mockRevokeDevice.mockResolvedValue({
      success: false,
      error: 'Server error',
    });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText('Failing Device')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /revoke/i }));

    await waitFor(() => {
      expect(screen.getByText(/revoke device/i)).toBeTruthy();
    });

    const confirmRevoke = getConfirmRevokeButton();
    await act(async () => {
      fireEvent.click(confirmRevoke);
    });

    await waitFor(() => {
      expect(mockRevokeDevice).toHaveBeenCalledWith('dev-fail');
    });

    await waitFor(() => {
      expect(screen.queryByText('Failing Device')).toBeTruthy();
    });
  });

  it('does not show revoked devices', async () => {
    const devices = [
      {
        id: 'dev-active',
        device_name: 'Active Device',
        client_kind: 'web',
        created_at: '2026-06-01T10:00:00Z',
        last_seen_at: '2026-06-05T08:00:00Z',
        current: false,
        revoked: false,
      },
      {
        id: 'dev-revoked',
        device_name: 'Revoked Device',
        client_kind: 'ios',
        created_at: '2026-05-01T10:00:00Z',
        last_seen_at: '2026-05-02T08:00:00Z',
        current: false,
        revoked: true,
      },
    ];
    mockListDevices.mockResolvedValue({ success: true, devices });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText('Active Device')).toBeTruthy();
    });

    expect(screen.queryByText('Revoked Device')).toBeNull();
  });

  it('subtitle conveys unlimited sessions without a cap', async () => {
    mockListDevices.mockResolvedValue({ success: true, devices: [] });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText(/no active sessions found/i)).toBeTruthy();
    });

    const subtitle = screen.getByText(
      /manage your signed-in devices and sessions/i,
    );
    expect(subtitle).toBeTruthy();
    expect(subtitle.textContent).toMatch(/as many devices as you need/i);
  });

  it('shows enrollment modal when Add new device is clicked', async () => {
    mockListDevices.mockResolvedValue({ success: true, devices: [] });
    render(<DevicesTab />);

    await waitFor(() => {
      expect(screen.getByText(/no active sessions found/i)).toBeTruthy();
    });

    const addButton = screen.getByRole('button', { name: /add new device/i });
    fireEvent.click(addButton);

    await waitFor(() => {
      expect(screen.getByTestId('enrollment-modal')).toBeTruthy();
    });
  });
});
