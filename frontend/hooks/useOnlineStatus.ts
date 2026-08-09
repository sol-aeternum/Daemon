import { useState, useEffect, useSyncExternalStore } from 'react';
import { useClientMounted } from './useClientMounted';

const subscribe = (callback: () => void) => {
  if (typeof window === 'undefined') {
    return () => {};
  }

  window.addEventListener('online', callback);
  window.addEventListener('offline', callback);
  return () => {
    window.removeEventListener('online', callback);
    window.removeEventListener('offline', callback);
  };
};

const readOnlineState = () => {
  if (typeof navigator === 'undefined') {
    return true;
  }
  return navigator.onLine;
};

export function useOnlineStatus() {
  const isClientMounted = useClientMounted();
  const isOnline = useSyncExternalStore(
    subscribe,
    () => (isClientMounted ? readOnlineState() : true),
    () => true,
  );
  const [wasOffline, setWasOffline] = useState(false);

  useEffect(() => {
    if (!isClientMounted) {
      return;
    }

    const handleOffline = () => {
      setWasOffline(true);
    };

    window.addEventListener('offline', handleOffline);

    return () => {
      window.removeEventListener('offline', handleOffline);
    };
  }, [isClientMounted]);

  return { isOnline, wasOffline };
}
