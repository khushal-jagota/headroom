/**
 * v13 (11): a title that does not fit its line ends in an ellipsis with no space before it.
 * CSS text-overflow cuts at the last glyph that fits, so a cut just after a word leaves
 * "Edits …". Instead the shown text is the longest prefix that fits once its trailing space is
 * trimmed and "…" is added.
 */
export const TITLE_ELLIPSIS = "…";

/** The longest `prefix.trimEnd() + "…"` of `text` that `fits` accepts; `text` itself when it fits. */
export function fitTitle(text: string, fits: (candidate: string) => boolean): string {
  if (fits(text)) return text;
  const characters = Array.from(text);
  const shown = (count: number): string => `${characters.slice(0, count).join("").trimEnd()}${TITLE_ELLIPSIS}`;
  let low = 0;
  let high = characters.length - 1;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (fits(shown(middle))) low = middle;
    else high = middle - 1;
  }
  return low === 0 ? TITLE_ELLIPSIS : shown(low);
}

/** Svelte action: keeps `node`'s text the fitted form of `title`, refitting when the row it
 *  stands in (`frame`, a selector for an ancestor whose width does not follow the title)
 *  changes width. */
export function fittedTitle(node: HTMLElement, title: string, frame = ".chat-head"): { update(next: string): void; destroy(): void } {
  let current = title;
  const fits = (candidate: string): boolean => {
    node.textContent = candidate;
    return node.scrollWidth <= node.clientWidth;
  };
  const refit = (): void => { node.textContent = fitTitle(current, fits); };
  const watched = node.closest<HTMLElement>(frame) ?? node.parentElement;
  let watchedWidth = -1;
  const observer = typeof ResizeObserver === "undefined" || !watched
    ? null
    : new ResizeObserver(() => {
        if (watched.clientWidth === watchedWidth) return;
        watchedWidth = watched.clientWidth;
        refit();
      });
  if (watched) observer?.observe(watched);
  refit();
  void document.fonts?.ready.then(() => refit());
  return {
    update(next: string): void { current = next; refit(); },
    destroy(): void { observer?.disconnect(); },
  };
}
