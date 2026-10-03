import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest';
import { useLayoutEffect } from 'react';
import { TextToSpeechButton } from '../components/TextToSpeechButton';
import { TtsPlaybackBar } from '../components/TtsPlaybackBar';
import {
  AudioPlaybackProvider,
  MAX_TTS_TEXT_CODE_POINTS,
  TTS_DECODE_ERROR_MESSAGE,
  TTS_PLAY_ERROR_MESSAGE,
  TTS_STALE_CONVERSATION_MESSAGE,
  TTS_TEXT_TOO_LONG_MESSAGE,
  useAudioPlayback,
} from '../components/AudioPlaybackProvider';
import { DEFAULT_TTS_SETTINGS } from '../lib/constants';
import { setTtsSettings } from '../lib/ttsSettings';

/**
 * These tests mount the REAL AudioPlaybackProvider. Nothing about the playback
 * lifecycle is mocked: synthesis, the protected download, object URLs, the
 * audio element, media events and play rejection all run through the provider
 * with a controlled fetch queue and a fake audio element.
 */

const authHarness = vi.hoisted(() => {
  const state = { generation: 0, listeners: new Set<() => void>() };
  return {
    state,
    ensureAuthHeader: vi.fn(async () => 'Bearer test-token'),
    getAuthGeneration: () => state.generation,
    subscribeAuthGeneration: (listener: () => void) => {
      state.listeners.add(listener);
      return () => {
        state.listeners.delete(listener);
      };
    },
    bump: () => {
      state.generation += 1;
      for (const listener of [...state.listeners]) listener();
    },
  };
});

vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: authHarness.ensureAuthHeader,
  getAuthGeneration: authHarness.getAuthGeneration,
  subscribeAuthGeneration: authHarness.subscribeAuthGeneration,
}));

interface MediaInstance {
  isConnected: boolean;
  src: string;
  playbackRate: number;
  currentTime: number;
  duration: number;
  paused: boolean;
  playCalls: number;
  pauseCalls: number;
  pause: () => void;
  loadCalls: number;
  onended: (() => void) | null;
  onerror: (() => void) | null;
  oncanplay: (() => void) | null;
  onplaying: (() => void) | null;
  onpause: (() => void) | null;
  onloadedmetadata: (() => void) | null;
  ondurationchange: (() => void) | null;
  ontimeupdate: (() => void) | null;
  onseeking: (() => void) | null;
  onseeked: (() => void) | null;
  resolvePlay: () => void;
  rejectPlay: (error: unknown) => void;
  canplay: () => void;
  playing: () => void;
  endPlayback: () => void;
  fail: () => void;
}

const media = vi.hoisted(() => {
  const instances: MediaInstance[] = [];
  const createdUrls: string[] = [];
  const revokedUrls: string[] = [];
  let objectUrlCounter = 0;

  interface PlayDeferred {
    promise: Promise<void>;
    resolve: () => void;
    reject: (error: unknown) => void;
  }

  // Each audio element owns its own play promise, exactly like a browser: a
  // rejection from a superseded element must not reach a newer element.
  const createPlayDeferred = (): PlayDeferred => {
    let resolve: () => void = () => {};
    let reject: (error: unknown) => void = () => {};
    const promise = new Promise<void>((res, rej) => {
      resolve = () => res();
      reject = rej;
    });
    // Nothing observes an unsettled promise until play() is called.
    promise.catch(() => {});
    return { promise, resolve, reject };
  };

  class FakeAudio {
    src: string;
    playbackRate = 1;
    currentTime = 0;
    duration = NaN;
    paused = true;
    playCalls = 0;
    pauseCalls = 0;
    loadCalls = 0;
    onended: (() => void) | null = null;
    onerror: (() => void) | null = null;
    oncanplay: (() => void) | null = null;
    onplaying: (() => void) | null = null;
    onpause: (() => void) | null = null;
    playDeferred: PlayDeferred = createPlayDeferred();

    constructor(src: string) {
      this.src = src;
      // Keep a real DOM node so provider attachment/retirement is exercised,
      // while only decoder events and the asynchronous play promise are fake.
      const element = document.createElement('audio');
      element.src = src;
      for (const [key, value] of Object.entries(this)) {
        if (key === 'src') continue;
        Object.defineProperty(element, key, {
          configurable: true,
          writable: true,
          value,
        });
      }
      for (const key of [
        'play',
        'pause',
        'load',
        'resolvePlay',
        'rejectPlay',
        'canplay',
        'playing',
        'endPlayback',
        'fail',
      ] as const) {
        Object.defineProperty(element, key, {
          value: FakeAudio.prototype[key],
        });
      }
      const instance = element as unknown as FakeAudio;
      instances.push(instance as unknown as MediaInstance);
      return instance;
    }

    play(): Promise<void> {
      this.playCalls += 1;
      if (this.playCalls > 1) this.playDeferred = createPlayDeferred();
      this.paused = false;
      return this.playDeferred.promise;
    }

    pause(): void {
      this.pauseCalls += 1;
      this.paused = true;
      this.onpause?.();
    }

    load(): void {
      this.loadCalls += 1;
    }

    resolvePlay(): void {
      this.playDeferred.resolve();
    }

    rejectPlay(error: unknown): void {
      this.playDeferred.reject(error);
    }

    canplay(): void {
      this.oncanplay?.();
    }

    /** Actual playback start, distinct from decoder readiness. */
    playing(): void {
      this.paused = false;
      this.onplaying?.();
    }

    endPlayback(): void {
      this.onended?.();
    }

    fail(): void {
      this.onerror?.();
    }
  }

  return {
    FakeAudio,
    instances,
    createdUrls,
    revokedUrls,
    reset() {
      instances.length = 0;
      createdUrls.length = 0;
      revokedUrls.length = 0;
      objectUrlCounter = 0;
    },
    createObjectURL: () => {
      objectUrlCounter += 1;
      const url = `blob:mock-audio/${objectUrlCounter}`;
      createdUrls.push(url);
      return url;
    },
    revokeObjectURL: (url: string) => {
      revokedUrls.push(url);
    },
    resolvePlay: () => {
      for (const instance of instances) instance.resolvePlay();
    },
    /** Decoder readiness alone must not report playback. */
    canplay: () => {
      for (const instance of instances) instance.canplay();
    },
    playing: () => {
      for (const instance of instances) instance.playing();
    },
    rejectPlay: (error: unknown) => {
      for (const instance of instances) instance.rejectPlay(error);
    },
    last: () => instances[instances.length - 1],
  };
});

interface PendingCall {
  url: string;
  init: RequestInit | undefined;
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
}

