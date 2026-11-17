/** The card on screen stays until that card leaves. A later list does not replace it. */
export function shownReviewItem<T>(waiting: readonly T[], pinnedKey: string | null, keyOf: (item: T) => string): { item: T | null; pinnedKey: string | null } {
  if (pinnedKey !== null) {
    const pinned = waiting.find(item => keyOf(item) === pinnedKey);
    if (pinned) return { item: pinned, pinnedKey };
  }
  const item = waiting[0] ?? null;
  return { item, pinnedKey: item === null ? null : keyOf(item) };
}
