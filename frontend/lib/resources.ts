import { ensureAuthHeader, getAuthGeneration } from './auth';
export type ResourceItem = {
  resource_id: string;
  version_id: string;
  representation: string;
  kind: string;
  mime: string;
  measured_bytes: number | null;
  created_at: string;
  availability: string;
  display: { name: string | null; title: string | null };
  reference: null | {
    reference_id: string;
    role: 'input' | 'output';
    ordinal: number;
    task_id: string | null;
    conversation_id: string | null;
    message_id: string | null;
    source_deleted: boolean;
  };
};
export type ResourcePage = {
  schema_version: 1;
  items: ResourceItem[];
  next_cursor: string | null;
};
export type PinnedEnvelope = {
  schema_version: number;
  component_library_version: number;
  snapshot_at: string;
  component: string;
  items: { resource_id: string; version_id: string }[];
};
export const isResourceUUID = (value: unknown): value is string =>
  typeof value === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
export class ResourceReadError extends Error {
  constructor(
    public code: string,
    public status = 0,
  ) {
    super(code);
  }
}
export async function resourceResponse(
  path: string,
  signal: AbortSignal,
  generation: number,
) {
  const current = () => {
    if (signal.aborted || getAuthGeneration() !== generation)
      throw new ResourceReadError('account_changed');
  };
  current();
  const authorization = await ensureAuthHeader();
  current();
  if (!authorization) throw new ResourceReadError('sign_in_required', 401);
  const response = await fetch(`/api${path}`, {
    headers: { Authorization: authorization },
    signal,
    cache: 'no-store',
    redirect: 'error',
  });
  current();
  if (!response.ok)
    throw new ResourceReadError(
      response.status === 409
        ? 'restart_required'
        : response.status === 404
          ? 'unavailable'
          : 'read_failed',
      response.status,
    );
  return response;
}
export async function resourceJSON<T>(
  path: string,
  signal: AbortSignal,
  generation: number,
): Promise<T> {
  const response = await resourceResponse(path, signal, generation);
  const value: unknown = await response.json();
  if (signal.aborted || getAuthGeneration() !== generation)
    throw new ResourceReadError('account_changed');
  return value as T;
}
export function resourcePath(resource: string, version: string) {
  if (!isResourceUUID(resource) || !isResourceUUID(version))
    throw new ResourceReadError('invalid_reference');
  return `/resources/${resource}/versions/${version}`;
}