function createFetchController() {
  const calls: PendingCall[] = [];
  const mock = vi.fn(
    (input: RequestInfo | URL, init?: RequestInit) =>
      new Promise((resolve, reject) => {
        calls.push({
          url: String(input),
          init,
          resolve: resolve as (value: unknown) => void,
          reject,
        });
      }),
  );
  return { mock, calls };
}

function ttsResponse(
  audioPath: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    ok: true,
    status: 200,
    json: async () => ({ audio_path: audioPath, ...overrides }),
    blob: async () => ({}),
  };
}

function errorResponse(status: number, body: unknown) {
  return {
    ok: false,
    status,
    json: async () => body,
    blob: async () => ({}),
  };
}

/** A successful response whose artifact is absent or not a string. */
function missingArtifactResponse(body: Record<string, unknown> = {}) {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    blob: async () => ({}),
  };
}

function audioResponse(status: number) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => ({}),
    blob: async () => ({ size: 2048 }),
  };
}

const DEFAULT_SCOPE = { conversationId: 'conv-1', authGeneration: 0 };

interface HarnessButton {
  messageId: string;
  /** Distinguishes two mounted instances that share one message identity. */
  key?: string;
  conversationId?: string | null;
  text: string;
  available?: boolean;
}

interface HarnessProps {
  scope?: { conversationId: string | null; authGeneration: number };
  buttons: HarnessButton[];
  player?: boolean;
  captureControls?: (controls: ReturnType<typeof useAudioPlayback>) => void;
}

function ControlsProbe({
  capture,
}: {
  capture: NonNullable<HarnessProps['captureControls']>;
}) {
  const controls = useAudioPlayback();
  useLayoutEffect(() => capture(controls), [capture, controls]);
  return null;
}

function Harness({
  scope = DEFAULT_SCOPE,
  buttons,
  player = false,
  captureControls,
}: HarnessProps) {
  return (
    <AudioPlaybackProvider scope={scope}>
      {buttons.map((button) => (
        <TextToSpeechButton
          key={button.key ?? button.messageId}
          messageId={button.messageId}
          conversationId={
            button.conversationId === undefined
              ? 'conv-1'
              : button.conversationId
          }
          text={button.text}
          available={button.available ?? true}
        />
      ))}
      {player && <TtsPlaybackBar />}
      {captureControls && <ControlsProbe capture={captureControls} />}
    </AudioPlaybackProvider>
  );
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function resolveCall(call: PendingCall, value: unknown): Promise<void> {
  await act(async () => {
    call.resolve(value);
  });
  await flush();
}

async function rejectCall(call: PendingCall, error: unknown): Promise<void> {
  await act(async () => {
    call.reject(error);
  });
  await flush();
}

async function waitForCalls(
  mock: ReturnType<typeof createFetchController>['mock'],
  count: number,
) {
  await waitFor(() => expect(mock.mock.calls.length).toBe(count));
}

function buttons(label: string): HTMLElement[] {
  return screen.getAllByLabelText(label);
}

function buttonByLabel(label: string): HTMLButtonElement {
  return screen.getByLabelText(label) as HTMLButtonElement;
}

function stopText(): string {
  return buttonByLabel('Stop TTS').textContent ?? '';
}

/** The jsdom environment exposes no working localStorage; install a fake. */
function installFakeLocalStorage(): void {
  const store: Record<string, string> = {};
  const fakeStorage = {
    getItem: (key: string) =>
      Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null,
    setItem: (key: string, value: string) => {
      store[key] = String(value);
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      for (const key of Object.keys(store)) delete store[key];
    },
    key: (index: number) => Object.keys(store)[index] ?? null,
    get length() {
      return Object.keys(store).length;
    },
  };
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: fakeStorage,
  });
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: fakeStorage,
  });
}

beforeAll(installFakeLocalStorage);

beforeEach(() => {
  window.localStorage.clear();
  setTtsSettings({ ...DEFAULT_TTS_SETTINGS });
  media.reset();
  authHarness.state.generation = 0;
  authHarness.state.listeners.clear();
  authHarness.ensureAuthHeader
    .mockReset()
    .mockResolvedValue('Bearer test-token');
  vi.stubGlobal('Audio', media.FakeAudio);
  URL.createObjectURL =
    media.createObjectURL as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL =
    media.revokeObjectURL as unknown as typeof URL.revokeObjectURL;
  process.env.NEXT_PUBLIC_API_URL = 'http://localhost:8000';
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.NEXT_PUBLIC_API_URL;
});

async function startPlayer(captureControls?: HarnessProps['captureControls']) {
  const fetch = createFetchController();
  vi.stubGlobal('fetch', fetch.mock);
  const props: HarnessProps = {
    player: true,
    captureControls,
    buttons: [
      { messageId: 'm1', text: 'First speech' },
      { messageId: 'm2', text: 'Second speech' },
    ],
  };
  const view = render(<Harness {...props} />);
  expect(screen.queryByRole('region', { name: 'Speech player' })).toBeNull();
  fireEvent.click(buttons('Play TTS')[0]);
  expect(buttonByLabel('Pause speech').disabled).toBe(true);
  expect((screen.getByRole('slider') as HTMLInputElement).disabled).toBe(true);
  await flush();
  await resolveCall(fetch.calls[0], ttsResponse('/generated-audio/m1.mp3'));
  await resolveCall(fetch.calls[1], audioResponse(200));
  const audio = media.last();
  act(() => {
    audio.duration = 125;
    audio.onloadedmetadata?.();
    audio.playing();
    audio.resolvePlay();
  });
  await flush();
  return { ...view, ...fetch, props, audio };
}

it('pauses and resumes the same attached audio at its position without fetching again', async () => {
  const { audio, mock } = await startPlayer();
  act(() => {
    audio.currentTime = 12.4;
    audio.ontimeupdate?.();
  });
  expect(screen.getByText('0:12 / 2:05')).toBeTruthy();
  expect((screen.getByRole('slider') as HTMLInputElement).value).toBe('12.4');
  fireEvent.click(buttonByLabel('Pause speech'));
  expect(audio.paused).toBe(true);
  expect(audio.currentTime).toBe(12.4);
  expect(screen.getByText('Paused')).toBeTruthy();
  expect(buttonByLabel('Stop TTS').textContent).not.toContain('Loading');
  expect(audio.isConnected).toBe(true);
  expect(media.revokedUrls).toHaveLength(0);
  fireEvent.click(buttonByLabel('Resume speech'));
  act(() => {
    audio.playing();
    audio.resolvePlay();
  });
  await flush();
  expect(screen.getByText('Reading aloud')).toBeTruthy();
  expect(audio.currentTime).toBe(12.4);
  expect(audio.playCalls).toBe(2);
  expect(audio.playbackRate).toBe(1);
  expect(media.instances).toHaveLength(1);
  expect(mock).toHaveBeenCalledTimes(2);
});

