const LEGACY_PII_STORAGE_KEYS = [
  'user_name',
  'daemon_user_id',
  'daemon_tier',
] as const;

/** Remove profile data left behind by frontend versions that used localStorage. */
export function clearLegacyPiiStorage(): void {
  if (typeof window === 'undefined') return;

  let storage: Storage;
  try {
    storage = window.localStorage;
  } catch {
    return;
  }

  for (const key of LEGACY_PII_STORAGE_KEYS) {
    try {
      storage.removeItem(key);
    } catch {
      // A blocked key must not prevent cleanup attempts for the others.
    }
  }
}
