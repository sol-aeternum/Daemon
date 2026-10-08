'use client';

// Reload persistence for unsent chat drafts.
// Text lives in this tab's sessionStorage, so it survives reload but not tab
// close. Attachment files live in IndexedDB (sessionStorage cannot hold them),
// referenced only from this tab's text record and expired after a day.
// A shared, nonsecret auth epoch in localStorage changes on every sign-in and
// sign-out; a record saved under another epoch is discarded instead of being
// restored, so a reload never resurrects another sign-in's draft.

const TEXT_KEY = 'daemon:chat-drafts:v1';
const EPOCH_KEY = 'daemon:chat-drafts:auth-epoch';
const DB_NAME = 'daemon-chat-drafts';
const STORE = 'attachments';

export const ATTACHMENT_TTL_MS = 24 * 60 * 60 * 1000;
export const MAX_PERSISTED_FILE_BYTES = 25 * 1024 * 1024;
export const MAX_PERSISTED_TOTAL_BYTES = 100 * 1024 * 1024;

export interface PersistedDraft {
  conversationId: string | null;
  input: string;
  attachmentIds: string[];
  /** The held submission this draft was restored from, if unedited. */
  restoredKey?: string;
}

/** A sent draft held until its outcome is known (see lib/chatDrafts). */
export interface PersistedHeld {
  key: string;
  conversationId: string | null;
  input: string;
  attachmentIds: string[];
  /** How many files the submission had (some may not have been persistable). */
  attachmentCount?: number;
}

export interface PersistedAttachment {
  id: string;
  file: File;
}

export interface AttachmentStore {
  put(attachment: PersistedAttachment): Promise<void>;
  getMany(ids: string[]): Promise<PersistedAttachment[]>;
  delete(ids: string[]): Promise<void>;
  clear(): Promise<void>;
  deleteSavedBefore(cutoffMs: number): Promise<void>;
}

interface StoredRecord {
  id: string;
  blob: Blob;
  name: string;
  type: string;
  lastModified: number;
  savedAt: number;
}

function session(): Storage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