it('seeks while playing and paused without resetting playback or speech preferences', async () => {
  const { audio, mock } = await startPlayer();
  const slider = screen.getByRole('slider') as HTMLInputElement;
  fireEvent.change(slider, { target: { value: '31.5' } });
  expect(audio.currentTime).toBe(31.5);
  expect(audio.paused).toBe(false);
  fireEvent.click(buttonByLabel('Pause speech'));
  fireEvent.change(slider, { target: { value: '7' } });
  expect(audio.currentTime).toBe(7);
  expect(audio.paused).toBe(true);
  act(() =>
    setTtsSettings({ ...DEFAULT_TTS_SETTINGS, speed: 2, format: 'wav' }),
  );
  expect(audio.currentTime).toBe(7);
  expect(audio.playbackRate).toBe(1);
  expect(mock).toHaveBeenCalledTimes(2);
});

it('keeps duration unknown until finite metadata and reconciles native media controls', async () => {
  const { audio } = await startPlayer();
  act(() => {
    audio.duration = Infinity;
    audio.ondurationchange?.();
    audio.currentTime = NaN;
    audio.ontimeupdate?.();
  });
  expect(screen.getByText('0:00 / —')).toBeTruthy();
  expect((screen.getByRole('slider') as HTMLInputElement).disabled).toBe(true);
  act(() => {
    audio.duration = 90;
    audio.currentTime = 4;
    audio.ondurationchange?.();
    audio.pause();
  });
  expect(screen.getByText('0:04 / 1:30')).toBeTruthy();
  expect(screen.getByLabelText('Resume speech')).toBeTruthy();
  act(() => audio.playing());
  expect(screen.getByLabelText('Pause speech')).toBeTruthy();
});

it('ignores a pending play rejection after pause and a late fulfillment while still paused', async () => {
  const { audio, mock } = await startPlayer();
  fireEvent.click(buttonByLabel('Pause speech'));
  fireEvent.click(buttonByLabel('Resume speech'));
  const lateReject = audio.rejectPlay.bind(audio);
  fireEvent.click(buttonByLabel('Pause speech'));
  await act(async () => lateReject(new DOMException('Paused', 'AbortError')));
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByLabelText('Resume speech')).toBeTruthy();
  fireEvent.click(buttonByLabel('Resume speech'));
  fireEvent.click(buttonByLabel('Pause speech'));
  act(() => audio.resolvePlay());
  await flush();
  expect(screen.getByText('Paused')).toBeTruthy();
  expect(audio.paused).toBe(true);
  expect(mock).toHaveBeenCalledTimes(2);
});

it('shows resume failures and releases the paused audio on close, end and scope change', async () => {
  const { audio, mock, calls, rerender, props } = await startPlayer();
  fireEvent.click(buttonByLabel('Pause speech'));
  fireEvent.click(buttonByLabel('Resume speech'));
  await act(async () => audio.rejectPlay(new Error('Resume denied')));
  expect(screen.getByRole('alert').textContent).toContain('Resume denied');
  expect(screen.queryByRole('region', { name: 'Speech player' })).toBeNull();
  expect(audio.isConnected).toBe(false);
  expect(media.revokedUrls).toHaveLength(1);

  fireEvent.click(buttonByLabel('Retry speech'));
  await flush();
  await resolveCall(calls[2], ttsResponse('/generated-audio/retry.mp3'));
  await resolveCall(calls[3], audioResponse(200));
  const retry = media.last();
  fireEvent.click(buttonByLabel('Pause speech'));
  fireEvent.click(buttonByLabel('Close speech player'));
  expect(retry.isConnected).toBe(false);
  expect(retry.ontimeupdate).toBeNull();
  expect(retry.onloadedmetadata).toBeNull();
  expect(screen.queryByRole('region', { name: 'Speech player' })).toBeNull();

  fireEvent.click(buttons('Play TTS')[0]);
  await flush();
  await resolveCall(calls[4], ttsResponse('/generated-audio/next.mp3'));
  await resolveCall(calls[5], audioResponse(200));
  const next = media.last();
  fireEvent.click(buttonByLabel('Pause speech'));
  rerender(
    <Harness
      {...props}
      scope={{ conversationId: 'conv-2', authGeneration: 0 }}
    />,
  );
  expect(next.isConnected).toBe(false);
  expect(screen.queryByRole('region', { name: 'Speech player' })).toBeNull();
  expect(mock).toHaveBeenCalledTimes(6);
});

it('does not let retired progress and media callbacks alter a replacement player', async () => {
  const { audio, calls } = await startPlayer();
  const staleTime = audio.ontimeupdate;
  const stalePause = audio.onpause;
  fireEvent.click(buttonByLabel('Pause speech'));
  fireEvent.click(buttonByLabel('Play TTS'));
  await flush();
  await resolveCall(calls[2], ttsResponse('/generated-audio/m2.mp3'));
  await resolveCall(calls[3], audioResponse(200));
  const replacement = media.last();
  act(() => {
    replacement.duration = 25;
    replacement.currentTime = 3;
    replacement.onloadedmetadata?.();
    replacement.playing();
    audio.currentTime = 110;
    staleTime?.();
    stalePause?.();
  });
  expect(screen.getByText('0:03 / 0:25')).toBeTruthy();
  expect(screen.getByText('Reading aloud')).toBeTruthy();
  expect(replacement.paused).toBe(false);
  act(() => replacement.endPlayback());
  expect(screen.queryByRole('region', { name: 'Speech player' })).toBeNull();
});

it('retires paused playback immediately when authentication is invalidated', async () => {
  const { audio } = await startPlayer();
  fireEvent.click(buttonByLabel('Pause speech'));
  act(() => authHarness.bump());
  expect(audio.isConnected).toBe(false);
  expect(media.revokedUrls).toHaveLength(1);
  expect(screen.queryByRole('region', { name: 'Speech player' })).toBeNull();
});

