// DOM identities are message IDs, never prompt labels or loaded-relative numbers.
export const REVEAL_MESSAGE_EVENT = 'daemon:reveal-message';

export function messageElements(container: HTMLElement): HTMLElement[] {
  return [
    ...container.querySelectorAll<HTMLElement>('article[data-message-id]'),
  ];
}

export function findMessage(container: HTMLElement, id: string) {
  // Do not interpolate IDs into selectors: persisted and streaming IDs need
  // not be valid CSS identifiers.
  return messageElements(container).find(
    (element) => element.dataset.messageId === id,
  );
}

export function revealMessage(element: HTMLElement) {
  element.dispatchEvent(new Event(REVEAL_MESSAGE_EVENT));
}

/** Last prompt starting above the reading line (or the first loaded prompt). */
export function currentPrompt(elements: HTMLElement[], top: number) {
  let low = 0;
  let high = elements.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (elements[middle].getBoundingClientRect().top <= top) low = middle + 1;
    else high = middle;
  }
  return elements[Math.max(0, low - 1)];
}
