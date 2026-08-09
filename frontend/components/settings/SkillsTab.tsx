'use client';

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from 'react';
import {
  AlertCircle,
  CheckCircle,
  Download,
  FileCode2,
  Plus,
  Save,
  Trash2,
  Upload,
  Search,
  Filter,
  Sparkles,
  AlertTriangle,
  X,
  Check,
  Bot,
  Lock,
  Globe,
  User,
  Code,
} from 'lucide-react';
import { ensureAuthHeader } from '@/lib/auth';

type ActionStatus = 'idle' | 'loading' | 'success' | 'error';
type FilterType = 'all' | 'system' | 'imported' | 'manual' | 'autonomous';

type SkillSourceType = 'system' | 'imported' | 'manual' | 'autonomous' | null;

interface SkillSummary {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  updated_at: string;
  source_type?: SkillSourceType;
  allow_autonomous_edit?: boolean | null;
  repo_version?: string | null;
  local_version?: string | null;
  pending_update?: Record<string, unknown> | null;
  use_count?: number | null;
  last_used_at?: string | null;
}

interface SkillDetail extends SkillSummary {
  content: string;
  created_by?: string | null;
  origin_url?: string | null;
}

const EMPTY_DETAIL: SkillDetail = {
  id: '',
  name: '',
  description: '',
  content: '',
  enabled: true,
  updated_at: '',
};

const SOURCE_BADGE_CONFIG: Record<
  string,
  { icon: typeof Lock; color: string; label: string }
> = {
  system: {
    icon: Lock,
    color: 'text-blue-400 bg-blue-500/10',
    label: 'System',
  },
  imported: {
    icon: Globe,
    color: 'text-purple-400 bg-purple-500/10',
    label: 'Imported',
  },
  manual: {
    icon: User,
    color: 'text-green-400 bg-green-500/10',
    label: 'Manual',
  },
  autonomous: {
    icon: Bot,
    color: 'text-amber-400 bg-amber-500/10',
    label: 'Autonomous',
  },
};