it('rejects stale request-qualified pause/resume/seek/close for a newer request of the same message', async () => {
  const captured: ReturnType<typeof useAudioPlayback>[] = [];
  const { calls, audio } = await startPlayer((controls) => {
    captured.push(controls);
  });
  const stale = captured.at(-1)!;
  const staleOwner = {
    messageId: stale.ownerMessageId!,
    conversationId: stale.ownerConversationId,
    requestId: stale.ownerRequestId,
  };
  act(() => {
    stale.startSpeech({
      ...staleOwner,
      text: 'First speech',
      voice: 'daemon-default',
      speed: 1,
      format: 'mp3',
    });
  });
  await flush();
  await resolveCall(
    calls[2],
    ttsResponse('/generated-audio/m1-replacement.mp3'),
  );
  await resolveCall(calls[3], audioResponse(200));
  const replacement = media.last();
  act(() => {
    replacement.duration = 25;
    replacement.currentTime = 3;
    replacement.onloadedmetadata?.();
    replacement.playing();
    stale.pauseSpeech(staleOwner);
    stale.seekSpeech(staleOwner, 20);
    stale.resumeSpeech(staleOwner);
    stale.stopSpeech(staleOwner);
  });
  expect(replacement.isConnected).toBe(true);
  expect(replacement.paused).toBe(false);
  expect(replacement.currentTime).toBe(3);
  expect(replacement.playCalls).toBe(1);
  expect(audio.isConnected).toBe(false);
  expect(screen.getByText('0:03 / 0:25')).toBeTruthy();
});

it('bounds seek inputs and reports a native seek exception with normal resource cleanup', async () => {
  const captured: ReturnType<typeof useAudioPlayback>[] = [];
  const { audio, mock } = await startPlayer((controls) => {
    captured.push(controls);
  });
  const controls = captured.at(-1)!;
  const owner = {
    messageId: controls.ownerMessageId!,
    conversationId: controls.ownerConversationId,
    requestId: controls.ownerRequestId,
  };
  act(() => controls.seekSpeech(owner, -1));
  expect(audio.currentTime).toBe(0);
  act(() => controls.seekSpeech(owner, 500));
  expect(audio.currentTime).toBe(125);
  act(() => {
    controls.seekSpeech(owner, NaN);
    controls.seekSpeech(owner, Infinity);
  });
  expect(audio.currentTime).toBe(125);
  Object.defineProperty(audio, 'currentTime', {
    configurable: true,
    get: () => 125,
    set: () => {
      throw new Error('Native seek rejected');
    },
  });
  act(() => controls.seekSpeech(owner, 10));
  expect(screen.getByRole('alert').textContent).toContain(
    'Speech position could not be changed',
  );
  expect(audio.isConnected).toBe(false);
  expect(media.revokedUrls).toHaveLength(1);
  expect(mock).toHaveBeenCalledTimes(2);
});

it('runs one provider-owned POST to protected download to Audio generation', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(<Harness buttons={[{ messageId: 'm1', text: 'Hello Daemon' }]} />);

  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);

  expect(calls[0].url).toBe('/api/tts');
  expect(JSON.parse(calls[0].init?.body as string)).toEqual({
    text: 'Hello Daemon',
    voice: 'daemon-default',
    speed: 1,
    format: 'mp3',
    cache: true,
  });
  expect(authHarness.ensureAuthHeader).toHaveBeenCalled();

  await resolveCall(calls[0], ttsResponse('/generated-audio/m1.mp3'));
  await waitForCalls(mock, 2);
  expect(calls[1].url).toBe('http://localhost:8000/generated-audio/m1.mp3');
  expect(media.instances).toHaveLength(0);

  await resolveCall(calls[1], audioResponse(200));
  expect(media.instances).toHaveLength(1);
  // Speed is synthesized once on the server: playback rate is untouched.
  expect(media.last().playbackRate).toBe(1);
  expect(media.last().src).toBe(media.createdUrls[0]);
  expect(media.last().playCalls).toBe(1);
  expect(media.last().isConnected).toBe(true);
  expect(media.revokedUrls).toHaveLength(0);
});

it('keeps media parented through rerenders and detaches only when retired', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  const props = { buttons: [{ messageId: 'm1', text: 'Attached speech' }] };
  const { rerender, unmount } = render(<Harness {...props} />);
  fireEvent.click(buttonByLabel('Play TTS'));
  await flush();
  await resolveCall(calls[0], ttsResponse('/generated-audio/m1.mp3'));
  await resolveCall(calls[1], audioResponse(200));
  const audio = media.last();
  expect(audio.isConnected).toBe(true);
  expect(document.querySelectorAll('audio')).toHaveLength(1);
  rerender(<Harness {...props} />);
  expect(audio.isConnected).toBe(true);
  fireEvent.click(buttonByLabel('Stop TTS'));
  expect(audio.isConnected).toBe(false);
  expect(audio.paused).toBe(true);
  expect(audio.loadCalls).toBe(1);
  expect(document.querySelectorAll('audio')).toHaveLength(0);
  await act(async () =>
    audio.rejectPlay(new DOMException('Removed', 'AbortError')),
  );
  expect(screen.queryByRole('alert')).toBeNull();
  unmount();
});

const pendingStages = [
  'post-auth',
  'post',
  'json',
  'download-auth',
  'download',
  'blob',
  'starting',
  'playing',
] as const;

it.each([
  [undefined, 'http://localhost:8000', 'mp3'],
  ['https://daemon.example', 'https://daemon.example', 'wav'],
  ['https://daemon.example/backend/', 'https://daemon.example/backend', 'opus'],
])(
  'always authenticates artifacts with API base %s',
  async (base, expected, format) => {
    if (base === undefined) delete process.env.NEXT_PUBLIC_API_URL;
    else process.env.NEXT_PUBLIC_API_URL = base;
    const { mock, calls } = createFetchController();
    vi.stubGlobal('fetch', mock);
    render(
      <Harness buttons={[{ messageId: 'm1', text: 'Protected speech' }]} />,
    );
    fireEvent.click(buttonByLabel('Play TTS'));
    await flush();
    await resolveCall(
      calls[0],
      ttsResponse(`/generated-audio/example.${format}`),
    );
    expect(calls[1].url).toBe(`${expected}/generated-audio/example.${format}`);
    expect(calls[1].init?.headers).toEqual({
      Authorization: 'Bearer test-token',
    });
    expect(calls[1].init?.signal).toBe(calls[0].init?.signal);
    expect(calls[1].init?.redirect).toBe('error');
    expect(media.instances).toHaveLength(0);
    await resolveCall(calls[1], audioResponse(200));
    expect(media.last().src).toBe(media.createdUrls[0]);
    expect(media.last().src).toMatch(/^blob:/);
  },
);

