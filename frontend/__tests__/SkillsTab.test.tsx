import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: async () => 'Bearer test',
}));

import SkillsTab from '@/components/settings/SkillsTab';

interface Call {
  path: string;
  method: string;
  body: unknown;
}

const account = {
  id: 'deploy-checklist',
  name: 'Deploy Checklist',
  description: 'How to deploy',
  enabled: true,
  updated_at: '2026-11-13T10:00:00+00:00',
  scope: 'account',
  revision: 3,
  source_type: 'autonomous',
  allow_autonomous_edit: true,
  use_count: 0,
  last_used_at: null,
  content: 'third',
};

const shipped = {
  ...account,
  id: 'house-style',
  name: 'House Style',
  scope: 'shipped',
  revision: 1,
  source_type: 'system',
  allow_autonomous_edit: false,
  content: 'shipped body',
};

const revisions = [
  { revision: 3, actor: 'autonomous', content: 'third' },
  { revision: 2, actor: 'user', content: 'second' },
  { revision: 1, actor: 'user', content: 'first body' },
].map((entry) => ({
  ...entry,
  name: 'Deploy Checklist',
  description: 'How to deploy',
  created_at: '2026-11-13T09:00:00+00:00',
}));

const other = {
  ...account,
  id: 'runbook',
  name: 'Runbook',
  revision: 2,
  content: 'B two',
};

const otherRevisions = [
  { revision: 2, actor: 'user', content: 'B two' },
  { revision: 1, actor: 'user', content: 'B one' },
].map((entry) => ({
  ...entry,
  name: 'Runbook',
  description: 'How to deploy',
  created_at: '2026-11-13T09:00:00+00:00',
}));

let calls: Call[];
let skills: (typeof account)[];
let putStatus: number;
// Paths whose response is held until the test releases it.
let held: Map<string, { promise: Promise<void>; release: () => void }>;
// A skill that is listed but whose detail request fails.
let brokenDetail: string | null;

function hold(path: string) {
  let release = () => {};
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  held.set(path, { promise, release });
  return release;
}

function json(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

beforeEach(() => {
  calls = [];
  skills = [account, shipped];
  putStatus = 200;
  held = new Map();
  brokenDetail = null;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input).replace(/^\/api/, '');
      const method = init?.method ?? 'GET';
      const body =
        typeof init?.body === 'string' ? JSON.parse(init.body) : undefined;
      calls.push({ path, method, body });
      const gate = held.get(path);
      if (gate) {
        held.delete(path);
        await gate.promise;
      }

      if (path === '/skills') return json({ skills });
      if (path === brokenDetail)
        return json({ detail: 'Skill not found' }, 404);
      if (path === '/skills/runbook/revisions') {
        return json({ revisions: otherRevisions });
      }
      if (path.endsWith('/revisions')) return json({ revisions });
      if (path.endsWith('/revert')) {
        return json({ ...account, revision: 4, content: 'first body' });
      }
      const skill = skills.find((entry) => path === `/skills/${entry.id}`);
      if (skill && method === 'PUT') {
        return putStatus === 200
          ? json({ ...skill, ...(body as object), revision: 4 })
          : json(
              {
                detail:
                  'The skill was changed elsewhere. Reload it and try again',
              },
              putStatus,
            );
      }
      if (skill) return json(skill);
      return json({ detail: 'Skill not found' }, 404);
    }),
  );
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function renderLoaded() {
  render(<SkillsTab />);
  await waitFor(() => expect(screen.getByDisplayValue('third')).toBeTruthy());
}

