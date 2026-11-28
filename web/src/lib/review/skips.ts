import { reviewProposalIdentity } from '../navigation/presentation';
import type { NavigationReviewItem, ReviewHistoryKey } from '../navigation/contracts';
import { reviewHistoryKey } from './return';

/**
 * The cards this device skipped. A skip lives here only: the server never hears of it, so a skip
 * on the phone leaves the same card waiting on the desktop. Each entry holds what the card
 * showed when it was skipped. A card that shows something else is new again.
 */
export type SkipList = Record<string, string>;

const STORAGE_KEY = 'panels:review-skipped:v1';

export function skipKey(key: ReviewHistoryKey): string { return JSON.stringify([key.kind, key.id]); }
export function skipHistoryKeys(list: SkipList): ReviewHistoryKey[] {
  return Object.keys(list).sort().map(encoded => {
    const [kind, id] = JSON.parse(encoded) as [ReviewHistoryKey['kind'], string];
    return { kind, id };
  });
}

/** What the card holds that can ask for the owner again: a proposal revision, a message, an ask. */
export function reviewFingerprint(item: NavigationReviewItem): string {
  const message = (row: { conversation_id: string; waiting_since: number; latest_sequence?: number | null }) =>
    [row.conversation_id, row.waiting_since, row.latest_sequence ?? null];
  if (item.review_item_type === 'message') return JSON.stringify([message(item)]);
  if (item.review_item_type === 'ask') return JSON.stringify([item.conversation_id, item.waiting_since]);
  return JSON.stringify([
    item.proposal ? reviewProposalIdentity(item.proposal) : null,
    item.messages.map(message).sort((a, b) => String(a[0]).localeCompare(String(b[0]))),
    item.ask ? [item.ask.conversation_id, item.ask.waiting_since] : null,
  ]);
}

export function isSkipped(list: SkipList, item: NavigationReviewItem): boolean {
  return list[skipKey(reviewHistoryKey(item))] === reviewFingerprint(item);
}
export function withSkip(list: SkipList, item: NavigationReviewItem): SkipList {
  return { ...list, [skipKey(reviewHistoryKey(item))]: reviewFingerprint(item) };
}

/**
 * Drops the skips that no longer hold: a card that changed, and, when `items` is the whole queue,
 * a card that left it. Returns the same list when nothing dropped.
 */
export function pruneSkips(list: SkipList, items: readonly NavigationReviewItem[], wholeQueue: boolean): SkipList {
  const current = new Map(items.map(item => [skipKey(reviewHistoryKey(item)), reviewFingerprint(item)]));
  const kept = Object.entries(list).filter(([key, fingerprint]) => current.has(key) ? current.get(key) === fingerprint : !wholeQueue);
  return kept.length === Object.keys(list).length ? list : Object.fromEntries(kept);
}

export function loadSkips(): SkipList {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? '{}');
    if (value === null || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string'));
  } catch { return {}; }
}
export function saveSkips(list: SkipList): void {
  try {
    if (Object.keys(list).length) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch { /* This visit still holds the skips. */ }
}