it.each([
  'https://evil.example/generated-audio/a.mp3',
  '//evil.example/generated-audio/a.mp3',
  '/generated-audio/../a.mp3',
  '/generated-audio/%2e%2e%2fa.mp3',
  '/generated-audio/\\evil.mp3',
  '/generated-audio/a.mp3?redirect=elsewhere',
  '/generated-audio/a.mp3#fragment',
  '/generated-audio/a.mp3\n',
  '/generated-images/a.mp3',
])(
  'rejects an unsupported artifact without forwarding auth: %s',
  async (path) => {
    const { mock, calls } = createFetchController();
    vi.stubGlobal('fetch', mock);
    render(
      <Harness buttons={[{ messageId: 'm1', text: 'Protected speech' }]} />,
    );
    fireEvent.click(buttonByLabel('Play TTS'));
    await flush();
    await resolveCall(calls[0], ttsResponse(path));
    expect(mock).toHaveBeenCalledTimes(1);
    expect(media.instances).toHaveLength(0);
    expect(screen.getByRole('alert').textContent).toContain(
      'Speech audio path was invalid',
    );
  },
);

it.each(
  pendingStages.flatMap((stage) =>
    (['replace', 'stop', 'unmount'] as const).map((action) => ({
      stage,
      action,
    })),
  ),
)(
  'retires $stage work on $action and ignores its late completion',
  async ({ stage, action }) => {
    const { mock, calls } = createFetchController();
    vi.stubGlobal('fetch', mock);
    let resolveGate!: (value: unknown) => void;
    const gate = new Promise<unknown>((resolve) => {
      resolveGate = resolve;
    });
    if (stage === 'post-auth') {
      authHarness.ensureAuthHeader.mockImplementationOnce(async () =>
        String(await gate),
      );
    } else if (stage === 'download-auth') {
      authHarness.ensureAuthHeader
        .mockResolvedValueOnce('Bearer test-token')
        .mockImplementationOnce(async () => String(await gate));
    }
    const initial = [
      { messageId: 'm1', text: 'Alpha' },
      { messageId: 'm2', text: 'Beta' },
    ];
    const view = render(<Harness buttons={initial} />);
    fireEvent.click(buttons('Play TTS')[0]);
    await flush();
    const oldPost = calls[0];
    if (!['post-auth', 'post'].includes(stage)) {
      await resolveCall(
        oldPost,
        stage === 'json'
          ? { ...ttsResponse('/generated-audio/a.mp3'), json: () => gate }
          : ttsResponse('/generated-audio/a.mp3'),
      );
    }
    const oldDownload = calls[1];
    if (['blob', 'starting', 'playing'].includes(stage)) {
      await resolveCall(
        oldDownload,
        stage === 'blob'
          ? { ...audioResponse(200), blob: () => gate }
          : audioResponse(200),
      );
    }
    const oldAudio = media.instances[0];
    const stalePlaying = oldAudio?.onplaying;
    const staleError = oldAudio?.onerror;
    if (stage === 'playing') {
      await act(async () => oldAudio.resolvePlay());
    }
    const oldMediaCount = media.instances.length;
    if (action === 'replace') {
      fireEvent.click(buttons('Play TTS')[0]);
      await flush();
      await resolveCall(calls.at(-1)!, ttsResponse('/generated-audio/b.mp3'));
      await resolveCall(calls.at(-1)!, audioResponse(200));
      await act(async () => media.last().resolvePlay());
    } else if (action === 'stop') {
      fireEvent.click(buttonByLabel('Stop TTS'));
    } else {
      view.rerender(<Harness buttons={[initial[1]]} />);
    }
    const callCount = calls.length;
    if (stage === 'post')
      await resolveCall(oldPost, ttsResponse('/generated-audio/a.mp3'));
    if (stage === 'download')
      await resolveCall(oldDownload, audioResponse(200));
    await act(async () => {
      resolveGate(
        stage.includes('auth')
          ? 'Bearer obsolete'
          : stage === 'json'
            ? { audio_path: '/generated-audio/a.mp3' }
            : { size: 2048 },
      );
      oldAudio?.resolvePlay();
      stalePlaying?.();
      staleError?.();
    });
    await flush();
    expect(calls).toHaveLength(callCount);
    expect(media.instances).toHaveLength(
      oldMediaCount + (action === 'replace' ? 1 : 0),
    );
    expect(screen.queryByRole('alert')).toBeNull();
    if (oldPost) expect(oldPost.init?.signal?.aborted).toBe(true);
    if (oldDownload) expect(oldDownload.init?.signal?.aborted).toBe(true);
    if (oldAudio) expect(oldAudio.paused).toBe(true);
    if (action === 'replace') {
      expect(media.last().pauseCalls).toBe(0);
      expect(buttonByLabel('Stop TTS')).toBeTruthy();
    } else {
      expect(screen.queryByLabelText('Stop TTS')).toBeNull();
    }
  },
);

it('refuses an old rendered sign-in scope before React commits the new scope', async () => {
  const { mock } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(
    <Harness buttons={[{ messageId: 'm1', text: 'Retired account text' }]} />,
  );
  act(() => authHarness.bump());
  // Deliberately leave the provider scope at generation zero.
  fireEvent.click(buttonByLabel('Play TTS'));
  await flush();
  expect(mock).not.toHaveBeenCalled();
  expect(media.instances).toHaveLength(0);
});

it('keeps a newer selection when an earlier synthesis completes later', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(
    <Harness
      buttons={[
        { messageId: 'm1', text: 'Alpha' },
        { messageId: 'm2', text: 'Beta' },
      ]}
    />,
  );

  const playButtons = buttons('Play TTS');
  fireEvent.click(playButtons[0]);
  await waitForCalls(mock, 1);

  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 2);
  expect(calls[0].init?.signal?.aborted).toBe(true);
  expect(calls[1].init?.signal?.aborted).toBe(false);

  // B completes first, then the superseded A POST resolves late.
  await resolveCall(calls[1], ttsResponse('/generated-audio/beta.mp3'));
  await waitForCalls(mock, 3);
  await resolveCall(calls[2], audioResponse(200));

  await resolveCall(calls[0], ttsResponse('/generated-audio/alpha.mp3'));
  await flush();

  expect(media.instances).toHaveLength(1);
  expect(media.last().src).toBe(media.createdUrls[0]);
  expect(media.createdUrls).toHaveLength(1);
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();
  expect(buttons('Play TTS')).toHaveLength(1);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('ignores a superseded play rejection and stale media events', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(
    <Harness
      buttons={[
        { messageId: 'm1', text: 'Alpha' },
        { messageId: 'm2', text: 'Beta' },
      ]}
    />,
  );

  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(calls[0], ttsResponse('/generated-audio/alpha.mp3'));
  await waitForCalls(mock, 2);
  await resolveCall(calls[1], audioResponse(200));
  expect(media.instances).toHaveLength(1);

  // B replaces A while A's play promise is still pending.
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 3);
  expect(media.instances[0].pauseCalls).toBe(1);
  expect(media.revokedUrls).toEqual([media.createdUrls[0]]);

  // Only the superseded element's play promise rejects; the new owner's
  // element must be untouched by it.
  media.instances[0].rejectPlay(new Error('A stale play rejection'));
  await flush();
  media.instances[0].fail();
  media.instances[0].endPlayback();
  await flush();

  await resolveCall(calls[2], ttsResponse('/generated-audio/beta.mp3'));
  await waitForCalls(mock, 4);
  await resolveCall(calls[3], audioResponse(200));
  media.last().resolvePlay();
  await act(async () => {
    media.last().canplay();
  });

  expect(media.instances).toHaveLength(2);
  expect(media.instances[1].playCalls).toBe(1);
  expect(media.last().pauseCalls).toBe(0);
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();
});

