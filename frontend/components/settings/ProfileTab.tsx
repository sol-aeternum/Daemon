'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  SkeletonLine,
  SkeletonBlock,
  SkeletonCircle,
} from '@/components/ui/Skeleton';
import { User, Save, CheckCircle2, AlertCircle, Loader2 } from 'lucide-react';
import { ensureAuthHeader } from '@/lib/auth';

interface UserSettings {
  preferences?: {
    display_name?: string;
    custom_instructions?: string;
    personality?: string;
    characteristics?: {
      warmth?: string;
      enthusiasm?: string;
      emoji?: string;
      formatting?: string;
    };
  };
}

interface ProfileFormData {
  displayName: string;
  preferences: string;
}

type SaveStatus = 'idle' | 'loading' | 'success' | 'error';

export default function ProfileTab() {
  const [formData, setFormData] = useState<ProfileFormData>({
    displayName: '',
    preferences: '',
  });
  const [isLoading, setIsLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle');
  const [errorMessage, setErrorMessage] = useState('');

  const apiBaseUrl =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '');

  const getAuthHeaders = useCallback(async (): Promise<
    Record<string, string>
  > => {
    const header = await ensureAuthHeader();
    if (!header) return {};
    return { Authorization: header };
  }, []);

  const apiCandidates = useCallback(
    (path: string) => {
      const normalizedPath = path.startsWith('/') ? path : `/${path}`;
      const trimmedBase = apiBaseUrl.endsWith('/')
        ? apiBaseUrl.slice(0, -1)
        : apiBaseUrl;

      if (!trimmedBase) {
        return [normalizedPath];
      }

      return [`${trimmedBase}${normalizedPath}`, normalizedPath];
    },
    [apiBaseUrl],
  );

  const fetchWithFallback = useCallback(
    async (path: string, init: RequestInit = {}, timeoutMs = 12000) => {
      const candidates = apiCandidates(path);

      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        const controller = new AbortController();
        const timeoutId = setTimeout(() => {
          try {
            controller.abort(
              new DOMException('Settings request timed out', 'AbortError'),
            );
          } catch {
            controller.abort();
          }
        }, timeoutMs);

        try {
          const response = await fetch(candidate, {
            ...init,
            signal: controller.signal,
          });
          clearTimeout(timeoutId);

          if (response.status === 404 && index < candidates.length - 1) {
            continue;
          }

          return response;
        } catch (error) {
          clearTimeout(timeoutId);
          if (index === candidates.length - 1) {
            throw error;
          }
        }
      }

      throw new Error('Request failed');
    },
    [apiCandidates],
  );

  // Fetch user settings on mount
  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const response = await fetchWithFallback('/users/me/settings', {
          headers: await getAuthHeaders(),
        });

        if (!response.ok) {
          setErrorMessage(
            'Failed to load settings. Please check API connectivity.',
          );
          return;
        }

        const settings: UserSettings = await response.json();

        setFormData({
          displayName: settings.preferences?.display_name || '',
          preferences: settings.preferences?.custom_instructions || '',
        });
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          setErrorMessage('Settings request timed out. Please retry.');
        } else {
          setErrorMessage('Failed to load settings. Please try again.');
        }
      } finally {
        setIsLoading(false);
      }
    };

    fetchSettings();
  }, [fetchWithFallback, getAuthHeaders]);

  // Handle form submission
  const handleSubmit = async (e: { preventDefault: () => void }) => {
    e.preventDefault();
    setSaveStatus('loading');
    setErrorMessage('');

    try {
      const response = await fetchWithFallback(
        '/users/me/settings',
        {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            ...(await getAuthHeaders()),
          },
          body: JSON.stringify({
            preferences: {
              display_name: formData.displayName,
              custom_instructions: formData.preferences,
            },
          }),
        },
        12000,
      );

      if (!response.ok) {
        setSaveStatus('error');
        setErrorMessage(
          'Failed to save settings. Please verify API key and connectivity.',
        );
        return;
      }

      setSaveStatus('success');

      // Reset success status after 3 seconds
      setTimeout(() => {
        setSaveStatus('idle');
      }, 3000);
    } catch (error) {
      setSaveStatus('error');
      if (error instanceof DOMException && error.name === 'AbortError') {
        setErrorMessage('Save request timed out. Please retry.');
      } else {
        setErrorMessage('Failed to save settings. Please try again.');
      }
    }
  };

  // Loading skeleton
  if (isLoading) {
    return (
      <div className="animate-fade-in">
        <div className="flex items-center gap-3 mb-6">
          <SkeletonCircle size={40} />
          <div className="space-y-2">
            <SkeletonLine width={128} height={20} />
            <SkeletonLine width={192} height={16} />
          </div>
        </div>

        <div className="space-y-6">
          <div className="space-y-2">
            <SkeletonLine width={96} height={16} />
            <SkeletonBlock height={40} />
          </div>

          <div className="space-y-2">
            <SkeletonLine width={128} height={16} />
            <SkeletonBlock height={128} />
          </div>

          <SkeletonBlock width={128} height={40} />
        </div>
      </div>
    );
  }
  return (
    <form onSubmit={handleSubmit} className="animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6 pb-6 border-b border-border-primary">
        <div className="w-10 h-10 rounded-full bg-accent-subtle flex items-center justify-center">
          <User className="w-5 h-5 text-accent-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-text-primary">
            Profile Settings
          </h2>
          <p className="text-sm text-text-muted">
            Manage your display name and preferences
          </p>
        </div>
      </div>

      {/* Error Message */}
      {saveStatus === 'error' && (
        <div className="mb-6 p-4 rounded-lg bg-status-error-bg border border-status-error/20 flex items-start gap-3 animate-slide-up">
          <AlertCircle className="w-5 h-5 text-status-error flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-status-error">Save failed</p>
            <p className="text-sm text-text-secondary">{errorMessage}</p>
          </div>
        </div>
      )}

      {/* Success Message */}
      {saveStatus === 'success' && (
        <div className="mb-6 p-4 rounded-lg bg-status-success-bg border border-status-success/20 flex items-center gap-3 animate-slide-up">
          <CheckCircle2 className="w-5 h-5 text-status-success flex-shrink-0" />
          <p className="text-sm font-medium text-status-success">
            Settings saved successfully
          </p>
        </div>
      )}

      {/* Form Fields */}
      <div className="space-y-6">
        {/* Display Name Field */}
        <div className="space-y-2">
          <label
            htmlFor="displayName"
            className="block text-sm font-medium text-text-secondary"
          >
            Display Name
          </label>
          <input
            type="text"
            id="displayName"
            value={formData.displayName}
            onChange={(e) =>
              setFormData({ ...formData, displayName: e.target.value })
            }
            placeholder="Enter your display name"
            disabled={saveStatus === 'loading'}
            className="w-full px-3 py-2.5 bg-bg-input border border-border-primary rounded-md text-text-primary placeholder-text-muted focus:outline-none focus:border-border-focus focus:ring-1 focus:ring-border-focus transition-colors"
          />
          <p className="text-xs text-text-muted">
            This name will be used in conversations and across the app
          </p>
        </div>

        {/* Preferences Textarea */}
        <div className="space-y-2">
          <label
            htmlFor="preferences"
            className="block text-sm font-medium text-text-secondary"
          >
            Custom Instructions
          </label>
          <textarea
            id="preferences"
            value={formData.preferences}
            onChange={(e) =>
              setFormData({ ...formData, preferences: e.target.value })
            }
            placeholder="Describe how you'd like the AI to interact with you..."
            rows={6}
            disabled={saveStatus === 'loading'}
            className="w-full px-3 py-2.5 bg-bg-input border border-border-primary rounded-md text-text-primary placeholder-text-muted focus:outline-none focus:border-border-focus focus:ring-1 focus:ring-border-focus transition-colors resize-y"
          />
          <p className="text-xs text-text-muted">
            These instructions help personalize the AI&apos;s responses to your
            style and preferences
          </p>
        </div>
      </div>

      {/* Submit Button */}
      <div className="mt-8 pt-6 border-t border-border-primary">
        <button
          type="submit"
          disabled={saveStatus === 'loading'}
          className="inline-flex items-center gap-2 px-5 py-2.5 bg-accent-primary hover:bg-accent-hover active:bg-accent-active text-white font-medium rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-accent-primary/50"
        >
          {saveStatus === 'loading' ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Saving...</span>
            </>
          ) : (
            <>
              <Save className="w-4 h-4" />
              <span>Save Changes</span>
            </>
          )}
        </button>
      </div>
    </form>
  );
}
