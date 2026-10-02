import { act, fireEvent, render, screen } from '@testing-library/react';
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest';
import { TextToSpeechButton } from '../components/TextToSpeechButton';
import { AudioPlaybackProvider } from '../components/AudioPlaybackProvider';
import { useTtsSettings } from '../hooks/useTtsSettings';
import { DEFAULT_TTS_SETTINGS } from '../lib/constants';
import {
  getTtsSettingsSnapshot,
  normalizeTtsSettings,
  setTtsSettings,
  subscribeTtsSettings,
  TTS_SETTINGS_STORAGE_KEY,
} from '../lib/ttsSettings';

/**
 * Read-aloud preference source: validation, reactivity and notification.
 *
 * The active key stays `tts_settings` and the only readers are VoiceTab and the
 * play buttons. A dedicated subscribed store replaces the per-instance
 * `useLocalStorage` snapshot so already-mounted buttons observe changes.
 */

const authHarness = vi.hoisted(() => ({
  ensureAuthHeader: vi.fn(async () => 'Bearer test-token'),
}));

vi.mock('@/lib/auth', () => ({
  ensureAuthHeader: authHarness.ensureAuthHeader,
  getAuthGeneration: () => 0,
  subscribeAuthGeneration: () => () => {},
}));

/**
 * This environment exposes no usable `localStorage` (Node's experimental global
 * shadows jsdom's), so a fake store is installed, matching the repo convention.
 * `window.sessionStorage` remains a genuine jsdom `Storage`, which is what a
 * non-local `storageArea` must be made of.
 */
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
  } as Storage;
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: fakeStorage,
  });
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: fakeStorage,
  });
}

/** A change in another tab of this origin's local storage. */
function localEvent(key: string | null, newValue: string | null): StorageEvent {
  return new StorageEvent('storage', {
    key: key as string,
    newValue,
    oldValue: null,
  });
}

/** A session-storage change: a real jsdom Storage area, not local storage. */
function sessionEvent(key: string, newValue: string | null): StorageEvent {
  const event = new StorageEvent('storage', {
    key,
    newValue,
    oldValue: null,
  });
  // Vitest's hybrid Window rejects jsdom Storage in this constructor's realm.
  // Supply the event field explicitly; the handler still sees the other area.
  Object.defineProperty(event, 'storageArea', { value: window.sessionStorage });
  return event;
}

function writeRaw(value: string): void {
  window.localStorage.setItem(TTS_SETTINGS_STORAGE_KEY, value);
}

function notifyStorage(key: string, newValue: string | null): void {
  act(() => {
    window.dispatchEvent(localEvent(key, newValue));
  });
}

/** A whole-storage clear in another tab reports `key === null`. */
function notifyStorageCleared(): void {
  act(() => {
    window.dispatchEvent(localEvent(null, null));
  });
}

let local: Storage;

beforeAll(() => {
  installFakeLocalStorage();
  local = window.localStorage;
});

