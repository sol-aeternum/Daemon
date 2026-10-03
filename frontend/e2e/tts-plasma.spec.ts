import { expect, test } from '@playwright/test';

// Mirrors KDE's document-discovery hook, not the rest of the extension:
// https://github.com/KDE/plasma-browser-integration/blob/a98771a68f4d4c49aaa578eb619bad33f4c80c83/extension/page-script.js
// Detached new Audio() players are temporarily inserted then removed on play.
// Chromium rejects their original play promise as "media was removed".
test.beforeEach(async ({ context }) => {
  await context.addInitScript(() => {
    const state = {
      audio: [] as HTMLAudioElement[],
      detachedRemovals: 0,
      extensionReplays: 0,
      stopOnNextPlay: false,
      pauseOnNextPlay: false,
    };
    Object.assign(window, { __plasmaSpeech: state });
    const NativeAudio = window.Audio;
    window.Audio = function (source?: string) {
      const audio = new NativeAudio(source);
      state.audio.push(audio);
      const replay = () => {
        if (audio.dataset.pbiPausedForDomRemoval !== 'true') return;
        delete audio.dataset.pbiPausedForDomRemoval;
        state.extensionReplays++;
        void audio.play().catch(() => {});
      };
      const register = () => {
        audio.dataset.pbiPausedForDomRemoval = 'true';
        audio.removeEventListener('play', register);
        if (document.documentElement.contains(audio) || audio.parentNode) {
          delete audio.dataset.pbiPausedForDomRemoval;
          audio.removeEventListener('pause', replay);
        } else {
          state.detachedRemovals++;
          (document.head || document.documentElement).appendChild(audio);
          audio.parentNode!.removeChild(audio);
        }
      };
      audio.addEventListener('play', register);
      audio.addEventListener('pause', replay);
      const nativePlay = audio.play.bind(audio);
      audio.play = () => {
        const promise = nativePlay();
        if (state.stopOnNextPlay) {
          state.stopOnNextPlay = false;
          queueMicrotask(() =>
            document
              .querySelector<HTMLButtonElement>('button[aria-label="Stop TTS"]')
              ?.click(),
          );
        }
        if (state.pauseOnNextPlay) {
          state.pauseOnNextPlay = false;
          // Native/extension control: React may not have committed the enabled
          // Pause button yet. Pause the pending native play, not a disabled UI.
          queueMicrotask(() => audio.pause());
        }
        return promise;
      };
      return audio;
    } as unknown as typeof Audio;
    window.Audio.prototype = NativeAudio.prototype;
    Reflect.deleteProperty(Navigator.prototype, 'serviceWorker');
  });

  const rate = 24000;
  const count = rate * 3;
  const wav = Buffer.alloc(44 + count * 2);
  wav.write('RIFF');
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24);
  wav.writeUInt32LE(rate * 2, 28);
  wav.writeUInt16LE(2, 32);
  wav.writeUInt16LE(16, 34);
  wav.write('data', 36);
  wav.writeUInt32LE(count * 2, 40);
  for (let i = 0; i < count; i++) {
    wav.writeInt16LE(
      Math.round(Math.sin((i * 2 * Math.PI * 220) / rate) * 1000),
      44 + i * 2,
    );
  }
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const headers = {
      'Access-Control-Allow-Origin':
        route.request().headers().origin || url.origin,
      'Access-Control-Allow-Credentials': 'true',
      'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    };
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers });
    }
    const json = (value: unknown) => route.fulfill({ json: value, headers });
    if (p.endsWith('/auth/config'))
      return json({
        mode: 'self_hosted',
        email: { enabled: false },
        google: { enabled: false },
      });
    if (p.endsWith('/auth/refresh'))
      return json({ access_token: 'fictional-plasma-token', expires_in: 3600 });
    if (p.endsWith('/entitlements'))
      return json({ plan: 'free', capabilities: ['chat'], limits: {} });
    const conversation = {
      id: 'plasma-fixture',
      title: 'Plasma fixture',
      status: 'active',
      pinned: false,
      metadata: {},
      created_at: '2026-10-03T00:00:00Z',
      updated_at: '2026-10-03T00:00:00Z',
    };
    if (p === '/conversations') return json({ conversations: [conversation] });
    if (p === '/conversations/plasma-fixture')
      return json({
        ...conversation,
        messages: [
          { id: 'one', role: 'assistant', content: '**First** speech.' },
          { id: 'two', role: 'assistant', content: 'Second speech.' },
        ],
      });
    if (p === '/users/me/settings') return json({});
    if (p === '/v1/catalog')
      return json({
        auto: { id: 'auto', name: 'Auto', tagline: 'Auto', icon: 'zap' },
        featured: [],
      });
    if (p === '/v1/models') return json({ data: [] });
    if (p === '/api/tts')
      return json({ audio_path: '/generated-audio/plasma.wav' });
    if (p === '/generated-audio/plasma.wav')
      return route.fulfill({ body: wav, contentType: 'audio/wav', headers });
    if (
      p.startsWith('/api/') ||
      p.startsWith('/v1/') ||
      p.startsWith('/users/') ||
      p.startsWith('/conversations')
    )
      return route.fulfill({
        status: 403,
        json: { detail: { code: 'fictional_unavailable' } },
      });
    if (
      ['127.0.0.1', 'localhost'].includes(url.hostname) &&
      url.port !== '8000'
    )
      return route.continue();
    return route.abort();
  });
});

