'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  SkeletonLine,
  SkeletonBlock,
  SkeletonCircle,
} from '@/components/ui/Skeleton';
import {
  Smartphone,
  Monitor,
  Tablet,
  Laptop,
  Trash2,
  AlertCircle,
  Loader2,
  Plus,
  RefreshCw,
  AlertTriangle,
} from 'lucide-react';
import { listDevices, revokeDevice, clearAuthState } from '@/lib/auth';
import { formatRelativeTime } from '@/lib/format';
import EnrollmentModal from '@/components/settings/EnrollmentModal';

interface Device {
  id: string;
  device_name: string;
  client_kind: string;
  created_at: string;
  last_seen_at: string | null;
  current: boolean;
  revoked: boolean;
}

type LoadStatus = 'idle' | 'loading' | 'success' | 'error';

function getDeviceIcon(clientKind: string) {
  const kind = clientKind.toLowerCase();
  if (kind.includes('desktop') || kind.includes('web')) return Monitor;
  if (kind.includes('tablet') || kind.includes('ipad')) return Tablet;
  if (kind.includes('laptop') || kind.includes('macbook')) return Laptop;
  return Smartphone;
}

function formatDevicePlatform(clientKind: string): string {
  const kind = clientKind.toLowerCase();
  if (kind === 'web') return 'Web browser';
  if (kind === 'ios') return 'iOS';
  if (kind === 'android') return 'Android';
  if (kind === 'native') return 'Native app';
  if (kind === 'desktop') return 'Desktop';
  if (kind === 'macos') return 'macOS';
  if (kind === 'windows') return 'Windows';
  if (kind === 'linux') return 'Linux';
  if (kind === 'chromeos') return 'Chrome OS';
  return clientKind.charAt(0).toUpperCase() + clientKind.slice(1);
}

function inferSessionLabel(deviceName: string): string | null {
  const name = deviceName.toLowerCase();
  if (
    name.includes('temporary') ||
    name.includes('public') ||
    name.includes('guest')
  ) {
    return 'Temporary session';
  }
  if (name.includes('mobile') || name.includes('phone')) {
    return 'Mobile';
  }
  return null;
}