export default function SkillsTab() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<SkillDetail>(EMPTY_DETAIL);
  const [isLoadingList, setIsLoadingList] = useState(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [status, setStatus] = useState<ActionStatus>('idle');
  const [message, setMessage] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<FilterType>('all');
  const [showFilters, setShowFilters] = useState(false);
  const uploadInputRef = useRef<HTMLInputElement | null>(null);

  const getAuthHeaders = useCallback(async (): Promise<HeadersInit> => {
    const header = await ensureAuthHeader();
    if (!header) return [];
    return { Authorization: header };
  }, []);

  const getJsonHeaders = useCallback(async (): Promise<HeadersInit> => {
    const authHeader = await ensureAuthHeader();
    if (authHeader) {
      return {
        'Content-Type': 'application/json',
        Authorization: authHeader,
      };
    }
    return { 'Content-Type': 'application/json' };
  }, []);

  const fetchWithTimeout = useCallback(
    async (path: string, init: RequestInit = {}, timeoutMs = 12000) => {
      const proxyPath = path.startsWith('/') ? `/api${path}` : `/api/${path}`;
      const controller = new AbortController();
      const timeoutId = setTimeout(() => {
        try {
          controller.abort(new DOMException('Request timed out', 'AbortError'));
        } catch {
          controller.abort();
        }
      }, timeoutMs);

      try {
        const response = await fetch(proxyPath, {
          ...init,
          signal: controller.signal,
        });
        clearTimeout(timeoutId);
        return response;
      } catch (error) {
        clearTimeout(timeoutId);
        throw error;
      }
    },
    [],
  );

  const setActionMessage = (nextStatus: ActionStatus, nextMessage: string) => {
    setStatus(nextStatus);
    setMessage(nextMessage);
  };

  const getErrorDetail = async (response: Response, fallback: string) => {
    try {
      const payload = await response.json();
      if (
        typeof payload?.detail === 'string' &&
        payload.detail.trim().length > 0
      ) {
        return payload.detail;
      }
    } catch {
      return fallback;
    }
  };

  const fetchSkills = useCallback(async () => {
    setIsLoadingList(true);
    try {
      const response = await fetchWithTimeout('/skills', {
        headers: await getAuthHeaders(),
      });
      if (!response.ok) {
        setSkills([]);
        setActionMessage(
          'error',
          'Failed to load skills. Please verify API connectivity.',
        );
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
          headers: await getAuthHeaders(),
        });
        if (!response.ok) {
          const detail = await getErrorDetail(
            response,
            'Failed to load selected skill.',
          );
          setActionMessage('error', detail);
          return;
        }
        const data = (await response.json()) as SkillDetail;
        setDraft(data);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') {
          setActionMessage(
            'error',
            'Skill detail request timed out. Please retry.',
          );
        } else {
          setActionMessage('error', 'Failed to load selected skill.');
        }
      } finally {
        setIsLoadingDetail(false);
      }
    },
    [fetchWithTimeout, getAuthHeaders],
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

  const filteredSkills = useMemo(() => {
    return skills.filter((skill) => {
      const matchesFilter =
        filterType === 'all' || skill.source_type === filterType;
      const query = searchQuery.toLowerCase().trim();
      const matchesSearch =
        !query ||
        skill.name.toLowerCase().includes(query) ||
        skill.description.toLowerCase().includes(query) ||
        skill.id.toLowerCase().includes(query);
      return matchesFilter && matchesSearch;
    });
  }, [skills, filterType, searchQuery]);

  const pendingUpdateCount = useMemo(
    () =>
      skills.filter(
        (s) => s.pending_update && Object.keys(s.pending_update).length > 0,
      ).length,
    [skills],
  );

  const autonomousCount = useMemo(
    () => skills.filter((s) => s.source_type === 'autonomous').length,
    [skills],
  );

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
        headers: await getJsonHeaders(),
        body: JSON.stringify({
          name: nextName,
          description: 'Describe when this skill should be used.',
          content:
            '# Instructions\n\nAdd actionable guidance for the agent here.',
          enabled: true,
        }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(
          response,
          'Failed to create skill. Please verify API connectivity.',
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
        setActionMessage(
          'error',
          'Create skill request timed out. Please retry.',
        );
      } else {
        setActionMessage('error', 'Failed to create skill.');
      }
    }
  };

  const handleSave = async () => {
    if (!selectedId) return;

    setActionMessage('loading', 'Saving skill...');
    try {
      const response = await fetchWithTimeout(`/skills/${selectedId}`, {
        method: 'PUT',
        headers: await getJsonHeaders(),
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
          'Failed to save skill. Please verify API connectivity.',
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
        setActionMessage(
          'error',
          'Save skill request timed out. Please retry.',
        );
      } else {
        setActionMessage('error', 'Failed to save skill.');
      }
    }
  };

  const handleToggleEnabled = async (enabled: boolean) => {
    if (!selectedId) return;

    setDraft((prev) => ({ ...prev, enabled }));
    setActionMessage(
      'loading',
      enabled ? 'Enabling skill...' : 'Disabling skill...',
    );
    try {
      const response = await fetchWithTimeout(`/skills/${selectedId}/enabled`, {
        method: 'PATCH',
        headers: await getJsonHeaders(),
        body: JSON.stringify({ enabled }),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(
          response,
          'Failed to update skill status.',
        );
        setActionMessage('error', detail);
        return;
      }

      const updated = (await response.json()) as SkillDetail;
      setDraft(updated);
      await fetchSkills();
      setActionMessage(
        'success',
        enabled ? 'Skill enabled.' : 'Skill disabled.',
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage(
          'error',
          'Skill status request timed out. Please retry.',
        );
      } else {
        setActionMessage('error', 'Failed to update skill status.');
      }
    }
  };

  const handleToggleAutonomousEdit = async (allow_autonomous_edit: boolean) => {
    if (!selectedId) return;

    setActionMessage(
      'loading',
      allow_autonomous_edit
        ? 'Enabling autonomous edit...'
        : 'Disabling autonomous edit...',
    );
    try {
      const response = await fetchWithTimeout(
        `/skills/${selectedId}/autonomous-edit`,
        {
          method: 'PATCH',
          headers: await getJsonHeaders(),
          body: JSON.stringify({ allow_autonomous_edit }),
        },
      );

      if (!response.ok) {
        const detail = await getErrorDetail(
          response,
          'Failed to update autonomous edit setting.',
        );
        setActionMessage('error', detail);
        return;
      }

      await fetchSkills();
      await fetchSkillDetail(selectedId);
      setActionMessage(
        'success',
        allow_autonomous_edit
          ? 'Autonomous edit enabled.'
          : 'Autonomous edit disabled.',
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage(
          'error',
          'Autonomous edit request timed out. Please retry.',
        );
      } else {
        setActionMessage('error', 'Failed to update autonomous edit setting.');
      }
    }
  };

  const handlePendingUpdateAction = async (action: 'apply' | 'dismiss') => {
    if (!selectedId) return;

    setActionMessage(
      'loading',
      action === 'apply' ? 'Applying update...' : 'Dismissing update...',
    );
    try {
      const response = await fetchWithTimeout(
        `/skills/${selectedId}/pending-update`,
        {
          method: 'POST',
          headers: await getJsonHeaders(),
          body: JSON.stringify({ action }),
        },
      );

      if (!response.ok) {
        const detail = await getErrorDetail(
          response,
          `Failed to ${action} pending update.`,
        );
        setActionMessage('error', detail);
        return;
      }

      await fetchSkills();
      await fetchSkillDetail(selectedId);
      setActionMessage(
        'success',
        action === 'apply' ? 'Update applied.' : 'Update dismissed.',
      );
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage(
          'error',
          'Pending update request timed out. Please retry.',
        );
      } else {
        setActionMessage('error', `Failed to ${action} pending update.`);
      }
    }
  };

  const handleDelete = async () => {
    if (!selectedId) return;

    const confirmed = window.confirm(
      'Delete this skill? This action cannot be undone.',
    );
    if (!confirmed) return;

    setActionMessage('loading', 'Deleting skill...');
    try {
      const response = await fetchWithTimeout(`/skills/${selectedId}`, {
        method: 'DELETE',
        headers: await getAuthHeaders(),
      });

      if (!response.ok) {
        const detail = await getErrorDetail(
          response,
          'Failed to delete skill.',
        );
        setActionMessage('error', detail);
        return;
      }

      const nextSkills = skills.filter((skill) => skill.id !== selectedId);
      setSkills(nextSkills);
      setSelectedId(nextSkills[0]?.id || null);
      setActionMessage('success', 'Skill deleted.');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage(
          'error',
          'Delete skill request timed out. Please retry.',
        );
      } else {
        setActionMessage('error', 'Failed to delete skill.');
      }
    }
  };

  const handleDownload = async () => {
    if (!selectedId) return;

    try {
      const response = await fetchWithTimeout(
        `/skills/${selectedId}/download`,
        {
          method: 'GET',
          headers: await getAuthHeaders(),
        },
      );

      if (!response.ok) {
        const detail = await getErrorDetail(
          response,
          'Failed to download skill.',
        );
        setActionMessage('error', detail);
        return;
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${selectedId}.md`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      setActionMessage('success', 'Skill downloaded.');
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        setActionMessage('error', 'Download request timed out. Please retry.');
      } else {
        setActionMessage('error', 'Failed to download skill.');
      }
    }
  };

  const selectedSummary = useMemo(
    () => skills.find((skill) => skill.id === selectedId) || null,
    [selectedId, skills],
  );

  const handleUploadClick = () => {
    uploadInputRef.current?.click();
  };

  const handleUploadFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';

    if (!file) return;

    if (!file.name.toLowerCase().endsWith('.md')) {
      setActionMessage(
        'error',
        'Only .md files are supported for skill upload.',
      );
      return;
    }

    setActionMessage('loading', 'Uploading skill file...');

    const formData = new FormData();
    formData.append('file', file);
    formData.append('overwrite', 'false');

    try {
      const response = await fetchWithTimeout('/skills/upload', {
        method: 'POST',
        headers: await getAuthHeaders(),
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

  const renderSourceBadge = (sourceType: SkillSourceType) => {
    if (!sourceType) return null;
    const config = SOURCE_BADGE_CONFIG[sourceType];
    if (!config) return null;
    const Icon = config.icon;
    return (
      <span
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] uppercase tracking-wider ${config.color}`}
      >
        <Icon className="h-3 w-3" />
        {config.label}
      </span>
    );
  };

  const hasPendingUpdate =
    selectedSummary?.pending_update &&
    Object.keys(selectedSummary.pending_update).length > 0;
  const pendingIsDeprecated =
    hasPendingUpdate && selectedSummary?.pending_update?.deprecated === true;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold text-text-primary mb-2">
          Skills
        </h1>
        <p className="text-text-secondary">
          Create reusable skill files and control which ones are injected into
          agent prompts.
        </p>
        <div className="mt-2 flex flex-wrap gap-4 text-xs text-[var(--color-text-muted)]">
          <span>{skills.length} total</span>
          {autonomousCount > 0 && (
            <span className="text-amber-400">{autonomousCount} autonomous</span>
          )}
          {pendingUpdateCount > 0 && (
            <span className="text-blue-400">
              {pendingUpdateCount} pending updates
            </span>
          )}
        </div>
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

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <section className="rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-3">
          <div className="mb-3 space-y-2">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                My skills
              </h2>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setShowFilters(!showFilters)}
                  className={`inline-flex h-8 w-8 items-center justify-center rounded-md ${showFilters ? 'bg-[var(--color-accent-primary)] text-white' : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)]'}`}
                  title="Toggle filters"
                >
                  <Filter className="h-4 w-4" />
                </button>
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

            <div className="relative">
              <Search className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--color-text-muted)]" />
              <input
                type="text"
                placeholder="Search skills..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] pl-8 pr-8 py-1.5 text-sm text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text-primary)]"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>

            {showFilters && (
              <div className="flex flex-wrap gap-1">
                {(
                  [
                    'all',
                    'system',
                    'imported',
                    'manual',
                    'autonomous',
                  ] as FilterType[]
                ).map((type) => (
                  <button
                    key={type}
                    onClick={() => setFilterType(type)}
                    className={`px-2 py-1 rounded text-xs capitalize transition-colors ${
                      filterType === type
                        ? 'bg-[var(--color-accent-primary)] text-white'
                        : 'bg-[var(--color-bg-tertiary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-border-primary)]'
                    }`}
                  >
                    {type === 'all' ? 'All' : type}
                  </button>
                ))}
              </div>
            )}
          </div>

          <input
            ref={uploadInputRef}
            type="file"
            accept=".md,text/markdown"
            className="hidden"
            onChange={handleUploadFile}
          />

          <div className="space-y-1 max-h-[500px] overflow-y-auto">
            {isLoadingList && (
              <div className="px-3 py-2 text-sm text-[var(--color-text-muted)]">
                Loading skills...
              </div>
            )}

            {!isLoadingList && filteredSkills.length === 0 && (
              <div className="px-3 py-2 text-sm text-[var(--color-text-muted)]">
                {searchQuery || filterType !== 'all'
                  ? 'No matching skills.'
                  : 'No skills yet. Create your first skill.'}
              </div>
            )}

            {filteredSkills.map((skill) => {
              const active = selectedId === skill.id;
              const hasPending =
                skill.pending_update &&
                Object.keys(skill.pending_update).length > 0;
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
                    <span className="truncate text-sm font-medium text-[var(--color-text-primary)]">
                      {skill.name}
                    </span>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {skill.source_type &&
                        renderSourceBadge(skill.source_type)}
                      {hasPending && (
                        <span
                          className="text-blue-400"
                          title="Update available"
                        >
                          <AlertTriangle className="h-3 w-3" />
                        </span>
                      )}
                      {skill.allow_autonomous_edit && (
                        <span
                          className="text-amber-400"
                          title="Autonomous edit enabled"
                        >
                          <Sparkles className="h-3 w-3" />
                        </span>
                      )}
                      <span
                        className={`h-2 w-2 rounded-full ${
                          skill.enabled
                            ? 'bg-green-400'
                            : 'bg-[var(--color-text-muted)]'
                        }`}
                      />
                    </div>
                  </div>
                  <p className="mt-1 truncate text-xs text-[var(--color-text-muted)]">
                    {skill.description || 'No description'}
                  </p>
                </button>
              );
            })}
          </div>
        </section>

        <section className="rounded-lg border border-[var(--color-border-primary)] bg-[var(--color-bg-secondary)] p-4 sm:p-5">
          {!selectedId && (
            <div className="text-sm text-[var(--color-text-muted)]">
              Select a skill to edit.
            </div>
          )}

          {selectedId && (
            <div className="space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--color-border-primary)] pb-3">
                <div className="space-y-1">
                  <h3 className="text-lg font-semibold text-[var(--color-text-primary)]">
                    {selectedSummary?.name || draft.name}
                  </h3>
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    {selectedSummary?.source_type &&
                      renderSourceBadge(selectedSummary.source_type)}
                    <span className="text-[var(--color-text-muted)]">
                      Updated{' '}
                      {selectedSummary
                        ? new Date(
                            selectedSummary.updated_at,
                          ).toLocaleDateString()
                        : '-'}
                    </span>
                    {typeof selectedSummary?.use_count === 'number' &&
                      selectedSummary.use_count > 0 && (
                        <span className="text-[var(--color-text-muted)]">
                          · Used {selectedSummary.use_count}×
                        </span>
                      )}
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  {(selectedSummary?.source_type === 'system' ||
                    selectedSummary?.source_type === 'imported' ||
                    selectedSummary?.source_type === 'manual') && (
                    <label
                      className="inline-flex items-center gap-2 text-xs text-[var(--color-text-secondary)]"
                      title="Allow the system to autonomously modify this skill based on usage patterns"
                    >
                      <input
                        type="checkbox"
                        checked={!!selectedSummary?.allow_autonomous_edit}
                        onChange={(event) =>
                          handleToggleAutonomousEdit(event.target.checked)
                        }
                        className="h-3.5 w-3.5 accent-[var(--color-accent-primary)]"
                      />
                      <span className="flex items-center gap-1">
                        <Sparkles className="h-3 w-3 text-amber-400" />
                        Allow autonomous edits
                      </span>
                    </label>
                  )}
                  <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
                    <input
                      type="checkbox"
                      checked={draft.enabled}
                      onChange={(event) =>
                        handleToggleEnabled(event.target.checked)
                      }
                      className="h-4 w-4 accent-[var(--color-accent-primary)]"
                    />
                    Enabled
                  </label>
                </div>
              </div>

              {hasPendingUpdate && (
                <div
                  className={`rounded-md border px-4 py-3 text-sm ${
                    pendingIsDeprecated
                      ? 'border-amber-500/40 bg-amber-500/10 text-amber-300'
                      : 'border-blue-500/40 bg-blue-500/10 text-blue-300'
                  }`}
                >
                  <div className="flex items-start gap-2">
                    {pendingIsDeprecated ? (
                      <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                    ) : (
                      <Sparkles className="h-4 w-4 shrink-0 mt-0.5" />
                    )}
                    <div className="flex-1">
                      <p className="font-medium">
                        {pendingIsDeprecated
                          ? 'Skill deprecated'
                          : 'Update available'}
                      </p>
                      <p className="text-xs opacity-80 mt-1">
                        {pendingIsDeprecated
                          ? 'This skill has been removed from the repository. You may continue using it locally.'
                          : `Repository version ${selectedSummary?.repo_version || 'newer'} is available.`}
                      </p>
                      {!pendingIsDeprecated && (
                        <div className="mt-3 flex gap-2">
                          <button
                            onClick={() => handlePendingUpdateAction('apply')}
                            disabled={status === 'loading'}
                            className="inline-flex items-center gap-1 rounded-md bg-blue-500/20 px-3 py-1.5 text-xs text-blue-300 hover:bg-blue-500/30 disabled:opacity-50"
                          >
                            <Check className="h-3 w-3" />
                            Apply update
                          </button>
                          <button
                            onClick={() => handlePendingUpdateAction('dismiss')}
                            disabled={status === 'loading'}
                            className="inline-flex items-center gap-1 rounded-md border border-blue-500/40 px-3 py-1.5 text-xs text-blue-300 hover:bg-blue-500/10 disabled:opacity-50"
                          >
                            <X className="h-3 w-3" />
                            Dismiss
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {selectedSummary?.source_type === 'autonomous' && (
                <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">
                  <Bot className="h-4 w-4 shrink-0" />
                  <p>
                    Created autonomously by the system. Always editable based on
                    usage patterns.
                  </p>
                </div>
              )}

              {(selectedSummary?.source_type === 'system' ||
                selectedSummary?.source_type === 'imported' ||
                selectedSummary?.source_type === 'manual') &&
                selectedSummary?.allow_autonomous_edit && (
                  <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-300">
                    <Sparkles className="h-4 w-4 shrink-0" />
                    <p>
                      Autonomous edits enabled. The system may modify this skill
                      automatically based on usage patterns.
                    </p>
                  </div>
                )}

              {isLoadingDetail ? (
                <div className="text-sm text-[var(--color-text-muted)]">
                  Loading skill details...
                </div>
              ) : (
                <>
                  <div>
                    <label className="mb-2 block text-sm font-medium text-[var(--color-text-secondary)]">
                      Name
                    </label>
                    <input
                      type="text"
                      value={draft.name}
                      onChange={(event) =>
                        setDraft((prev) => ({
                          ...prev,
                          name: event.target.value,
                        }))
                      }
                      className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-3 py-2 text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="mb-2 block text-sm font-medium text-[var(--color-text-secondary)]">
                      Description
                    </label>
                    <input
                      type="text"
                      value={draft.description}
                      onChange={(event) =>
                        setDraft((prev) => ({
                          ...prev,
                          description: event.target.value,
                        }))
                      }
                      className="w-full rounded-md border border-[var(--color-border-primary)] bg-[var(--color-bg-tertiary)] px-3 py-2 text-[var(--color-text-primary)] focus:border-[var(--color-accent-primary)] focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="mb-2 block text-sm font-medium text-[var(--color-text-secondary)]">
                      Instructions (Markdown)
                    </label>
                    <textarea
                      value={draft.content}
                      onChange={(event) =>
                        setDraft((prev) => ({
                          ...prev,
                          content: event.target.value,
                        }))
                      }
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
                  onClick={handleDownload}
                  disabled={status === 'loading' || isLoadingDetail}
                  className="inline-flex items-center gap-2 rounded-md border border-[var(--color-border-primary)] px-4 py-2 text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-tertiary)] disabled:opacity-50"
                >
                  <Download className="h-4 w-4" />
                  Download
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