interface PlasmaFixtureState {
  audio: HTMLAudioElement[];
  detachedRemovals: number;
  extensionReplays: number;
  stopOnNextPlay: boolean;
  pauseOnNextPlay: boolean;
}

test('bottom player pauses, seeks and resumes one native audio without another request', async ({
  page,
}, testInfo) => {
  const requests = { speech: 0, download: 0 };
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (path === '/api/tts') requests.speech++;
    if (path === '/generated-audio/plasma.wav') requests.download++;
  });
  await page.goto('/?id=plasma-fixture');
  await expect(page.getByRole('region', { name: 'Speech player' })).toHaveCount(
    0,
  );
  await page
    .getByRole('button', { name: 'Play TTS', exact: true })
    .first()
    .click();
  const player = page.getByRole('region', { name: 'Speech player' });
  const slider = player.getByRole('slider', {
    name: 'Speech playback position',
  });
  await expect(slider).toBeEnabled();
  await expect(
    player.getByText('Reading aloud', { exact: true }),
  ).toBeVisible();
  await player
    .getByRole('button', { name: 'Pause speech', exact: true })
    .click();
  await expect(player.getByText('Paused', { exact: true })).toBeVisible();
  const playerBox = await player.boundingBox();
  const composerBox = await page
    .getByRole('textbox', { name: 'Message Daemon' })
    .boundingBox();
  expect(playerBox).not.toBeNull();
  expect(composerBox).not.toBeNull();
  expect(playerBox!.y + playerBox!.height).toBeLessThanOrEqual(composerBox!.y);
  const viewport = page.viewportSize()!;
  expect(playerBox!.x).toBeGreaterThanOrEqual(0);
  expect(playerBox!.x + playerBox!.width).toBeLessThanOrEqual(viewport.width);
  await page.screenshot({ path: testInfo.outputPath('paused-player.png') });
  const time = await page
    .locator('audio')
    .evaluate((audio) => (audio as HTMLAudioElement).currentTime);
  await page.waitForTimeout(350); // Prove the native playback clock is paused.
  expect(
    await page
      .locator('audio')
      .evaluate((audio) => (audio as HTMLAudioElement).currentTime),
  ).toBe(time);
  await slider.focus();
  await slider.press('Home');
  for (let i = 0; i < 10; i++) await slider.press('ArrowRight');
  await expect
    .poll(() =>
      page
        .locator('audio')
        .evaluate((audio) => (audio as HTMLAudioElement).currentTime),
    )
    .toBeCloseTo(1, 1);
  await expect(player.getByText('Paused', { exact: true })).toBeVisible();
  await player
    .getByRole('button', { name: 'Resume speech', exact: true })
    .click();
  await expect
    .poll(() =>
      page
        .locator('audio')
        .evaluate((audio) => (audio as HTMLAudioElement).currentTime),
    )
    .toBeGreaterThan(1);
  const native = await page.evaluate(() => {
    const state = (window as unknown as { __plasmaSpeech: PlasmaFixtureState })
      .__plasmaSpeech;
    return {
      count: state.audio.length,
      detached: state.detachedRemovals,
      replays: state.extensionReplays,
      attached: state.audio[0].isConnected,
    };
  });
  expect(native).toEqual({ count: 1, detached: 0, replays: 0, attached: true });
  expect(requests).toEqual({ speech: 1, download: 1 });
  await player.getByRole('button', { name: 'Close speech player' }).click();
  await expect(player).toHaveCount(0);
  await expect(page.locator('audio')).toHaveCount(0);
});

