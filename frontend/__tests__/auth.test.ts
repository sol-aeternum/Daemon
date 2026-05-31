import { beforeEach, describe, expect, it, vi } from "vitest";

// Mocks must be set up before importing the module, so we use vi.doMock() inside
// a beforeEach and import the module fresh inside each test.

const mockNavigator = { value: { locks: undefined } };
const mockLocalStorage = { value: {} as Record<string, string> };
const mockBroadcastChannel = { value: null as BroadcastChannel | null };

class TestBroadcastChannel extends EventTarget implements BroadcastChannel {
  static instances: TestBroadcastChannel[] = [];
  name: string;
  onmessage: ((this: BroadcastChannel, ev: MessageEvent) => unknown) | null = null;
  onmessageerror: ((this: BroadcastChannel, ev: MessageEvent) => unknown) | null = null;

  constructor(name: string) {
    super();
    this.name = name;
    TestBroadcastChannel.instances.push(this);
  }

  postMessage(message: unknown): void {
    this.dispatch(message);
  }

  close(): void {}

  dispatch(message: unknown): void {
    const event = new MessageEvent("message", { data: message });
    this.dispatchEvent(event);
    this.onmessage?.call(this, event);
  }

  addEventListener(
    type: string,
    callback: EventListenerOrEventListenerObject | null,
    options?: boolean | AddEventListenerOptions,
  ): void {
    if (callback) super.addEventListener(type, callback, options);
  }

  removeEventListener(
    type: string,
    callback: EventListenerOrEventListenerObject | null,
    options?: boolean | EventListenerOptions,
  ): void {
    if (callback) super.removeEventListener(type, callback, options);
  }
}

function installStorage(initial: Record<string, string> = {}): void {
  const store = { ...initial };
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => {
        store[key] = value;
      },
      removeItem: (key: string) => {
        delete store[key];
      },
      clear: () => {
        for (const key of Object.keys(store)) delete store[key];
      },
    },
  });
}

beforeEach(() => {
  mockNavigator.value = { locks: undefined };
  mockLocalStorage.value = {};
  mockBroadcastChannel.value = null;
  vi.resetModules();
});

describe("auth token management", () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    TestBroadcastChannel.instances = [];
    vi.resetModules();
  });

  it("hasValidAccessToken returns false when no token", async () => {
    const { hasValidAccessToken } = await import("../lib/auth");
    expect(hasValidAccessToken()).toBe(false);
  });

  it("hasValidAccessToken returns true when token is fresh", async () => {
    const { hasValidAccessToken, setAccessToken } = await import("../lib/auth");
    setAccessToken("tok", Date.now() + 60_000);
    expect(hasValidAccessToken()).toBe(true);
  });

  it("hasValidAccessToken returns false when token is expired", async () => {
    const { hasValidAccessToken, setAccessToken } = await import("../lib/auth");
    setAccessToken("tok", Date.now() - 1_000);
    expect(hasValidAccessToken()).toBe(false);
  });

  it("hasValidAccessToken returns false when token expires within 30s buffer", async () => {
    const { hasValidAccessToken, setAccessToken } = await import("../lib/auth");
    setAccessToken("tok", Date.now() + 20_000);
    expect(hasValidAccessToken()).toBe(false);
  });

  it("setAccessToken and getAccessToken round-trip correctly", async () => {
    const { setAccessToken, getAccessToken } = await import("../lib/auth");
    setAccessToken("my-token", 99_000);
    expect(getAccessToken()).toBe("my-token");
  });

  it("clearLocalAuthState wipes token and expiry", async () => {
    const { clearLocalAuthState, getAccessToken, hasValidAccessToken, setAccessToken } = await import("../lib/auth");
    setAccessToken("tok", Date.now() + 99_000);
    clearLocalAuthState();
    expect(getAccessToken()).toBeNull();
    expect(hasValidAccessToken()).toBe(false);
  });

  it("getAuthHeader returns null when no valid token", async () => {
    const { getAuthHeader } = await import("../lib/auth");
    expect(getAuthHeader()).toBeNull();
  });

  it("getAuthHeader returns Bearer token when valid", async () => {
    const { getAuthHeader, setAccessToken } = await import("../lib/auth");
    setAccessToken("secret", Date.now() + 99_000);
    expect(getAuthHeader()).toBe("Bearer secret");
  });

  it("getAuthHeader returns null when token is expired", async () => {
    const { getAuthHeader, setAccessToken } = await import("../lib/auth");
    setAccessToken("expired", Date.now() - 1_000);
    expect(getAuthHeader()).toBeNull();
  });
});

