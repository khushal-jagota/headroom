import { reviewProposalIdentity } from '../navigation/presentation';
import type { NavigationReviewItem, ReviewHistoryKey } from '../navigation/contracts';
import { reviewHistoryKey } from './return';

/**
 * The cards this device skipped. A skip lives here only: the server never hears of it, so a skip
 * on the phone leaves the same card waiting on the desktop. Each entry holds what the card
 * showed when it was skipped. A card that shows something else is new again.
 */
export type SkipEntry = { fingerprint: string; at: number };
export type SkipList = Record<string, SkipEntry>;

/** The most skips one device keeps. The oldest go first. */
export const SKIP_LIMIT = 500;
const STORED_VERSION = 1;

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
  return list[skipKey(reviewHistoryKey(item))]?.fingerprint === reviewFingerprint(item);
}
export function withSkip(list: SkipList, item: NavigationReviewItem, at: number): SkipList {
  return limitSkips({ ...list, [skipKey(reviewHistoryKey(item))]: { fingerprint: reviewFingerprint(item), at } });
}
/** Keeps the newest `limit` skips. */
export function limitSkips(list: SkipList, limit = SKIP_LIMIT): SkipList {
  const entries = Object.entries(list);
  if (entries.length <= limit) return list;
  return Object.fromEntries(entries.sort((a, b) => b[1].at - a[1].at).slice(0, limit));
}

/**
 * Drops the skips that no longer hold: a card that changed, and, when `items` is the whole queue,
 * a card that left it. Returns the same list when nothing dropped.
 */
export function pruneSkips(list: SkipList, items: readonly NavigationReviewItem[], wholeQueue: boolean): SkipList {
  const current = new Map(items.map(item => [skipKey(reviewHistoryKey(item)), reviewFingerprint(item)]));
  const kept = Object.entries(list).filter(([key, entry]) => current.has(key) ? current.get(key) === entry.fingerprint : !wholeQueue);
  return kept.length === Object.keys(list).length ? list : Object.fromEntries(kept);
}

/** What storage holds. Anything that is not a list this build wrote reads as no skips, and never throws. */
export function parseSkips(raw: string | null): SkipList {
  try {
    const value: unknown = JSON.parse(raw ?? 'null');
    if (value === null || typeof value !== 'object' || (value as { version?: unknown }).version !== STORED_VERSION) return {};
    const entries = (value as { entries?: unknown }).entries;
    if (entries === null || typeof entries !== 'object' || Array.isArray(entries)) return {};
    const list: SkipList = {};
    for (const [key, entry] of Object.entries(entries)) {
      const fingerprint = (entry as SkipEntry | null)?.fingerprint, at = (entry as SkipEntry | null)?.at;
      if (typeof fingerprint === 'string' && typeof at === 'number' && Number.isFinite(at) && validKey(key)) list[key] = { fingerprint, at };
    }
    return limitSkips(list);
  } catch { return {}; }
}
export function stringifySkips(list: SkipList): string { return JSON.stringify({ version: STORED_VERSION, entries: list }); }
export function sameSkips(left: SkipList, right: SkipList): boolean { return stringifySkips(left) === stringifySkips(right); }
function validKey(key: string): boolean {
  try {
    const [kind, id] = JSON.parse(key) as [unknown, unknown];
    return (kind === 'ticket' || kind === 'conversation') && typeof id === 'string';
  } catch { return false; }
}
