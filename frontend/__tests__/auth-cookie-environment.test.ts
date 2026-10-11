import { afterEach, expect, it, vi } from 'vitest';
import {
  AuthTestBroadcastChannel,
  FIXTURE_AUTH_EPOCH,
  createAuthTestStorage,
  currentStorage,
  installAuthCookieTestEnvironment,
} from './helpers/authCookieEnvironment';

afterEach(() => vi.unstubAllGlobals());

it('requires explicit browser storage when Node has none', () => {
  vi.stubGlobal('window', undefined);
  vi.stubGlobal('localStorage', undefined);
  expect(currentStorage).toThrow('No usable storage');
  const storage = createAuthTestStorage();
  vi.stubGlobal('localStorage', storage);
  expect(currentStorage()).toBe(storage);
  installAuthCookieTestEnvironment(storage);
  expect(storage.getItem('daemon:auth-coordination')).toBe(
    JSON.stringify({ version: 1, epoch: FIXTURE_AUTH_EPOCH, status: 'stable' }),
  );
});

it('turns a throwing storage getter into the intended fixture diagnostic', () => {
  vi.stubGlobal('window', undefined);
  const descriptor = Object.getOwnPropertyDescriptor(
    globalThis,
    'localStorage',
  );
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    get: () => {
      throw new Error('disabled storage');
    },
  });
  try {
    expect(currentStorage).toThrow('No usable storage');
  } finally {
    if (descriptor)
      Object.defineProperty(globalThis, 'localStorage', descriptor);
    else Reflect.deleteProperty(globalThis, 'localStorage');
  }
});

it('delivers same-name cloned notices without echo and removes closed recipients', async () => {
  const sender = new AuthTestBroadcastChannel('fixture-delivery');
  const receiver = new AuthTestBroadcastChannel('fixture-delivery');
  const closed = new AuthTestBroadcastChannel('fixture-delivery');
  const different = new AuthTestBroadcastChannel('fixture-other');
  const senderListener = vi.fn();
  const receiverListener = vi.fn();
  const propertyListener = vi.fn();
  const closedListener = vi.fn();
  const differentListener = vi.fn();
  sender.addEventListener('message', senderListener);
  receiver.addEventListener('message', receiverListener);
  receiver.onmessage = propertyListener;
  closed.addEventListener('message', closedListener);
  different.addEventListener('message', differentListener);
  try {
    const message = { type: 'refreshed', epoch: 'fictional' };
    sender.postMessage(message);
    message.epoch = 'changed after posting';
    closed.close();
    expect(receiverListener).not.toHaveBeenCalled();
    await Promise.resolve();
    expect(receiverListener).toHaveBeenCalledTimes(1);
    expect(receiverListener.mock.calls[0][0].data).toEqual({
      type: 'refreshed',
      epoch: 'fictional',
    });
    expect(propertyListener).toHaveBeenCalledTimes(1);
    expect(senderListener).not.toHaveBeenCalled();
    expect(closedListener).not.toHaveBeenCalled();
    expect(differentListener).not.toHaveBeenCalled();
    receiver.removeEventListener('message', receiverListener);
    sender.postMessage({ type: 'cleared' });
    await Promise.resolve();
    expect(receiverListener).toHaveBeenCalledTimes(1);
    expect(propertyListener).toHaveBeenCalledTimes(2);
    expect(() => closed.postMessage({})).toThrow('Channel is closed');
  } finally {
    for (const channel of [sender, receiver, closed, different])
      channel.close();
  }
});

it('keeps the real cached auth channel reachable across fixture setup cycles', async () => {
  const setup = () => {
    const storage = createAuthTestStorage();
    vi.stubGlobal('localStorage', storage);
    installAuthCookieTestEnvironment(storage, { broadcastChannel: true });
  };
  setup();
  const auth = await import('../lib/auth');
  auth.clearLocalAuthState();
  auth.setAccessToken('fictional-A', Date.now() + 120_000, FIXTURE_AUTH_EPOCH);
  const original = auth._getChannel();
  const notices = vi.fn();
  const unsubscribe = auth.listenForAuthEvents(notices);
  const peer = new AuthTestBroadcastChannel('daemon-auth');
  try {
    for (const type of ['refreshed', 'cleared'] as const) {
      setup();
      auth.clearLocalAuthState();
      auth.setAccessToken(
        'fictional-B',
        Date.now() + 120_000,
        FIXTURE_AUTH_EPOCH,
      );
      const generation = auth.getAuthGeneration();
      expect(auth._getChannel()).toBe(original);
      peer.postMessage({
        type,
        tabId: 'fictional-peer',
        epoch: FIXTURE_AUTH_EPOCH,
      });
      await Promise.resolve();
      expect(notices).toHaveBeenLastCalledWith(type, 'fictional-peer');
      expect(auth.getAuthGeneration()).toBe(generation + 1);
      expect(auth.getAccessToken()).toBe(
        type === 'cleared' ? null : 'fictional-B',
      );
    }
  } finally {
    unsubscribe();
    peer.close();
    auth.clearLocalAuthState();
  }
});