describe("auth refresh promise singleton", () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it("getRefreshPromise returns null initially", async () => {
    const { getRefreshPromise } = await import("../lib/auth");
    expect(getRefreshPromise()).toBeNull();
  });
});

describe("listenForAuthEvents", () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it("returns an unsubscribe function", async () => {
    const { listenForAuthEvents } = await import("../lib/auth");
    const unsub = listenForAuthEvents(() => {});
    expect(unsub).toBeDefined();
    expect(typeof unsub).toBe("function");
  });
});

describe("refreshAccessToken", () => {
  beforeEach(() => {
    mockNavigator.value = { locks: undefined };
    mockLocalStorage.value = {};
    mockBroadcastChannel.value = null;
    vi.resetModules();
  });

  it("returns success immediately when token is already valid", async () => {
    const { refreshAccessToken, setAccessToken } = await import("../lib/auth");
    setAccessToken("valid-token", Date.now() + 60_000);
    const result = await refreshAccessToken();
    expect(result).toEqual({ success: true });
  });

  it("uses navigator.locks when available and refresh succeeds", async () => {
    const mockLockRequest = vi.fn(async (_name: string, cb: (impl: { held: boolean }) => void) => {
      await cb({ held: true });
    });
    Object.defineProperty(globalThis, "navigator", {
      value: { locks: { request: mockLockRequest } },
      writable: true,
    });

    const mockFetch = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ access_token: "new-access", expires_in: 1800 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      )
    );
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, setAccessToken } = await import("../lib/auth");
    setAccessToken("tok", Date.now() - 1_000);
    const result = await refreshAccessToken();

    expect(result.success).toBe(true);
    expect(mockLockRequest).toHaveBeenCalledWith("daemon-refresh", expect.any(Function));

    Object.defineProperty(globalThis, "navigator", { value: mockNavigator.value, writable: true });
    vi.restoreAllMocks();
  });

  it("returns failure when refresh fails with 401 and clears auth", async () => {
    const mockLockRequest = vi.fn(async (_name: string, cb: (impl: { held: boolean }) => void) => {
      await cb({ held: true });
    });
    Object.defineProperty(globalThis, "navigator", {
      value: { locks: { request: mockLockRequest } },
      writable: true,
    });

    const mockFetch = vi.fn(() => Promise.resolve(new Response(null, { status: 401 })));
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, setAccessToken, getAccessToken } = await import("../lib/auth");
    setAccessToken("old-token", Date.now() - 1_000);
    const result = await refreshAccessToken();

    expect(result.success).toBe(false);
    expect(result.error).toBe("Session expired");
    expect(getAccessToken()).toBeNull();

    Object.defineProperty(globalThis, "navigator", { value: mockNavigator.value, writable: true });
    vi.restoreAllMocks();
  });

  it("rechecks hasValidAccessToken after acquiring navigator.locks before calling doRefresh", async () => {
    const mockLockRequest = vi.fn(
      async (_name: string, cb: (impl: { held: boolean }) => void) => {
        const { setAccessToken } = await import("../lib/auth");
        setAccessToken("already-refreshed", Date.now() + 60_000);
        await cb({ held: true });
      }
    );
    Object.defineProperty(globalThis, "navigator", {
      value: { locks: { request: mockLockRequest } },
      writable: true,
    });

    const mockFetch = vi.fn();
    globalThis.fetch = mockFetch;

    const { refreshAccessToken } = await import("../lib/auth");
    const result = await refreshAccessToken();

    expect(result.success).toBe(true);
    expect(mockFetch).not.toHaveBeenCalled();

    Object.defineProperty(globalThis, "navigator", { value: mockNavigator.value, writable: true });
    vi.restoreAllMocks();
  });

  it("listenForAuthEvents applies token and expiry from a received refreshed event", async () => {
    // Fresh module so channel is created after spy is set up
    vi.resetModules();
    const { _getChannel } = await import("../lib/auth");
    const channel = _getChannel();
    expect(channel).not.toBeNull();
    const addEventListenerSpy = vi.spyOn(channel!, "addEventListener");

    const { listenForAuthEvents, getAccessToken, hasValidAccessToken, setAccessToken } = await import("../lib/auth");
    setAccessToken("old-token", Date.now() - 1_000);
    expect(hasValidAccessToken()).toBe(false);

    const newToken = "new-access-from-broadcast";
    const newExpiry = Date.now() + 60_000;

    listenForAuthEvents(() => {});

    const messageHandler = addEventListenerSpy.mock.calls[0]?.[1] as (event: MessageEvent) => void;
    messageHandler({ data: { type: "refreshed", tabId: "other-tab", accessToken: newToken, expiresAt: newExpiry } } as MessageEvent);

    expect(getAccessToken()).toBe(newToken);
    expect(hasValidAccessToken()).toBe(true);
  });

  it("listenForAuthEvents passes cleared event through to user callback", async () => {
    vi.resetModules();
    const { _getChannel } = await import("../lib/auth");
    const channel = _getChannel();
    expect(channel).not.toBeNull();
    const addEventListenerSpy = vi.spyOn(channel!, "addEventListener");

    const { listenForAuthEvents, getAccessToken } = await import("../lib/auth");

    let receivedType: string | null = null;
    listenForAuthEvents((type) => {
      receivedType = type;
    });

    const messageHandler = addEventListenerSpy.mock.calls[0]?.[1] as (event: MessageEvent) => void;
    messageHandler({ data: { type: "cleared", tabId: "other-tab" } } as MessageEvent);

    expect(receivedType).toBe("cleared");
    expect(getAccessToken()).toBeNull();
  });

  it("refreshAccessToken no-Web-Locks fallback: stale lock resolved false does not proceed to doRefresh", async () => {
    // Mock fetch to return 401 so doRefresh fails if it were ever called
    const mockFetch = vi.fn(() => Promise.resolve(new Response(null, { status: 401 })));
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, hasValidAccessToken, getAccessToken, setAccessToken } = await import("../lib/auth");

    setAccessToken("invalid-token", Date.now() - 1_000);

    const result = await refreshAccessToken();

    // waitForRefresh returned false (no refreshed event received), doRefresh was NOT called.
    // No POST was made, so fetch was never invoked.
    expect(result.success).toBe(false);
    expect(hasValidAccessToken()).toBe(false);
    expect(getAccessToken()).toBe("invalid-token");
    expect(mockFetch).not.toHaveBeenCalled();

    vi.restoreAllMocks();
  });

  it("refreshAccessToken no-Web-Locks fallback uses broadcast result without posting again", async () => {
    Object.defineProperty(globalThis, "navigator", {
      value: { locks: undefined },
      writable: true,
    });
    Object.defineProperty(globalThis, "BroadcastChannel", {
      configurable: true,
      value: TestBroadcastChannel,
    });
    installStorage({
      "daemon:refresh-lock": JSON.stringify({
        ownerTabId: "other-tab",
        nonce: "other-nonce",
        expiresAt: Date.now() + 10_000,
      }),
    });

    const mockFetch = vi.fn(() => Promise.resolve(new Response(null, { status: 500 })));
    globalThis.fetch = mockFetch;

    const { refreshAccessToken, getAccessToken, hasValidAccessToken } = await import("../lib/auth");
    const refreshPromise = refreshAccessToken();

    await Promise.resolve();
    TestBroadcastChannel.instances[0]?.dispatch({
      type: "refreshed",
      tabId: "other-tab",
      accessToken: "shared-access-token",
      expiresAt: Date.now() + 60_000,
    });

    const result = await refreshPromise;
    expect(result.success).toBe(true);
    expect(getAccessToken()).toBe("shared-access-token");
    expect(hasValidAccessToken()).toBe(true);
    expect(mockFetch).not.toHaveBeenCalled();

    vi.restoreAllMocks();
  });
});