beforeEach(() => {
  local.clear();
  window.sessionStorage.clear();
  // A pristine, explicitly-written baseline for each test.
  setTtsSettings({ ...DEFAULT_TTS_SETTINGS });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function Probe({ onChange }: { onChange?: (value: unknown) => void }) {
  const { value } = useTtsSettings();
  onChange?.(value);
  return <span data-testid="probe">{value.speed}</span>;
}

describe('normalizeTtsSettings', () => {
  it('falls back to defaults for malformed and non-object payloads', () => {
    for (const raw of ['[]', 'null', '"a string"', '42']) {
      expect(normalizeTtsSettings(JSON.parse(raw))).toEqual(
        DEFAULT_TTS_SETTINGS,
      );
    }
    // Unparseable text and non-object values must not throw.
    expect(normalizeTtsSettings('not json at all')).toEqual(
      DEFAULT_TTS_SETTINGS,
    );
    expect(normalizeTtsSettings(undefined)).toEqual(DEFAULT_TTS_SETTINGS);
    expect(normalizeTtsSettings(null)).toEqual(DEFAULT_TTS_SETTINGS);
  });

  it('merges a partial stored object onto the defaults', () => {
    expect(normalizeTtsSettings({ speed: 1.5 })).toEqual({
      ...DEFAULT_TTS_SETTINGS,
      speed: 1.5,
    });
    expect(normalizeTtsSettings({ enabled: false, format: 'opus' })).toEqual({
      ...DEFAULT_TTS_SETTINGS,
      enabled: false,
      format: 'opus',
    });
  });

  it('requires actual booleans and preserves valid ones', () => {
    expect(normalizeTtsSettings({ enabled: 'yes' }).enabled).toBe(true);
    expect(normalizeTtsSettings({ enabled: 0 }).enabled).toBe(true);
    expect(normalizeTtsSettings({ autoPlay: 'true' }).autoPlay).toBe(false);
    expect(normalizeTtsSettings({ enabled: false }).enabled).toBe(false);
    expect(normalizeTtsSettings({ autoPlay: true }).autoPlay).toBe(true);
  });

  it('accepts only finite in-range speeds', () => {
    expect(normalizeTtsSettings({ speed: 0.5 }).speed).toBe(0.5);
    expect(normalizeTtsSettings({ speed: 2 }).speed).toBe(2);
    expect(normalizeTtsSettings({ speed: '1.5' }).speed).toBe(1);
    expect(normalizeTtsSettings({ speed: Number.NaN }).speed).toBe(1);
    expect(
      normalizeTtsSettings({ speed: Number.POSITIVE_INFINITY }).speed,
    ).toBe(1);
    expect(normalizeTtsSettings({ speed: 0.49 }).speed).toBe(1);
    expect(normalizeTtsSettings({ speed: 2.01 }).speed).toBe(1);
  });

  it('accepts only the supported audio formats', () => {
    for (const format of ['mp3', 'wav', 'opus']) {
      expect(normalizeTtsSettings({ format }).format).toBe(format);
    }
    for (const format of ['flac', 'mp3_44100_128', 'MP3', 7, null]) {
      expect(normalizeTtsSettings({ format }).format).toBe('mp3');
    }
  });

  it('maps legacy and unknown voices to the single supported voice', () => {
    expect(normalizeTtsSettings({ voice: 'daemon-default' }).voice).toBe(
      'daemon-default',
    );
    expect(normalizeTtsSettings({ voice: 'rachel' }).voice).toBe(
      'daemon-default',
    );
    expect(normalizeTtsSettings({ voice: 'Xb7hH8MSUJpSbSDYk0k2' }).voice).toBe(
      'daemon-default',
    );
    expect(normalizeTtsSettings({ voice: 'allay' }).voice).toBe(
      'daemon-default',
    );
    // An unsupported name is never forwarded to the server.
    expect(normalizeTtsSettings({ voice: 'custom-clone' }).voice).toBe(
      'daemon-default',
    );
    expect(normalizeTtsSettings({ voice: 42 }).voice).toBe('daemon-default');
  });

  it('keeps a legacy model label but never an empty one', () => {
    expect(
      normalizeTtsSettings({ model: 'eleven_multilingual_v2' }).model,
    ).toBe('eleven_multilingual_v2');
    expect(normalizeTtsSettings({ model: '   ' }).model).toBe(
      DEFAULT_TTS_SETTINGS.model,
    );
  });
});

describe('reactive store', () => {
  it('notifies every subscriber in the same tab from the setter', () => {
    const first = vi.fn();
    const second = vi.fn();
    const unsubscribeFirst = subscribeTtsSettings(first);
    const unsubscribeSecond = subscribeTtsSettings(second);

    act(() => {
      setTtsSettings((previous) => ({ ...previous, speed: 1.5 }));
    });

    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
    expect(getTtsSettingsSnapshot().speed).toBe(1.5);
    expect(
      JSON.parse(window.localStorage.getItem(TTS_SETTINGS_STORAGE_KEY) ?? '{}'),
    ).toMatchObject({ speed: 1.5, voice: 'daemon-default', format: 'mp3' });

    unsubscribeFirst();
    unsubscribeSecond();
  });

  it('renders every mounted consumer with the same validated value', () => {
    const first = render(<Probe />);
    const second = render(<Probe />);
    expect(
      first.container.querySelector('[data-testid="probe"]')?.textContent,
    ).toBe('1');
    expect(
      second.container.querySelector('[data-testid="probe"]')?.textContent,
    ).toBe('1');

    act(() => {
      setTtsSettings((previous) => ({ ...previous, speed: 0.5 }));
    });
    expect(
      first.container.querySelector('[data-testid="probe"]')?.textContent,
    ).toBe('0.5');
    expect(
      second.container.querySelector('[data-testid="probe"]')?.textContent,
    ).toBe('0.5');
  });

  it('adopts a cross-tab change without writing it back', () => {
    render(<Probe />);
    writeRaw(JSON.stringify({ speed: 1.75, format: 'wav' }));
    const before = window.localStorage.getItem(TTS_SETTINGS_STORAGE_KEY);

    notifyStorage(
      TTS_SETTINGS_STORAGE_KEY,
      window.localStorage.getItem(TTS_SETTINGS_STORAGE_KEY),
    );

    expect(getTtsSettingsSnapshot()).toEqual({
      ...DEFAULT_TTS_SETTINGS,
      speed: 1.75,
      format: 'wav',
    });
    // Read-side normalization must not rewrite the stored value.
    expect(window.localStorage.getItem(TTS_SETTINGS_STORAGE_KEY)).toBe(before);
  });

  it('normalizes a corrupt value written by another tab without persisting it', () => {
    render(<Probe />);
    writeRaw('{"speed":"fast","format":"flac","voice":"rachel"}');
    const corrupt = window.localStorage.getItem(TTS_SETTINGS_STORAGE_KEY);

    notifyStorage(TTS_SETTINGS_STORAGE_KEY, corrupt);

    expect(getTtsSettingsSnapshot()).toEqual(DEFAULT_TTS_SETTINGS);
    expect(window.localStorage.getItem(TTS_SETTINGS_STORAGE_KEY)).toBe(corrupt);
  });

  it('returns to defaults when another tab clears the key or all storage', () => {
    render(<Probe />);
    act(() => {
      setTtsSettings((previous) => ({ ...previous, speed: 1.5 }));
    });
    expect(getTtsSettingsSnapshot().speed).toBe(1.5);

    writeRaw(JSON.stringify({ speed: 0.5 }));
    notifyStorage(
      TTS_SETTINGS_STORAGE_KEY,
      window.localStorage.getItem(TTS_SETTINGS_STORAGE_KEY),
    );
    expect(getTtsSettingsSnapshot().speed).toBe(0.5);

    // Key removed in another tab.
    window.localStorage.removeItem(TTS_SETTINGS_STORAGE_KEY);
    notifyStorage(TTS_SETTINGS_STORAGE_KEY, null);
    expect(getTtsSettingsSnapshot()).toEqual(DEFAULT_TTS_SETTINGS);

    // Whole storage area cleared in another tab.
    act(() => {
      setTtsSettings((previous) => ({ ...previous, speed: 2 }));
    });
    window.localStorage.clear();
    notifyStorageCleared();
    expect(getTtsSettingsSnapshot()).toEqual(DEFAULT_TTS_SETTINGS);
  });

  it('ignores unrelated keys and non-localStorage areas', () => {
    render(<Probe />);
    act(() => {
      setTtsSettings((previous) => ({ ...previous, speed: 1.5 }));
    });

    act(() => {
      window.dispatchEvent(
        localEvent('tts_settings_other_key', JSON.stringify({ speed: 2 })),
      );
    });
    expect(getTtsSettingsSnapshot().speed).toBe(1.5);

    window.sessionStorage.setItem(
      TTS_SETTINGS_STORAGE_KEY,
      JSON.stringify({ speed: 0.5 }),
    );
    act(() => {
      window.dispatchEvent(
        sessionEvent(TTS_SETTINGS_STORAGE_KEY, '{"speed":0.5}'),
      );
    });
    expect(getTtsSettingsSnapshot().speed).toBe(1.5);
  });

  it('refreshes on the first subscriber after every consumer unmounted', () => {
    const first = render(<Probe />);
    first.unmount();
    // No consumer is attached, so a change here produces no local notification.
    window.localStorage.setItem(
      TTS_SETTINGS_STORAGE_KEY,
      JSON.stringify({ speed: 0.5, format: 'opus' }),
    );

    const second = render(<Probe />);
    expect(second.getByTestId('probe').textContent).toBe('0.5');
    expect(getTtsSettingsSnapshot().format).toBe('opus');
  });
});

describe('mounted play button', () => {
  function renderButton() {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      return new Promise(() => {});
    });
    vi.stubGlobal('fetch', fetchMock);
    return {
      calls,
      view: render(
        <AudioPlaybackProvider
          scope={{ conversationId: 'conv-1', authGeneration: 0 }}
        >
          <TextToSpeechButton
            messageId="m1"
            conversationId="conv-1"
            text="Alpha"
          />
        </AudioPlaybackProvider>,
      ),
    };
  }

  it('sends the current validated preferences for a new request', async () => {
    const first = renderButton();
    act(() => {
      setTtsSettings((previous) => ({
        ...previous,
        speed: 1.5,
        format: 'opus',
      }));
    });

    fireEvent.click(screen.getByLabelText('Play TTS'));
    await act(async () => {
      await Promise.resolve();
    });

    expect(first.calls).toHaveLength(1);
    expect(JSON.parse(first.calls[0].init?.body as string)).toEqual({
      text: 'Alpha',
      voice: 'daemon-default',
      speed: 1.5,
      format: 'opus',
      cache: true,
    });
    first.view.unmount();

    // A later request observes a later preference, not a stale snapshot.
    act(() => {
      setTtsSettings((previous) => ({
        ...previous,
        speed: 0.5,
        format: 'wav',
      }));
    });
    const second = renderButton();
    fireEvent.click(screen.getByLabelText('Play TTS'));
    await act(async () => {
      await Promise.resolve();
    });
    expect(JSON.parse(second.calls[0].init?.body as string)).toMatchObject({
      speed: 0.5,
      format: 'wav',
    });
    second.view.unmount();
    vi.unstubAllGlobals();
  });
});