it('keeps Stop available and cancels work in every active phase', async () => {
  const synthesizing = createFetchController();
  vi.stubGlobal('fetch', synthesizing.mock);
  const view = render(
    <Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(synthesizing.mock, 1);
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();
  fireEvent.click(screen.getByLabelText('Stop TTS'));
  expect(synthesizing.calls[0].init?.signal?.aborted).toBe(true);
  expect(media.instances).toHaveLength(0);
  view.unmount();

  // Downloading: Stop aborts the protected download and creates no media.
  const downloading = createFetchController();
  vi.stubGlobal('fetch', downloading.mock);
  render(<Harness buttons={[{ messageId: 'm2', text: 'Beta' }]} />);
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(downloading.mock, 1);
  await resolveCall(
    downloading.calls[0],
    ttsResponse('/generated-audio/m2.mp3'),
  );
  await waitForCalls(downloading.mock, 2);
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();
  fireEvent.click(screen.getByLabelText('Stop TTS'));
  expect(downloading.calls[1].init?.signal?.aborted).toBe(true);
  expect(media.instances).toHaveLength(0);
  // Resolving the aborted download must stay inert.
  await resolveCall(downloading.calls[1], audioResponse(200));
  expect(media.instances).toHaveLength(0);

  // Starting: the audio element exists but play() is still pending.
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(downloading.mock, 3);
  await resolveCall(
    downloading.calls[2],
    ttsResponse('/generated-audio/m3.mp3'),
  );
  await waitForCalls(downloading.mock, 4);
  await resolveCall(downloading.calls[3], audioResponse(200));
  expect(media.instances).toHaveLength(1);
  expect(media.last().playCalls).toBe(1);
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();
  expect(stopText()).toContain('Loading');

  // Decoder readiness alone must not claim playback.
  await act(async () => {
    media.last().canplay();
  });
  expect(stopText()).toContain('Loading');

  // Playing: Stop remains and the loading affordance is gone.
  media.last().resolvePlay();
  await flush();
  await act(async () => {
    media.last().playing();
  });
  expect(stopText()).not.toContain('Loading');

  fireEvent.click(screen.getByLabelText('Stop TTS'));
  expect(media.last().pauseCalls).toBe(1);
  expect(media.last().onended).toBeNull();
  expect(media.last().onerror).toBeNull();
  expect(media.last().oncanplay).toBeNull();
  expect(media.last().onplaying).toBeNull();
  expect(media.revokedUrls).toEqual([media.createdUrls[0]]);
  expect(screen.getByLabelText('Play TTS')).toBeTruthy();
});

it('returns to idle when playback ends naturally', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(<Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />);
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(calls[0], ttsResponse('/generated-audio/m1.mp3'));
  await waitForCalls(mock, 2);
  media.resolvePlay();
  await resolveCall(calls[1], audioResponse(200));
  await act(async () => {
    media.last().canplay();
  });
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();

  await act(async () => {
    media.last().endPlayback();
  });
  expect(screen.getByLabelText('Play TTS')).toBeTruthy();
  expect(media.revokedUrls).toEqual([media.createdUrls[0]]);
});

it('cancels the owning button when the message unmounts', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  const view = render(
    <Harness
      buttons={[
        { messageId: 'm1', text: 'Alpha' },
        { messageId: 'm2', text: 'Beta' },
      ]}
    />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(calls[0], ttsResponse('/generated-audio/alpha.mp3'));
  await waitForCalls(mock, 2);
  media.resolvePlay();
  await resolveCall(calls[1], audioResponse(200));

  // Removing the owner cannot leave unowned speech behind.
  view.rerender(<Harness buttons={[{ messageId: 'm2', text: 'Beta' }]} />);
  expect(media.instances[0].pauseCalls).toBe(1);
  expect(media.revokedUrls).toEqual([media.createdUrls[0]]);
  expect(buttons('Play TTS')).toHaveLength(1);

  // A late completion after the owner disappeared must stay inert.
  await act(async () => {
    media.instances[0].endPlayback();
  });
  expect(media.instances[0].pauseCalls).toBe(1);
});

it('cancels a captured rendering when the message content grows', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  const view = render(
    <Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);

  view.rerender(
    <Harness buttons={[{ messageId: 'm1', text: 'Alpha and more content' }]} />,
  );
  expect(calls[0].init?.signal?.aborted).toBe(true);

  await resolveCall(calls[0], ttsResponse('/generated-audio/stale.mp3'));
  await flush();
  expect(media.instances).toHaveLength(0);
  expect(screen.getByLabelText('Play TTS')).toBeTruthy();
});

it('treats duplicate text with distinct message IDs as distinct owners', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  const view = render(
    <Harness
      buttons={[
        { messageId: 'm1', text: 'Same text' },
        { messageId: 'm2', text: 'Same text' },
      ]}
    />,
  );

  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(calls[0], ttsResponse('/generated-audio/first.mp3'));
  await waitForCalls(mock, 2);
  media.resolvePlay();
  await resolveCall(calls[1], audioResponse(200));
  expect(media.instances).toHaveLength(1);

  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 3);
  await resolveCall(calls[2], ttsResponse('/generated-audio/second.mp3'));
  await waitForCalls(mock, 4);
  await resolveCall(calls[3], audioResponse(200));
  expect(media.instances).toHaveLength(2);

  // The first button leaving must not stop the second button's speech.
  view.rerender(<Harness buttons={[{ messageId: 'm2', text: 'Same text' }]} />);
  expect(media.instances[0].pauseCalls).toBe(1);
  expect(media.instances[1].pauseCalls).toBe(0);
  expect(media.revokedUrls).toEqual([media.createdUrls[0]]);
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();
});