test('native pause before the initial play promise settles retains resumable audio', async ({
  page,
}) => {
  await page.goto('/?id=plasma-fixture');
  await page.evaluate(() => {
    (
      window as unknown as { __plasmaSpeech: PlasmaFixtureState }
    ).__plasmaSpeech.pauseOnNextPlay = true;
  });
  await page
    .getByRole('button', { name: 'Play TTS', exact: true })
    .first()
    .click();
  await expect(
    page.getByRole('button', { name: 'Resume speech' }),
  ).toBeVisible();
  await expect(page.locator('audio')).toHaveCount(1);
  await expect(page.locator('span[role="alert"]')).toHaveCount(0);
  await page.getByRole('button', { name: 'Resume speech' }).click();
  await expect
    .poll(() =>
      page
        .locator('audio')
        .evaluate((audio) => (audio as HTMLAudioElement).currentTime),
    )
    .toBeGreaterThan(0);
  await page.getByRole('button', { name: 'Close speech player' }).click();
  await expect(page.locator('audio')).toHaveCount(0);
});

test('plays with Plasma integration and cleans up on replacement, Stop and end', async ({
  page,
}) => {
  await page.goto('/?id=plasma-fixture');
  await page
    .getByRole('button', { name: 'Play TTS', exact: true })
    .first()
    .click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { __plasmaSpeech: PlasmaFixtureState })
            .__plasmaSpeech.audio[0]?.currentTime ?? 0,
      ),
    )
    .toBeGreaterThan(0);
  await expect(page.locator('audio')).toHaveCount(1);
  await page.getByRole('button', { name: 'Play TTS', exact: true }).click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { __plasmaSpeech: PlasmaFixtureState })
            .__plasmaSpeech.audio[1]?.currentTime ?? 0,
      ),
    )
    .toBeGreaterThan(0);
  await expect(page.locator('audio')).toHaveCount(1);
  await page.getByRole('button', { name: 'Stop TTS', exact: true }).click();
  await expect(page.locator('audio')).toHaveCount(0);
  await page
    .getByRole('button', { name: 'Play TTS', exact: true })
    .first()
    .click();
  await expect(
    page.getByRole('button', { name: 'Stop TTS', exact: true }),
  ).toBeVisible();
  await expect(page.locator('audio')).toHaveCount(1);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { __plasmaSpeech: PlasmaFixtureState })
            .__plasmaSpeech.audio[2]?.currentTime ?? 0,
      ),
    )
    .toBeGreaterThan(0);
  await expect(page.locator('audio')).toHaveCount(0, { timeout: 10000 });
  const state = await page.evaluate(() => {
    const s = (window as unknown as { __plasmaSpeech: PlasmaFixtureState })
      .__plasmaSpeech;
    return {
      detached: s.detachedRemovals,
      replays: s.extensionReplays,
      paused: s.audio.every((a) => a.paused),
      connected: s.audio.some((a) => a.isConnected),
    };
  });
  expect(state).toEqual({
    detached: 0,
    replays: 0,
    paused: true,
    connected: false,
  });
  // Next's route announcer also has role=alert; assert speech's inline errors.
  await expect(page.locator('span[role="alert"]')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Retry speech' })).toHaveCount(
    0,
  );
});

test('Stop before the native play event cannot resurrect a detached player', async ({
  page,
}) => {
  await page.goto('/?id=plasma-fixture');
  await page.evaluate(() => {
    (
      window as unknown as { __plasmaSpeech: PlasmaFixtureState }
    ).__plasmaSpeech.stopOnNextPlay = true;
  });
  await page
    .getByRole('button', { name: 'Play TTS', exact: true })
    .first()
    .click();
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          (window as unknown as { __plasmaSpeech: PlasmaFixtureState })
            .__plasmaSpeech.audio.length,
      ),
    )
    .toBe(1);
  await expect(page.locator('audio')).toHaveCount(0);
  await page.waitForTimeout(300); // Let the extension's queued play/pause tasks run.
  const state = await page.evaluate(() => {
    const s = (window as unknown as { __plasmaSpeech: PlasmaFixtureState })
      .__plasmaSpeech;
    return {
      detached: s.detachedRemovals,
      replays: s.extensionReplays,
      paused: s.audio.every((a) => a.paused),
    };
  });
  expect(state).toEqual({ detached: 0, replays: 0, paused: true });
  await expect(page.locator('span[role="alert"]')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Retry speech' })).toHaveCount(
    0,
  );
});
