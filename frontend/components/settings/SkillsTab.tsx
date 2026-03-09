'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';
import { AlertCircle, CheckCircle, FileCode2, Plus, Save, Trash2, Upload } from 'lucide-react';

type ActionStatus = 'idle' | 'loading' | 'success' | 'error';

interface SkillSummary {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  updated_at: string;
}

interface SkillDetail extends SkillSummary {
  content: string;
}

const EMPTY_DETAIL: SkillDetail = {
  id: '',
  name: '',
  description: '',
  content: '',
  enabled: true,
  updated_at: '',
};

export default function SkillsTab() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<SkillDetail>(EMPTY_DETAIL);
  const [isLoadingList, setIsLoadingList] = useState(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [status, setStatus] = useState<ActionStatus>('idle');
  const [message, setMessage] = useState('');
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  const apiBaseUrl =
    process.env.NEXT_PUBLIC_API_URL ||
    (process.env.NODE_ENV === 'development' ? 'http://localhost:8000' : '');

  const getAuthHeaders = useCallback(() => {
    const apiKey = typeof window !== 'undefined' ? localStorage.getItem('daemon_api_key') || '' : '';
    const headers: Record<string, string> = {};

    if (apiKey) {
      headers.Authorization = `Bearer ${apiKey}`;
    }

    return headers;
  }, []);

  const getJsonHeaders = useCallback(
    () => ({
      'Content-Type': 'application/json',
      ...getAuthHeaders(),
    }),
    [getAuthHeaders]
  );

  const apiCandidates = useCallback(
    (path: string) => {
      const normalizedPath = path.startsWith('/') ? path : `/${path}`;
      const trimmedBase = apiBaseUrl.endsWith('/') ? apiBaseUrl.slice(0, -1) : apiBaseUrl;

      if (!trimmedBase) {
        return [normalizedPath];
      }

      return [`${trimmedBase}${normalizedPath}`, normalizedPath];
    },
    [apiBaseUrl]
  );

  const fetchWithTimeout = useCallback(
    async (path: string, init: RequestInit = {}, timeoutMs = 12000) => {
      const candidates = apiCandidates(path);
      let lastError: unknown = null;

      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        const controller = new AbortController();
        const timeoutId = setTimeout(() => {
          try {
            controller.abort(new DOMException('Request timed out', 'AbortError'));
          } catch {
            controller.abort();
          }
        }, timeoutMs);

        try {
          const response = await fetch(candidate, { ...init, signal: controller.signal });
          clearTimeout(timeoutId);
          if (response.status === 404 && index < candidates.length - 1) {
            continue;
          }
          return response;
        } catch (error) {
          clearTimeout(timeoutId);
          lastError = error;
          if (index === candidates.length - 1) {
            throw error;
          }
        }
      }

      if (lastError instanceof Error) {
        throw lastError;
      }
      throw new Error('Request failed');
    },
    [apiCandidates]
  );

  const setActionMessage = (nextStatus: ActionStatus, nextMessage: string) => {
    setStatus(nextStatus);
    setMessage(nextMessage);
  };

  const getErrorDetail = async (response: Response, fallback: string) => {
    try {
      const payload = await response.json();
      if (typeof payload?.detail === 'string' && payload.detail.trim().length > 0) {
        return payload.detail;
      }
    } catch {
      // ignore parse failure, fallback below
    }
    return fallback;
  };

  const fetchSkills = useCallback(async () => {
    setIsLoadingList(true);
    try {
      const response = await fetchWithTimeout('/skills', {
        headers: getAuthHeaders(),
      });
      if (!response.ok) {
        setSkills([]);
        setActionMessage('error', 'Failed to load skills. Please verify API connectivity.');
        return;
      }
      const data = (await response.json()) as { skills: SkillSummary[] };
      setSkills(data.skills || []);

      if (!selectedId && data.skills.length > 0) {
        setSelectedId(data.skills[0].id);
      }
      if (selectedId && !data.skills.some((skill) => skill.id === selectedId)) {
        setSelectedId(data.skills[0]?.id || null);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage('error', 'Skills request timed out. Please retry.');
      } else {
        setActionMessage('error', 'Failed to load skills.');
      }
    } finally {
      setIsLoadingList(false);
    }
  }, [fetchWithTimeout, getAuthHeaders, selectedId]);

  const fetchSkillDetail = useCallback(
    async (skillId: string) => {
      setIsLoadingDetail(true);
      try {
        const response = await fetchWithTimeout(`/skills/${skillId}`, {
          headers: getAuthHeaders(),
        });
      if (!response.ok) {
        const detail = await getErrorDetail(response, 'Failed to load selected skill.');
        setActionMessage('error', detail);
        return;
      }
        const data = (await response.json()) as SkillDetail;
        setDraft(data);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          setActionMessage('error', 'Skill detail request timed out. Please retry.');
        } else {
          setActionMessage('error', 'Failed to load selected skill.');
        }
      } finally {
        setIsLoadingDetail(false);
      }
    },
    [fetchWithTimeout, getAuthHeaders]
  );

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  useEffect(() => {
    if (!selectedId) {
      setDraft(EMPTY_DETAIL);
      return;
    }
    fetchSkillDetail(selectedId);
  }, [fetchSkillDetail, selectedId]);

  const handleCreateSkill = async () => {
    const existing = new Set(skills.map((skill) => skill.id));
    let nextName = 'new-skill';
    let index = 1;
    while (existing.has(nextName)) {
      index += 1;
      nextName = `new-skill-${index}`;
    }

    setActionMessage('loading', 'Creating skill...');
    try {
      const response = await fetchWithTimeout('/skills', {
        method: 'POST',
        headers: getJsonHeaders(),
        body: JSON.stringify({
          name: nextName,
          description: 'Describe when this skill should be used.',
          content: '# Instructions\n\nAdd actionable guidance for the agent here.',
          enabled: true,
        }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(
          response,
          'Failed to create skill. Please verify API connectivity.'
        );
        setActionMessage('error', detail);
        return;
      }

      const created = (await response.json()) as SkillDetail;
      await fetchSkills();
      setSelectedId(created.id);
      setActionMessage('success', 'Skill created.');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage('error', 'Create skill request timed out. Please retry.');
      } else {
        setActionMessage('error', 'Failed to create skill.');
      }
    }
  };

  const handleSave = async () => {
    if (!selectedId) {
      return;
    }

    setActionMessage('loading', 'Saving skill...');
    try {
      const response = await fetchWithTimeout(`/skills/${selectedId}`, {
        method: 'PUT',
        headers: getJsonHeaders(),
        body: JSON.stringify({
          name: draft.name,
          description: draft.description,
          content: draft.content,
          enabled: draft.enabled,
        }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(
          response,
          'Failed to save skill. Please verify API connectivity.'
        );
        setActionMessage('error', detail);
        return;
      }

      const updated = (await response.json()) as SkillDetail;
      setDraft(updated);
      await fetchSkills();
      setActionMessage('success', 'Skill saved.');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage('error', 'Save skill request timed out. Please retry.');
      } else {
        setActionMessage('error', 'Failed to save skill.');
      }
    }
  };

  const handleToggleEnabled = async (enabled: boolean) => {
    if (!selectedId) {
      return;
    }

    setDraft((prev) => ({ ...prev, enabled }));
    setActionMessage('loading', enabled ? 'Enabling skill...' : 'Disabling skill...');
    try {
      const response = await fetchWithTimeout(`/skills/${selectedId}/enabled`, {
        method: 'PATCH',
        headers: getJsonHeaders(),
        body: JSON.stringify({ enabled }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(response, 'Failed to update skill status.');
        setActionMessage('error', detail);
        return;
      }

      const updated = (await response.json()) as SkillDetail;
      setDraft(updated);
      await fetchSkills();
      setActionMessage('success', enabled ? 'Skill enabled.' : 'Skill disabled.');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage('error', 'Skill status request timed out. Please retry.');
      } else {
        setActionMessage('error', 'Failed to update skill status.');
      }
    }
  };

  const handleDelete = async () => {
    if (!selectedId) {
      return;
    }

    const confirmed = window.confirm('Delete this skill? This action cannot be undone.');
    if (!confirmed) {
      return;
    }

    setActionMessage('loading', 'Deleting skill...');
    try {
      const response = await fetchWithTimeout(`/skills/${selectedId}`, {
        method: 'DELETE',
        headers: getAuthHeaders(),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(response, 'Failed to delete skill.');
        setActionMessage('error', detail);
        return;
      }

      const nextSkills = skills.filter((skill) => skill.id !== selectedId);
      setSkills(nextSkills);
      setSelectedId(nextSkills[0]?.id || null);
      setActionMessage('success', 'Skill deleted.');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage('error', 'Delete skill request timed out. Please retry.');
      } else {
        setActionMessage('error', 'Failed to delete skill.');
      }
    }
  };

  const selectedSummary = useMemo(
    () => skills.find((skill) => skill.id === selectedId) || null,
    [selectedId, skills]
  );

  const handleUploadClick = () => {
    uploadInputRef.current?.click();
  };

  const handleUploadFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';

    if (!file) {
      return;
    }

    if (!file.name.toLowerCase().endsWith('.md')) {
      setActionMessage('error', 'Only .md files are supported for skill upload.');
      return;
    }

    setActionMessage('loading', 'Uploading skill file...');

    const formData = new FormData();
    formData.append('file', file);
    formData.append('overwrite', 'false');

    try {
      const response = await fetchWithTimeout('/skills/upload', {
        method: 'POST',
        headers: getAuthHeaders(),
        body: formData,
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => null);
        const detail =
          typeof errorBody?.detail === 'string'
            ? errorBody.detail
            : 'Failed to upload skill file. Ensure it matches the standard skill format.';
        setActionMessage('error', detail);
        return;
      }

      const imported = (await response.json()) as SkillDetail;
      await fetchSkills();
      setSelectedId(imported.id);
      setActionMessage('success', `Imported skill '${imported.name}'.`);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage('error', 'Skill upload timed out. Please retry.');
      } else {
        setActionMessage('error', 'Failed to upload skill file.');
      }
    }
  };

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-text-primary mb-2">Skills</h1>
        <p className="text-text-secondary">
          Create reusable skill files and control which ones are injected into agent prompts.
        </p>
        <p className="mt-2 text-xs text-[var(--color-text-muted)]">
          Upload `.md` skill files using either frontmatter (`name`, `description`, `enabled`) or the
          standard markdown skill format (`# Title` + `## Purpose` + instructions).
        </p>
      </header>

      {status === 'error' && (
        <div className="flex items-center gap-2 rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          <AlertCircle className="h-4 w-4" />
          {message}
        </div>
      )}
      {status === 'success' && (
        <div className="flex items-center gap-2 rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-300">
          <CheckCircle className="h-4 w-4" />
          {message}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        <section className="rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-3">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">My skills</h2>
            <div className="flex items-center gap-1">
              <button
                onClick={handleUploadClick}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]"
                title="Upload skill.md"
              >
                <Upload className="h-4 w-4" />
              </button>
              <button
                onClick={handleCreateSkill}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] hover:text-[var(--color-text-primary)]"
                title="Create skill"
              >
                <Plus className="h-4 w-4" />
              </button>
            </div>
          </div>

          <input
            ref={uploadInputRef}
            type="file"
            accept=".md,text/markdown"
            className="hidden"
            onChange={handleUploadFile}
          />

          <div className="space-y-1">
            {isLoadingList && <div className="px-3 py-2 text-sm text-[var(--color-text-muted)]">Loading skills...</div>}

            {!isLoadingList && skills.length === 0 && (
              <div className="px-3 py-2 text-sm text-[var(--color-text-muted)]">No skills yet. Create your first skill.</div>
            )}

            {skills.map((skill) => {
              const active = selectedId === skill.id;
              return (
                <button
                  key={skill.id}
                  onClick={() => setSelectedId(skill.id)}
                  className={`w-full rounded-md border px-3 py-2 text-left transition-colors ${
                    active
                      ? 'border-[var(--color-accent-primary)] bg-[var(--color-accent-subtle)]'
                      : 'border-transparent hover:bg-[var(--color-bg-tertiary)]'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">{skill.name}</span>
                    <span
                      className={`h-2 w-2 rounded-full ${
                        skill.enabled ? 'bg-green-400' : 'bg-[var(--color-text-muted)]'
                      }`}
                    />
                  </div>
                  <p className="mt-1 truncate text-xs text-[var(--color-text-muted)]">{skill.description || 'No description'}</p>
                </button>
              );
            })}
          </div>
        </section>

        <section className="rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4 sm:p-5">
          {!selectedId && <div className="text-sm text-[var(--color-text-muted)]">Select a skill to edit.</div>}

          {selectedId && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border-primary)] pb-3">
                <div>
                  <h3 className="text-lg font-semibold text-[var(--color-text-primary)]">{selectedSummary?.name || draft.name}</h3>
                  <p className="text-xs text-[var(--color-text-muted)]">
                    Last updated {selectedSummary ? new Date(selectedSummary.updated_at).toLocaleString() : '-'}
                  </p>
                </div>

                <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
                  <input
                    type="checkbox"
                    checked={draft.enabled}
                    onChange={(event) => handleToggleEnabled(event.target.checked)}
                    className="h-4 w-4 accent-[var(--color-accent-primary)]"
                  />
                  Enabled
                </label>
              </div>

              {isLoadingDetail ? (
                <div className="text-sm text-[var(--color-text-muted)]">Loading skill details...</div>
              ) : (
                <>
                  <div>
                    <label className="mb-2 block text-sm font-medium text-[var(--color-text-secondary)]">Name</label>
                    <input
                      type="text"
                      value={draft.name}
                      onChange={(event) => setDraft((prev) => ({ ...prev, name: event.target.value }))}
                      className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-3 py-2 text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="mb-2 block text-sm font-medium text-[var(--color-text-secondary)]">Description</label>
                    <input
                      type="text"
                      value={draft.description}
                      onChange={(event) => setDraft((prev) => ({ ...prev, description: event.target.value }))}
                      className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-3 py-2 text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="mb-2 block text-sm font-medium text-[var(--color-text-secondary)]">Instructions (Markdown)</label>
                    <textarea
                      value={draft.content}
                      onChange={(event) => setDraft((prev) => ({ ...prev, content: event.target.value }))}
                      rows={16}
                      className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-3 py-2 font-mono text-sm text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none"
                    />
                  </div>
                </>
              )}

              <div className="flex flex-wrap items-center gap-2 border-t border-[var(--color-border-primary)] pt-3">
                <button
                  onClick={handleSave}
                  disabled={status === 'loading' || isLoadingDetail}
                  className="inline-flex items-center gap-2 rounded-md bg-[var(--color-accent-primary)] px-4 py-2 text-white hover:bg-[var(--color-accent-hover)] disabled:opacity-50"
                >
                  <Save className="h-4 w-4" />
                  {status === 'loading' ? 'Saving...' : 'Save skill'}
                </button>

                <button
                  onClick={handleDelete}
                  disabled={status === 'loading'}
                  className="inline-flex items-center gap-2 rounded-md border border-red-500/50 px-4 py-2 text-red-300 hover:bg-red-500/10 disabled:opacity-50"
                >
                  <Trash2 className="h-4 w-4" />
                  Delete
                </button>

                <div className="ml-auto inline-flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                  <FileCode2 className="h-4 w-4" />
                  Agents can use enabled skills in prompt assembly.
                </div>
              </div>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