it('cancels committed conversation changes in both directions', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  const view = render(
    <Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(calls[0], ttsResponse('/generated-audio/m1.mp3'));
  await waitForCalls(mock, 2);
  media.resolvePlay();
  await resolveCall(calls[1], audioResponse(200));
  await act(async () => {
    media.last().canplay();
  });

  view.rerender(
    <Harness
      scope={{ conversationId: 'conv-2', authGeneration: 0 }}
      buttons={[{ messageId: 'm1', text: 'Alpha', conversationId: 'conv-2' }]}
    />,
  );
  expect(media.last().pauseCalls).toBe(1);
  expect(buttons('Play TTS')).toHaveLength(1);

  // null -> assigned conversation also cancels conservatively.
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 3);
  view.rerender(
    <Harness
      scope={{ conversationId: null, authGeneration: 0 }}
      buttons={[{ messageId: 'm1', text: 'Alpha', conversationId: null }]}
    />,
  );
  expect(calls[2].init?.signal?.aborted).toBe(true);
  expect(buttons('Play TTS')).toHaveLength(1);
});

it('cancels on auth invalidation before React rerenders the scope', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(<Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />);
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(calls[0], ttsResponse('/generated-audio/m1.mp3'));
  await waitForCalls(mock, 2);
  media.resolvePlay();
  await resolveCall(calls[1], audioResponse(200));
  await act(async () => {
    media.last().canplay();
  });
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();

  // No rerender: the imperative generation subscription must retire the owner.
  await act(async () => {
    authHarness.bump();
  });
  expect(media.last().pauseCalls).toBe(1);
  expect(media.revokedUrls).toEqual([media.createdUrls[0]]);
  expect(buttons('Play TTS')).toHaveLength(1);

  // A late rejection after invalidation must not raise a stale error.
  media.rejectPlay(new Error('stale'));
  await flush();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('cancels on auth invalidation while synthesis is in flight', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(<Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />);
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);

  await act(async () => {
    authHarness.bump();
  });
  expect(calls[0].init?.signal?.aborted).toBe(true);

  await resolveCall(calls[0], ttsResponse('/generated-audio/stale.mp3'));
  await flush();
  expect(media.instances).toHaveLength(0);
  expect(screen.getByLabelText('Play TTS')).toBeTruthy();
});

it('refuses speech for a message outside the committed conversation', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(
    <Harness
      buttons={[{ messageId: 'm1', text: 'Alpha', conversationId: 'conv-9' }]}
    />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await flush();
  expect(calls).toHaveLength(0);
  expect(screen.getByRole('alert').textContent).toContain(
    TTS_STALE_CONVERSATION_MESSAGE,
  );
});

it('surfaces a visible retryable error for every request failure', async () => {
  const cases: Array<{
    name: string;
    ok: boolean;
    status: number;
    body: unknown;
    expected: string;
  }> = [
    {
      name: 'unauthorized POST',
      ok: false,
      status: 401,
      body: { detail: 'Session expired' },
      expected: 'Session expired (401)',
    },
    {
      name: 'server error code',
      ok: false,
      status: 404,
      body: { detail: { code: 'voice_unavailable' } },
      expected: 'voice_unavailable (404)',
    },
    {
      name: 'pydantic detail array',
      ok: false,
      status: 422,
      body: {
        detail: [
          {
            type: 'string_too_long',
            loc: ['body', 'text'],
            msg: 'String should have at most 3000 characters',
          },
        ],
      },
      expected: 'body.text: String should have at most 3000 characters (422)',
    },
    {
      name: 'missing artifact',
      ok: true,
      status: 200,
      body: { cached: true },
      expected: 'Speech audio was not returned',
    },
  ];

  for (const testCase of cases) {
    const { mock, calls } = createFetchController();
    vi.stubGlobal('fetch', mock);
    const view = render(
      <Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />,
    );
    fireEvent.click(buttons('Play TTS')[0]);
    await waitForCalls(mock, 1);
    await resolveCall(
      calls[0],
      testCase.ok
        ? missingArtifactResponse(testCase.body as Record<string, unknown>)
        : errorResponse(testCase.status, testCase.body),
    );

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain(testCase.expected);
    expect(screen.getByLabelText('Retry speech')).toBeTruthy();
    expect(screen.queryByLabelText('Play TTS')).toBeNull();
    expect(media.instances).toHaveLength(0);

    // Retry starts a fresh request.
    fireEvent.click(screen.getByLabelText('Retry speech'));
    await waitForCalls(mock, 2);
    expect(calls[1].url).toBe('/api/tts');
    view.unmount();
  }
});