describe('SkillsTab', () => {
  it('shows history with actors and restores a revision as a new one', async () => {
    await renderLoaded();

    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    await waitFor(() => expect(screen.getByText('Revision 1')).toBeTruthy());
    expect(screen.getByText(/Daemon, autonomous edit/)).toBeTruthy();
    expect(screen.getByText('Current')).toBeTruthy();
    // The current revision cannot be restored onto itself.
    expect(screen.getAllByRole('button', { name: 'Restore' })).toHaveLength(2);

    fireEvent.click(screen.getAllByRole('button', { name: 'View' })[2]);
    expect(screen.getByText('first body')).toBeTruthy();

    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' })[1]);
    await waitFor(() =>
      expect(screen.getByText('Restored revision 1.')).toBeTruthy(),
    );
    const revert = calls.find((call) => call.path.endsWith('/revert'));
    expect(revert).toMatchObject({
      path: '/skills/deploy-checklist/revert',
      method: 'POST',
      body: { revision: 1, expected_revision: 3 },
    });
    expect(screen.getByDisplayValue('first body')).toBeTruthy();
  });

  it('sends the loaded revision on save and reports a conflict', async () => {
    await renderLoaded();
    putStatus = 409;

    fireEvent.click(screen.getByRole('button', { name: 'Save skill' }));
    await waitFor(() =>
      expect(screen.getByText(/changed elsewhere/)).toBeTruthy(),
    );
    const save = calls.find((call) => call.method === 'PUT');
    expect(save?.body).toMatchObject({
      content: 'third',
      expected_revision: 3,
    });
  });

  it('lets the owner revoke autonomous edits on an autonomous skill', async () => {
    await renderLoaded();

    const toggle = screen.getByRole('checkbox', {
      name: /Allow autonomous edits/,
    });
    expect((toggle as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/Daemon may keep refining it/)).toBeTruthy();
    expect(screen.queryByText(/Update available/)).toBeNull();
  });

  it('shows a shipped skill read-only with no write controls', async () => {
    skills = [shipped];
    render(<SkillsTab />);
    await waitFor(() =>
      expect(screen.getByDisplayValue('shipped body')).toBeTruthy(),
    );

    expect(screen.getByText(/Shipped with Daemon and read-only/)).toBeTruthy();
    expect(
      screen.getByDisplayValue('shipped body').hasAttribute('readonly'),
    ).toBe(true);
    for (const name of ['Save skill', 'Delete', 'History']) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    expect(screen.queryByRole('checkbox')).toBeNull();
    expect(
      (screen.getByRole('button', { name: 'Download' }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("never shows or restores one skill's history under another", async () => {
    skills = [account, other];
    await renderLoaded();

    // A's history is requested and held; the person moves to B meanwhile.
    const releaseA = hold('/skills/deploy-checklist/revisions');
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    fireEvent.click(screen.getByText('Runbook'));
    await waitFor(() => expect(screen.getByDisplayValue('B two')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    await waitFor(() => expect(screen.getByText('Revision 2')).toBeTruthy());

    // A's response arrives last and must change nothing.
    releaseA();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText('Revision 3')).toBeNull();
    expect(screen.queryByText(/Daemon, autonomous edit/)).toBeNull();
    expect(screen.getAllByRole('button', { name: 'View' })).toHaveLength(2);
    fireEvent.click(screen.getAllByRole('button', { name: 'View' })[1]);
    expect(screen.getByText('B one')).toBeTruthy();
    expect(screen.queryByText('first body')).toBeNull();

    // Restore acts on B with B's own revision numbers.
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    await waitFor(() =>
      expect(calls.some((call) => call.path.endsWith('/revert'))).toBe(true),
    );
    expect(calls.filter((call) => call.path.endsWith('/revert'))).toEqual([
      {
        path: '/skills/runbook/revert',
        method: 'POST',
        body: { revision: 1, expected_revision: 2 },
      },
    ]);
  });

  it('drops a held history response when the other skill has no history open', async () => {
    skills = [account, other];
    await renderLoaded();

    const releaseA = hold('/skills/deploy-checklist/revisions');
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    fireEvent.click(screen.getByText('Runbook'));
    await waitFor(() => expect(screen.getByDisplayValue('B two')).toBeTruthy());
    releaseA();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText(/^Revision /)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    await waitFor(() => expect(screen.getByText('Revision 2')).toBeTruthy());
    expect(screen.queryByText('Revision 3')).toBeNull();
  });

  it("does not let a late restore response replace another skill's draft", async () => {
    skills = [account, other];
    await renderLoaded();
    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    await waitFor(() => expect(screen.getByText('Revision 1')).toBeTruthy());

    const releaseRevert = hold('/skills/deploy-checklist/revert');
    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' })[1]);
    await waitFor(() =>
      expect(calls.some((call) => call.path.endsWith('/revert'))).toBe(true),
    );
    fireEvent.click(screen.getByText('Runbook'));
    await waitFor(() => expect(screen.getByDisplayValue('B two')).toBeTruthy());

    releaseRevert();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByDisplayValue('B two')).toBeTruthy();
    expect(screen.queryByDisplayValue('first body')).toBeNull();
    expect(screen.queryByText('Restored revision 1.')).toBeNull();
    expect(screen.queryByText(/^Revision /)).toBeNull();
  });

  it("cannot save one skill's draft into another while its detail is pending", async () => {
    // Same revision as the first skill, so a revision check alone would not
    // catch a draft written to the wrong skill.
    const sameRevision = {
      ...account,
      id: 'third-skill',
      name: 'Third Skill',
      content: 'third skill body',
    };
    skills = [account, other, sameRevision];
    await renderLoaded();

    const releaseA = hold('/skills/runbook');
    const releaseB = hold('/skills/third-skill');
    fireEvent.click(screen.getAllByText('Runbook')[0]);
    fireEvent.click(screen.getAllByText('Third Skill')[0]);

    // The older request finishes first. It must not unlock the editor.
    releaseA();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByDisplayValue('third')).toBeNull();
    expect(screen.queryByDisplayValue('B two')).toBeNull();
    expect(screen.getByText('Loading skill details...')).toBeTruthy();
    const save = screen.getByRole('button', {
      name: 'Save skill',
    }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.click(save);
    for (const box of screen.getAllByRole('checkbox')) {
      fireEvent.click(box);
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(
      calls.filter((call) => call.method === 'PUT' || call.method === 'PATCH'),
    ).toEqual([]);

    // Once the selected skill's own detail arrives it can be saved, as itself.
    releaseB();
    await waitFor(() =>
      expect(screen.getByDisplayValue('third skill body')).toBeTruthy(),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Save skill' }));
    await waitFor(() =>
      expect(calls.some((call) => call.method === 'PUT')).toBe(true),
    );
    expect(calls.filter((call) => call.method === 'PUT')).toEqual([
      {
        path: '/skills/third-skill',
        method: 'PUT',
        body: {
          name: 'Third Skill',
          description: 'How to deploy',
          content: 'third skill body',
          enabled: true,
          expected_revision: 3,
        },
      },
    ]);
  });

  it('keeps the editor locked when the selected skill fails to load', async () => {
    skills = [account, { ...other, id: 'missing-skill', name: 'Missing' }];
    brokenDetail = '/skills/missing-skill';
    await renderLoaded();

    fireEvent.click(screen.getAllByText('Missing')[0]);
    await waitFor(() =>
      expect(screen.getByText(/Skill details are unavailable/)).toBeTruthy(),
    );
    expect(screen.queryByDisplayValue('third')).toBeNull();
    for (const name of ['Save skill', 'Delete']) {
      const button = screen.getByRole('button', { name }) as HTMLButtonElement;
      expect(button.disabled).toBe(true);
      fireEvent.click(button);
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(calls.filter((call) => call.method !== 'GET')).toEqual([]);
  });
});
