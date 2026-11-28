import { reviewProposalIdentity } from '../navigation/presentation';
import type { NavigationReviewItem, ReviewHistoryKey } from '../navigation/contracts';
import { reviewHistoryKey } from './return';

/**
 * The cards this device skipped. A skip lives here only: the server never hears of it, so a skip
 * on the phone leaves the same card waiting on the desktop. Panels has one owner per server, so a
 * list belongs to a server and to no one else.
 *
 * Each entry remembers the parts of the card that were waiting when it was skipped. A skip ends
 * when the card shows a part that is newer or new. It never ends because a part is gone: an answered
 * message, a decided proposal or an older view of the queue makes a card smaller, not new.
 */
export type SkipMarks = {
  /** The proposal waiting, and its place in time. */
  proposal: { identity: string; order: [number, number] } | null;
  /** The newest message to the owner in each conversation. Null when the queue gives no sequence. */
  messages: Record<string, number | null>;
  /** When the ask in each conversation was raised. */
  asks: Record<string, number>;
};
export type SkipEntry = { marks: SkipMarks; at: number };
export type SkipList = Record<string, SkipEntry>;

/** The most skips one device keeps. */
export const SKIP_LIMIT = 500;
const STORED_VERSION = 1;

export function skipKey(key: ReviewHistoryKey): string { return JSON.stringify([key.kind, key.id]); }
export function skipHistoryKeys(list: SkipList): ReviewHistoryKey[] {
  return Object.keys(list).sort().map(encoded => {
    const [kind, id] = JSON.parse(encoded) as [ReviewHistoryKey['kind'], string];
    return { kind, id };
  });
}

type MessageRow = { conversation_id: string; latest_sequence?: number | null };
export function skipMarks(item: NavigationReviewItem): SkipMarks {
  const messages = (rows: readonly MessageRow[]) => Object.fromEntries(rows.map(row => [row.conversation_id, row.latest_sequence ?? null]));
  if (item.review_item_type === 'message') return { proposal: null, messages: messages([item]), asks: {} };
  if (item.review_item_type === 'ask') return { proposal: null, messages: {}, asks: { [item.conversation_id]: item.waiting_since } };
  return {
    proposal: item.proposal ? { identity: reviewProposalIdentity(item.proposal), order: [item.proposal.waiting_since, item.proposal.proposal_revision] } : null,
    messages: messages(item.messages),
    asks: item.ask ? { [item.ask.conversation_id]: item.ask.waiting_since } : {},
  };
}

const later = (left: number | null, right: number | null): boolean => left !== null && (right === null || left > right);
const laterOrder = (left: [number, number], right: [number, number]): boolean => left[0] > right[0] || (left[0] === right[0] && left[1] > right[1]);

/** The card shows a proposal, a message or an ask that is newer than the marks, or that they never held. */
export function hasNewer(marks: SkipMarks, item: NavigationReviewItem): boolean {
  const now = skipMarks(item);
  if (now.proposal && (!marks.proposal || (now.proposal.identity !== marks.proposal.identity && laterOrder(now.proposal.order, marks.proposal.order)))) return true;
  for (const [conversation, sequence] of Object.entries(now.messages))
    if (!(conversation in marks.messages) || later(sequence, marks.messages[conversation])) return true;
  return Object.entries(now.asks).some(([conversation, raised]) => !(conversation in marks.asks) || raised > marks.asks[conversation]);
}

/** Marks that hold the newer of each part. A skip made from an older view cannot make a newer one older. */
export function mergeMarks(left: SkipMarks, right: SkipMarks): SkipMarks {
  const messages = { ...left.messages };
  for (const [conversation, sequence] of Object.entries(right.messages))
    messages[conversation] = conversation in messages && !later(sequence, messages[conversation]) ? messages[conversation] : sequence;
  const asks = { ...left.asks };
  for (const [conversation, raised] of Object.entries(right.asks)) asks[conversation] = Math.max(raised, asks[conversation] ?? raised);
  const proposal = !left.proposal ? right.proposal : !right.proposal ? left.proposal : laterOrder(right.proposal.order, left.proposal.order) ? right.proposal : left.proposal;
  return { proposal, messages, asks };
}

