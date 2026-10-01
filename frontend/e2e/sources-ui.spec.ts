import { readFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';

// Real UI/server/SW integration with fictional account/snapshot content only.
const conversation = '11111111-1111-4111-8111-111111111111';
const first = '22222222-2222-4222-8222-000000000001';
const last = '22222222-2222-4222-8222-000000000021';

async function openSources(page: Page) {
  await page.goto(`/?id=${conversation}`);
  const composer = page.locator('textarea').first();
  await composer.fill('Unsent fictional Sources draft');
  await page
    .getByRole('button', { name: 'Open conversation details', exact: true })
    .click();
  const sources = page.getByRole('region', {
    name: 'Retained sources',
    exact: true,
  });
  await expect(sources.locator('[data-snapshot-id]')).toHaveCount(20);
  return sources;
}

test.beforeEach(async ({ request }) => {
  await request.get('/__fixture/reset');
});

test('external expiry leaves a truthful empty page with Previous navigation', async ({
  page,
  request,
}) => {
  const sources = await openSources(page);
  await sources.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(sources.locator('[data-snapshot-id]')).toHaveCount(1);
  await request.get('/__fixture/expire-last');
  await sources.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(sources).toContainText('No retained sources on this page.');
  await sources.getByRole('button', { name: 'Previous', exact: true }).click();
  await expect(sources.locator('[data-snapshot-id]')).toHaveCount(20);
});

for (const [width, theme] of [
  [1440, 'dark'],
  [390, 'light'],
] as const) {
  test(`Sources ${theme} ${width}: metadata, export, scoped confirmation, pagination and draft`, async ({
    page,
    request,
  }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push(message.text());
    });
    await page.setViewportSize({ width, height: 844 });
    await page.addInitScript(
      (value) => localStorage.setItem('daemon-theme', value),
      theme,
    );
    const sources = await openSources(page);
    const row = sources.locator(`[data-snapshot-id="${first}"]`);
    await expect(row).toContainText('Retrieved');
    await expect(row).toContainText('Expires');
    await expect(row.locator('a')).toHaveAttribute(
      'href',
      'https://example.org/fictional-source',
    );
    await expect(row.locator('a')).toHaveAttribute(
      'rel',
      'noopener noreferrer',
    );
    const downloaded = page.waitForEvent('download');
    await row
      .getByRole('button', { name: 'Export snapshot JSON', exact: true })
      .click();
    const download = await downloaded;
    expect(download.suggestedFilename()).toBe(`web-snapshot-${first}.json`);
    const filename = await download.path();
    expect(filename).not.toBeNull();
    const body = JSON.parse(await readFile(filename!, 'utf8'));
    expect(body.snapshot_id).toBe(first);
    expect(body.content).toBe('Fictional page text.');
    await expect(sources).not.toContainText('Fictional page text.');

    const remove = row.getByRole('button', {
      name: 'Remove retained snapshot',
      exact: true,
    });
    await remove.click();
    await expect(
      row.getByRole('button', { name: 'Cancel', exact: true }),
    ).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(remove).toBeFocused();
    await expect(
      page.getByRole('heading', { name: 'Conversation details', exact: true }),
    ).toBeVisible();
    expect(
      (await (await request.get('/__fixture/status')).json()).deletes,
    ).toBe(0);

    await sources.getByRole('button', { name: 'Next', exact: true }).click();
    await expect(sources.locator('[data-snapshot-id]')).toHaveCount(1);
    const lastRow = sources.locator(`[data-snapshot-id="${last}"]`);
    await lastRow
      .getByRole('button', { name: 'Remove retained snapshot', exact: true })
      .click();
    await expect(lastRow).toContainText(
      'original website and this conversation transcript are unchanged',
    );
    await lastRow
      .getByRole('button', { name: 'Remove snapshot', exact: true })
      .click();
    await expect(sources.locator('[data-snapshot-id]')).toHaveCount(20);
    await expect(
      sources.getByRole('button', { name: 'Next', exact: true }),
    ).toBeDisabled();
    expect(
      (await (await request.get('/__fixture/status')).json()).deletes,
    ).toBe(1);
    await page
      .getByRole('button', { name: 'Close conversation details', exact: true })
      .click();
    await expect(page.locator('textarea').first()).toHaveValue(
      'Unsent fictional Sources draft',
    );
    expect(errors).toEqual([]);
  });
}

test('unavailable export and uncertain accepted removal stay truthful and never replay', async ({
  page,
  request,
}) => {
  const sources = await openSources(page);
  const row = sources.locator(`[data-snapshot-id="${first}"]`);
  await request.get('/__fixture/failure?status=404&kind=export');
  await row
    .getByRole('button', { name: 'Export snapshot JSON', exact: true })
    .click();
  await expect(sources).toContainText(
    'Snapshot export is unavailable right now.',
  );
  await request.get('/__fixture/failure?unknownDelete=true');
  await row
    .getByRole('button', { name: 'Remove retained snapshot', exact: true })
    .click();
  await row
    .getByRole('button', { name: 'Remove snapshot', exact: true })
    .click();
  await expect(sources).toContainText('The removal outcome is unknown.');
  await expect(
    row.getByRole('button', { name: 'Remove retained snapshot', exact: true }),
  ).toBeDisabled();
  expect((await (await request.get('/__fixture/status')).json()).deletes).toBe(
    1,
  );
  await sources.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(row).toHaveCount(0);
  expect((await (await request.get('/__fixture/status')).json()).deletes).toBe(
    1,
  );
  await page
    .getByRole('button', { name: 'Close conversation details', exact: true })
    .click();
  await expect(page.locator('textarea').first()).toHaveValue(
    'Unsent fictional Sources draft',
  );
});