function local(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

function readAuthEpoch(): string {
  try {
    return local()?.getItem(EPOCH_KEY) ?? '';
  } catch {
    return '';
  }
}

function newEpoch(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function isPersistedDraft(value: unknown): value is PersistedDraft {
  if (!value || typeof value !== 'object') return false;
  const draft = value as Record<string, unknown>;
  return (
    (draft.conversationId === null ||
      (typeof draft.conversationId === 'string' &&
        draft.conversationId.length > 0)) &&
    typeof draft.input === 'string' &&
    Array.isArray(draft.attachmentIds) &&
    draft.attachmentIds.every((id) => typeof id === 'string' && id.length > 0)
  );
}

function isPersistedHeld(value: unknown): value is PersistedHeld {
  if (!value || typeof value !== 'object') return false;
  const held = value as Record<string, unknown>;
  return (
    typeof held.key === 'string' &&
    held.key.length > 0 &&
    isPersistedDraft({
      conversationId: held.conversationId,
      input: held.input,
      attachmentIds: held.attachmentIds,
    })
  );
}

function loadRecord(): { drafts: PersistedDraft[]; held: PersistedHeld[] } {
  const storage = session();
  if (!storage) return { drafts: [], held: [] };
  try {
    const raw = storage.getItem(TEXT_KEY);
    if (!raw) return { drafts: [], held: [] };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    const held = parsed?.held ?? [];
    if (
      parsed?.v !== 1 ||
      parsed.epoch !== readAuthEpoch() ||
      !Array.isArray(parsed.drafts) ||
      !parsed.drafts.every(isPersistedDraft) ||
      !Array.isArray(held) ||
      !held.every(isPersistedHeld)
    ) {
      storage.removeItem(TEXT_KEY);
      return { drafts: [], held: [] };
    }
    return { drafts: parsed.drafts, held };
  } catch {
    clearDraftText();
    return { drafts: [], held: [] };
  }
}

/** This tab's saved drafts, or none if they belong to another sign-in. */
export function loadDraftText(): PersistedDraft[] {
  return loadRecord().drafts;
}

/** This tab's held submissions, under the same rules as its drafts. */
export function loadHeldSubmissions(): PersistedHeld[] {
  return loadRecord().held;
}

export function saveDraftText(
  drafts: PersistedDraft[],
  held: PersistedHeld[] = [],
): void {
  const storage = session();
  if (!storage) return;
  try {
    if (drafts.length === 0 && held.length === 0) {
      storage.removeItem(TEXT_KEY);
      return;
    }
    storage.setItem(
      TEXT_KEY,
      JSON.stringify({ v: 1, epoch: readAuthEpoch(), drafts, held }),
    );
  } catch {
    // Quota or disabled storage: the in-memory draft still works.
  }
}

export function clearDraftText(): void {
  try {
    session()?.removeItem(TEXT_KEY);
  } catch {
    return;
  }
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export function createIndexedDbAttachmentStore(): AttachmentStore | null {
  if (typeof indexedDB === 'undefined') return null;
  let db: Promise<IDBDatabase> | null = null;
  const open = () => {
    db ??= new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const store = req.result.createObjectStore(STORE, { keyPath: 'id' });
        store.createIndex('savedAt', 'savedAt');
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }).catch((error: unknown) => {
      db = null;
      throw error;
    });
    return db;
  };
  const write = async (fn: (store: IDBObjectStore) => void) => {
    const tx = (await open()).transaction(STORE, 'readwrite');
    fn(tx.objectStore(STORE));
    await done(tx);
  };
  return {
    put: ({ id, file }) =>
      write((store) => {
        const record: StoredRecord = {
          id,
          blob: file,
          name: file.name,
          type: file.type,
          lastModified: file.lastModified,
          savedAt: Date.now(),
        };
        store.put(record);
      }),
    async getMany(ids) {
      const store = (await open())
        .transaction(STORE, 'readonly')
        .objectStore(STORE);
      const records = await Promise.all(
        ids.map((id) => request(store.get(id) as IDBRequest<StoredRecord>)),
      );
      return records
        .filter((record): record is StoredRecord => Boolean(record))
        .map((record) => ({
          id: record.id,
          file: new File([record.blob], record.name, {
            type: record.type,
            lastModified: record.lastModified,
          }),
        }));
    },
    delete: (ids) =>
      write((store) => {
        for (const id of ids) store.delete(id);
      }),
    clear: () => write((store) => store.clear()),
    deleteSavedBefore: (cutoffMs) =>
      write((store) => {
        const cursor = store
          .index('savedAt')
          .openCursor(IDBKeyRange.upperBound(cutoffMs, true));
        cursor.onsuccess = () => {
          const current = cursor.result;
          if (!current) return;
          current.delete();
          current.continue();
        };
      }),
  };
}

let attachmentStore: AttachmentStore | null | undefined;

export function getAttachmentStore(): AttachmentStore | null {
  if (attachmentStore === undefined) {
    attachmentStore = createIndexedDbAttachmentStore();
  }
  return attachmentStore;
}

/** Test seam: replace the IndexedDB adapter (null disables attachments). */
export function setAttachmentStoreForTests(
  store: AttachmentStore | null | undefined,
): void {
  attachmentStore = store;
}

/**
 * Sign-in or sign-out in this tab: no saved draft from before may be restored
 * by any tab of this browser, including tabs reopened by session restore.
 */
export function discardAllPersistedDrafts(): void {
  try {
    local()?.setItem(EPOCH_KEY, newEpoch());
  } catch {
    // Without localStorage the text record still clears below.
  }
  clearDraftText();
  void getAttachmentStore()
    ?.clear()
    .catch(() => undefined);
}