export function isSkipped(list: SkipList, item: NavigationReviewItem): boolean {
  const entry = list[skipKey(reviewHistoryKey(item))];
  return entry !== undefined && !hasNewer(entry.marks, item);
}
export function withSkip(list: SkipList, item: NavigationReviewItem, at: number): SkipList {
  const key = skipKey(reviewHistoryKey(item));
  const marks = skipMarks(item);
  return { ...list, [key]: { marks: list[key] ? mergeMarks(list[key].marks, marks) : marks, at } };
}

/**
 * The skips that still apply, for the count the server makes. A card the page has loaded must not
 * show something newer. A card it has not loaded cannot be checked, so its skip counts.
 */
export function applyingSkips(list: SkipList, items: readonly NavigationReviewItem[]): SkipList {
  const loaded = new Map(items.map(item => [skipKey(reviewHistoryKey(item)), item]));
  return Object.fromEntries(Object.entries(list).filter(([key, entry]) => { const item = loaded.get(key); return !item || !hasNewer(entry.marks, item); }));
}

/** The skips for cards the page has not loaded, so that their change cannot be checked. */
export function unloadedSkipKeys(list: SkipList, loaded: ReadonlySet<string>): ReviewHistoryKey[] {
  return skipHistoryKeys(Object.fromEntries(Object.entries(list).filter(([key]) => !loaded.has(key))));
}

/** Drops the skips of cards that left the queue: those the page never saw in any window it loaded. */
export function dropUnseen(list: SkipList, seen: ReadonlySet<string>): SkipList {
  const kept = Object.entries(list).filter(([key]) => seen.has(key));
  return kept.length === Object.keys(list).length ? list : Object.fromEntries(kept);
}

/** Keeps `limit` skips. The cards the page has not seen go first, the oldest first, because they may be gone. */
export function limitSkips(list: SkipList, seen: ReadonlySet<string>, limit = SKIP_LIMIT): SkipList {
  const entries = Object.entries(list);
  if (entries.length <= limit) return list;
  const rank = ([key, entry]: [string, SkipEntry]) => (seen.has(key) ? 1 : 0) * 1e15 + entry.at;
  return Object.fromEntries(entries.sort((a, b) => rank(b) - rank(a)).slice(0, limit));
}

/** What storage holds. Anything that is not a list this build wrote reads as no skips, and never throws. */
export function parseSkips(raw: string | null): SkipList {
  try {
    const value: unknown = JSON.parse(raw ?? 'null');
    if (!isObject(value) || value.version !== STORED_VERSION || !isObject(value.entries)) return {};
    const list: SkipList = {};
    for (const [key, entry] of Object.entries(value.entries)) {
      const marks = isObject(entry) ? readMarks(entry.marks) : null;
      if (marks && isObject(entry) && typeof entry.at === 'number' && Number.isFinite(entry.at) && validKey(key)) list[key] = { marks, at: entry.at };
    }
    return limitSkips(list, new Set());
  } catch { return {}; }
}
export function stringifySkips(list: SkipList): string { return JSON.stringify({ version: STORED_VERSION, entries: list }); }
export function sameSkips(left: SkipList, right: SkipList): boolean { return stringifySkips(left) === stringifySkips(right); }

function isObject(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === 'object' && !Array.isArray(value); }
const number = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
function readMarks(value: unknown): SkipMarks | null {
  if (!isObject(value) || !isObject(value.messages) || !isObject(value.asks)) return null;
  const messages = Object.entries(value.messages), asks = Object.entries(value.asks);
  if (!messages.every(([, sequence]) => sequence === null || number(sequence)) || !asks.every(([, raised]) => number(raised))) return null;
  const proposal = value.proposal;
  if (proposal !== null && !(isObject(proposal) && typeof proposal.identity === 'string' && Array.isArray(proposal.order) && proposal.order.length === 2 && proposal.order.every(number))) return null;
  return { proposal: proposal as SkipMarks['proposal'], messages: Object.fromEntries(messages) as SkipMarks['messages'], asks: Object.fromEntries(asks) as SkipMarks['asks'] };
}
function validKey(key: string): boolean {
  try {
    const [kind, id] = JSON.parse(key) as [unknown, unknown];
    return (kind === 'ticket' || kind === 'conversation') && typeof id === 'string';
  } catch { return false; }
}