it('surfaces download, blob, decoder and play failures', async () => {
  const unauthorized = createFetchController();
  vi.stubGlobal('fetch', unauthorized.mock);
  const first = render(
    <Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(unauthorized.mock, 1);
  await resolveCall(
    unauthorized.calls[0],
    ttsResponse('/generated-audio/m1.mp3'),
  );
  await waitForCalls(unauthorized.mock, 2);
  await resolveCall(unauthorized.calls[1], audioResponse(401));
  expect(screen.getByRole('alert').textContent).toContain(
    'not authorized (401)',
  );
  first.unmount();

  const missing = createFetchController();
  vi.stubGlobal('fetch', missing.mock);
  const second = render(
    <Harness buttons={[{ messageId: 'm2', text: 'Beta' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(missing.mock, 1);
  await resolveCall(missing.calls[0], ttsResponse('/generated-audio/m2.mp3'));
  await waitForCalls(missing.mock, 2);
  await resolveCall(missing.calls[1], audioResponse(404));
  expect(screen.getByRole('alert').textContent).toContain(
    'no longer available (404)',
  );
  expect(screen.getAllByRole('alert')).toHaveLength(1);
  second.unmount();

  const blobFailure = createFetchController();
  vi.stubGlobal('fetch', blobFailure.mock);
  const third = render(
    <Harness buttons={[{ messageId: 'm3', text: 'Gamma' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(blobFailure.mock, 1);
  await resolveCall(
    blobFailure.calls[0],
    ttsResponse('/generated-audio/m3.mp3'),
  );
  await waitForCalls(blobFailure.mock, 2);
  await rejectCall(blobFailure.calls[1], new Error('blob decode failed'));
  expect(screen.getByRole('alert').textContent).toContain('blob decode failed');
  expect(media.instances).toHaveLength(0);
  third.unmount();

  const decoderFailure = createFetchController();
  vi.stubGlobal('fetch', decoderFailure.mock);
  const fourth = render(
    <Harness buttons={[{ messageId: 'm4', text: 'Delta' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(decoderFailure.mock, 1);
  await resolveCall(
    decoderFailure.calls[0],
    ttsResponse('/generated-audio/m4.mp3'),
  );
  await waitForCalls(decoderFailure.mock, 2);
  media.resolvePlay();
  await resolveCall(decoderFailure.calls[1], audioResponse(200));
  await act(async () => {
    media.last().fail();
  });
  expect(screen.getByRole('alert').textContent).toContain(
    TTS_DECODE_ERROR_MESSAGE,
  );
  expect(media.last().pauseCalls).toBe(1);
  expect(screen.getByLabelText('Retry speech')).toBeTruthy();
  fourth.unmount();

  const playFailure = createFetchController();
  vi.stubGlobal('fetch', playFailure.mock);
  const fifth = render(
    <Harness buttons={[{ messageId: 'm5', text: 'Epsilon' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(playFailure.mock, 1);
  await resolveCall(
    playFailure.calls[0],
    ttsResponse('/generated-audio/m5.mp3'),
  );
  await waitForCalls(playFailure.mock, 2);
  await resolveCall(playFailure.calls[1], audioResponse(200));
  await act(async () => {
    media.rejectPlay(new Error('autoplay blocked'));
  });
  await flush();
  expect(screen.getByRole('alert').textContent).toContain(
    TTS_PLAY_ERROR_MESSAGE,
  );
  expect(screen.getByRole('alert').textContent).toContain('autoplay blocked');
  fifth.unmount();
});

it('scopes errors and retries to the owning message', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(
    <Harness
      buttons={[
        { messageId: 'm1', text: 'Alpha' },
        { messageId: 'm2', text: 'Beta' },
      ]}
    />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(
    calls[0],
    errorResponse(503, { detail: { code: 'overloaded' } }),
  );

  expect(screen.getAllByRole('alert')).toHaveLength(1);
  expect(screen.getAllByLabelText('Retry speech')).toHaveLength(1);
  expect(buttons('Play TTS')).toHaveLength(1);

  fireEvent.click(screen.getByLabelText('Retry speech'));
  await waitForCalls(mock, 2);
  await resolveCall(calls[1], ttsResponse('/generated-audio/m1.mp3'));
  await waitForCalls(mock, 3);
  media.resolvePlay();
  await resolveCall(calls[2], audioResponse(200));
  await act(async () => {
    media.last().canplay();
  });
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();
});

it('cancels owned speech when the message becomes unavailable again', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  const view = render(
    <Harness buttons={[{ messageId: 'm1', text: 'Alpha' }]} />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(calls[0], ttsResponse('/generated-audio/m1.mp3'));
  await waitForCalls(mock, 2);
  media.resolvePlay();
  await resolveCall(calls[1], audioResponse(200));
  media.last().playing();
  await flush();

  // A message that starts streaming again owns no speech: Stop disappears.
  view.rerender(
    <Harness
      buttons={[{ messageId: 'm1', text: 'Alpha', available: false }]}
    />,
  );
  expect(media.instances[0].pauseCalls).toBe(1);
  expect(media.revokedUrls).toEqual([media.createdUrls[0]]);
  expect(buttonByLabel('Play TTS unavailable').disabled).toBe(true);
});

it('never releases speech it does not own, even with an identical identity', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  // Two mounted instances share one message identity; only one ever starts.
  const view = render(
    <Harness
      buttons={[
        { key: 'primary', messageId: 'm1', text: 'Alpha' },
        { key: 'ghost', messageId: 'm1', text: 'Alpha' },
      ]}
    />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  await resolveCall(calls[0], ttsResponse('/generated-audio/m1.mp3'));
  await waitForCalls(mock, 2);
  media.resolvePlay();
  await resolveCall(calls[1], audioResponse(200));
  media.last().playing();
  await flush();
  // Both instances reflect the single owner; neither owns a second request.
  expect(screen.getAllByLabelText('Stop TTS')).toHaveLength(2);
  expect(media.instances).toHaveLength(1);

  // The never-started twin unmounts: it owns no request and must not stop it.
  view.rerender(
    <Harness buttons={[{ key: 'primary', messageId: 'm1', text: 'Alpha' }]} />,
  );
  expect(media.instances[0].pauseCalls).toBe(0);
  expect(media.revokedUrls).toHaveLength(0);
  expect(screen.getByLabelText('Stop TTS')).toBeTruthy();

  // The real owner leaving does stop it.
  view.rerender(<Harness buttons={[]} />);
  expect(media.instances[0].pauseCalls).toBe(1);
  expect(media.revokedUrls).toEqual([media.createdUrls[0]]);
});

it('blocks speech above the raw Unicode code point limit without truncating', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  const atLimit = '😀'.repeat(MAX_TTS_TEXT_CODE_POINTS);
  // 3000 code points but 6000 UTF-16 units: the server counts code points.
  expect(atLimit.length).toBe(MAX_TTS_TEXT_CODE_POINTS * 2);

  const view = render(
    <Harness
      buttons={[
        { messageId: 'm1', text: atLimit },
        { messageId: 'm2', text: `${atLimit}😀` },
      ]}
    />,
  );
  fireEvent.click(buttons('Play TTS')[0]);
  await waitForCalls(mock, 1);
  expect(JSON.parse(calls[0].init?.body as string).text).toHaveLength(
    MAX_TTS_TEXT_CODE_POINTS * 2,
  );

  const disabled = view.container.querySelector(
    'button[aria-label="Play TTS unavailable"]',
  ) as HTMLButtonElement | null;
  expect(disabled).not.toBeNull();
  expect(disabled?.disabled).toBe(true);
  expect(disabled?.title).toBe(TTS_TEXT_TOO_LONG_MESSAGE);
  const statuses = screen.getAllByRole('status');
  expect(
    statuses.some((node) => node.textContent === TTS_TEXT_TOO_LONG_MESSAGE),
  ).toBe(true);
  expect(calls).toHaveLength(1);
});

it('keeps speech unavailable until the message is complete', async () => {
  const { mock, calls } = createFetchController();
  vi.stubGlobal('fetch', mock);
  render(
    <Harness
      buttons={[{ messageId: 'm1', text: 'Alpha', available: false }]}
    />,
  );
  const streaming = buttonByLabel('Play TTS unavailable');
  expect(streaming.disabled).toBe(true);
  expect(streaming.title).toContain('response is complete');
  fireEvent.click(streaming);
  await flush();
  expect(calls).toHaveLength(0);
});
