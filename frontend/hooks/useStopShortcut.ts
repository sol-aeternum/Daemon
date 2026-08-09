"use client";

import { useEffect } from "react";

const STOP_SHORTCUT_BLOCKER = '[data-stop-shortcut-block="true"]';

type UseStopShortcutOptions = {
  active: boolean;
  onStop: () => void;
};

function hasOpenShortcutBlocker() {
  return [...document.querySelectorAll<HTMLElement>(STOP_SHORTCUT_BLOCKER)].some((element) => {
    for (let current: HTMLElement | null = element; current; current = current.parentElement) {
      const style = window.getComputedStyle(current);
      if (
        current.hidden
        || current.getAttribute("aria-hidden") === "true"
        || style.display === "none"
        || style.visibility === "hidden"
      ) {
        return false;
      }
    }
    return true;
  });
}

export function useStopShortcut({ active, onStop }: UseStopShortcutOptions) {
  useEffect(() => {
    if (!active) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      if (hasOpenShortcutBlocker()) return;

      event.preventDefault();
      onStop();
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [active, onStop]);
}