export default function DevicesTab() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [loadStatus, setLoadStatus] = useState<LoadStatus>('loading');
  const [errorMessage, setErrorMessage] = useState('');

  const [revokingId, setRevokingId] = useState<string | null>(null);
  const [confirmDevice, setConfirmDevice] = useState<Device | null>(null);
  const [showEnrollment, setShowEnrollment] = useState(false);

  const fetchDevices = useCallback(async () => {
    setLoadStatus('loading');
    setErrorMessage('');
    try {
      const result = await listDevices();
      if (result.success && result.devices) {
        setDevices(result.devices.filter((d) => !d.revoked));
        setLoadStatus('success');
      } else {
        setErrorMessage(result.error || 'Failed to load devices');
        setLoadStatus('error');
      }
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to load devices',
      );
      setLoadStatus('error');
    }
  }, []);

  useEffect(() => {
    fetchDevices();
  }, [fetchDevices]);

  const handleRevoke = async (device: Device) => {
    setRevokingId(device.id);
    try {
      const result = await revokeDevice(device.id);
      if (result.success) {
        if (device.current) {
          clearAuthState();
          if (typeof window !== 'undefined') {
            window.location.replace('/setup');
          }
          return;
        }
        setDevices((prev) => prev.filter((d) => d.id !== device.id));
      } else {
        setErrorMessage(result.error || 'Failed to revoke device');
      }
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to revoke device',
      );
    } finally {
      setRevokingId(null);
      setConfirmDevice(null);
    }
  };

  if (loadStatus === 'loading' && devices.length === 0) {
    return (
      <div className="animate-fade-in">
        <div className="flex items-center gap-3 mb-6">
          <SkeletonCircle size={40} />
          <div className="space-y-2">
            <SkeletonLine width={128} height={20} />
            <SkeletonLine width={192} height={16} />
          </div>
        </div>

        <div className="space-y-4">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="flex items-center gap-4 p-4 rounded-lg border border-border-primary bg-bg-secondary"
            >
              <SkeletonCircle size={40} />
              <div className="flex-1 space-y-2">
                <SkeletonLine width={160} height={16} />
                <SkeletonLine width={240} height={14} />
              </div>
              <SkeletonBlock width={80} height={36} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in">
      <div className="flex items-center gap-3 mb-6 pb-6 border-b border-border-primary">
        <div className="w-10 h-10 rounded-full bg-accent-subtle flex items-center justify-center">
          <Smartphone className="w-5 h-5 text-accent-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-text-primary">Devices</h2>
          <p className="text-sm text-text-muted">
            Manage your signed-in devices and sessions. You can sign in on as
            many devices as you need.
          </p>
        </div>
      </div>

      {loadStatus === 'error' && (
        <div className="mb-6 p-4 rounded-lg bg-status-error-bg border border-status-error/20 flex items-start gap-3 animate-slide-up">
          <AlertCircle className="w-5 h-5 text-status-error flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-status-error">Error</p>
            <p className="text-sm text-text-secondary">{errorMessage}</p>
          </div>
          <button
            type="button"
            onClick={fetchDevices}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-text-secondary hover:text-text-primary hover:bg-bg-tertiary rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus/50"
          >
            <RefreshCw className="w-4 h-4" />
            Retry
          </button>
        </div>
      )}

      <div className="space-y-4">
        {devices.length === 0 && loadStatus === 'success' ? (
          <div className="text-center py-12 rounded-lg border border-border-primary bg-bg-secondary">
            <Smartphone className="w-8 h-8 text-text-muted mx-auto mb-3" />
            <p className="text-sm text-text-muted">No active sessions found</p>
            <p className="text-xs text-text-muted mt-1">
              Sign in on this or another device to get started
            </p>
          </div>
        ) : (
          devices.map((device) => {
            const DeviceIcon = getDeviceIcon(device.client_kind);
            return (
              <div
                key={device.id}
                className={`flex items-center gap-4 p-4 rounded-lg border transition-colors ${
                  device.current
                    ? 'border-accent-primary/30 bg-accent-subtle/30'
                    : 'border-border-primary bg-bg-secondary'
                }`}
              >
                <div className="w-10 h-10 rounded-full bg-bg-tertiary flex items-center justify-center flex-shrink-0">
                  <DeviceIcon className="w-5 h-5 text-text-secondary" />
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-text-primary truncate">
                      {device.device_name || 'Unnamed device'}
                    </span>
                    {device.current && (
                      <span className="inline-flex items-center px-2 py-0.5 text-xs font-medium bg-accent-primary/10 text-accent-primary rounded-full">
                        Current
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-text-muted mt-0.5 flex-wrap">
                    <span>{formatDevicePlatform(device.client_kind)}</span>
                    {inferSessionLabel(device.device_name) && (
                      <>
                        <span>·</span>
                        <span>{inferSessionLabel(device.device_name)}</span>
                      </>
                    )}
                    <span>·</span>
                    <span>Added {formatRelativeTime(device.created_at)}</span>
                    {device.last_seen_at ? (
                      <>
                        <span>·</span>
                        <span>
                          Last seen {formatRelativeTime(device.last_seen_at)}
                        </span>
                      </>
                    ) : (
                      <>
                        <span>·</span>
                        <span>Not seen yet</span>
                      </>
                    )}
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => setConfirmDevice(device)}
                  disabled={revokingId === device.id}
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-status-error hover:bg-status-error-bg border border-status-error/30 rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-status-error/50"
                >
                  {revokingId === device.id ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Trash2 className="w-4 h-4" />
                  )}
                  <span className="hidden sm:inline">Revoke</span>
                </button>
              </div>
            );
          })
        )}
      </div>

      <div className="mt-8 pt-6 border-t border-border-primary">
        <button
          type="button"
          onClick={() => setShowEnrollment(true)}
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-accent-primary hover:bg-accent-hover active:bg-accent-active text-white font-medium rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-accent-primary/50"
        >
          <Plus className="w-4 h-4" />
          <span>Add new device</span>
        </button>
      </div>

      {confirmDevice && (
        <div className="fixed inset-0 z-modal flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-bg-overlay"
            onClick={() => setConfirmDevice(null)}
          />
          <div className="relative w-full max-w-md bg-bg-secondary rounded-xl border border-border-primary shadow-xl animate-scale">
            <div className="p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-full bg-status-error-bg flex items-center justify-center">
                  <AlertTriangle className="w-5 h-5 text-status-error" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-text-primary">
                    Revoke device?
                  </h3>
                </div>
              </div>

              <p className="text-sm text-text-secondary mb-6">
                {confirmDevice.current
                  ? 'This will sign you out of this browser. You can sign in again at any time.'
                  : `This will sign out that session and remove its access. The user can sign in again to regain access.`}
              </p>

              <div className="flex items-center justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setConfirmDevice(null)}
                  className="px-4 py-2 text-sm font-medium text-text-secondary hover:text-text-primary hover:bg-bg-tertiary rounded-md transition-colors focus:outline-none focus:ring-2 focus:ring-border-focus/50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => handleRevoke(confirmDevice)}
                  disabled={revokingId === confirmDevice.id}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-status-error text-white font-medium rounded-md hover:bg-status-error/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-status-error/50"
                >
                  {revokingId === confirmDevice.id ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>Revoking...</span>
                    </>
                  ) : (
                    <>
                      <Trash2 className="w-4 h-4" />
                      <span>Revoke</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <EnrollmentModal
        isOpen={showEnrollment}
        onClose={() => setShowEnrollment(false)}
      />
    </div>
  );
}
