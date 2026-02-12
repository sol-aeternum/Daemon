"use client";

import { useState, useEffect, useCallback, useRef } from "react";

export function useLocalStorage<T>(key: string, initialValue: T) {
  const [storedValue, setStoredValue] = useState<T>(initialValue);
  const [isLoaded, setIsLoaded] = useState(false);
  const isUpdating = useRef(false);

  useEffect(() => {
    try {
      const item = window.localStorage.getItem(key);
      if (item) {
        setStoredValue(JSON.parse(item));
      }
    } catch (error) {
      console.error(`Error loading localStorage key "${key}":`, error);
    }
    setIsLoaded(true);
  }, [key]);

  const setValue = useCallback(
    (value: T | ((val: T) => T)) => {
      if (isUpdating.current) return;
      
      try {
        isUpdating.current = true;
        const valueToStore = value instanceof Function ? value(storedValue) : value;
        setStoredValue(valueToStore);
        window.localStorage.setItem(key, JSON.stringify(valueToStore));
        
        // Reset flag after a tick
        setTimeout(() => {
          isUpdating.current = false;
        }, 0);
      } catch (error) {
        console.error(`Error saving localStorage key "${key}":`, error);
        isUpdating.current = false;
      }
    },
    [key, storedValue]
  );

  const removeValue = useCallback(() => {
    try {
      window.localStorage.removeItem(key);
      setStoredValue(initialValue);
    } catch (error) {
      console.error(`Error removing localStorage key "${key}":`, error);
    }
  }, [key, initialValue]);

  return { value: storedValue, setValue, removeValue, isLoaded };
}
