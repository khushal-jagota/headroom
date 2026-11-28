import { dropUnseen, limitSkips, parseSkips, sameSkips, skipKey, stringifySkips, withSkip, type SkipList } from './skips';
import { reviewHistoryKey } from './return';
import type { NavigationReviewItem } from '../navigation/contracts';

const KEY_PREFIX = 'panels:review-skipped:';
const NONE: SkipList = Object.freeze({});
/** A list nobody has touched for this long belongs to a server that is gone. */
const ABANDONED_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * The one owner of the skipped cards on this device. Each server has its own list, named by the
 * server's installation, so a skip never applies to another server (the desktop app can change
 * servers). Panels has one owner per server, so the list has no owner part.
 *
 * A list is read from storage once per server. Every change reads storage again first and writes
 * the merged list back, so two tabs do not overwrite each other. A `storage` event brings another
 * tab's change here. Storage can be empty or blocked: the list then lives in this page for the visit.
 */
class ReviewSkipStore {
  #lists = new Map<string, SkipList>();
  /** The cards this page has seen in the queue, by server. A skip for a card never seen may belong to one that left. */
  #seen = new Map<string, Set<string>>();
  #version = $state(0);

  constructor() {
    if (typeof window !== 'undefined') window.addEventListener('storage', event => this.#fromOtherTab(event));
  }

  /** The skips for one server. Reactive: it changes when a skip is added, dropped or arrives from another tab. */
  list(scope: string | null): SkipList {
    void this.#version;
    if (scope === null) return NONE;
    let list = this.#lists.get(scope);
    if (!list) {
      list = this.#read(scope) ?? NONE;
      this.#lists.set(scope, list);
      this.#forgetAbandoned(scope);
    }
    return list;
  }

  skip(scope: string, item: NavigationReviewItem): void {
    this.#change(scope, list => withSkip(list, item, Date.now()));
  }

  /**
   * The queue the page has loaded. `complete` says that its last window is loaded, so every window of
   * the queue has been seen: a skip for a card in none of them is for a card that left.
   */
  observe(scope: string, items: readonly NavigationReviewItem[], complete: boolean): void {
    const seen = this.#seenIn(scope);
    for (const item of items) seen.add(skipKey(reviewHistoryKey(item)));
    if (complete) this.#change(scope, list => dropUnseen(list, seen));
  }

  #seenIn(scope: string): Set<string> {
    let seen = this.#seen.get(scope);
    if (!seen) this.#seen.set(scope, seen = new Set());
    return seen;
  }

  #change(scope: string, change: (list: SkipList) => SkipList): void {
    // Storage is read again, so a skip made in another tab since is kept. Blocked storage leaves this page's own list.
    const base = this.#read(scope) ?? this.#lists.get(scope) ?? NONE;
    const next = limitSkips(change(base), this.#seenIn(scope));
    if (sameSkips(base, next) && sameSkips(this.#lists.get(scope) ?? NONE, next)) return;
    this.#write(scope, next);
    this.#set(scope, next);
  }

  #set(scope: string, list: SkipList): void {
    if (sameSkips(this.#lists.get(scope) ?? NONE, list)) return;
    this.#lists.set(scope, list);
    this.#version += 1;
  }

  #fromOtherTab(event: StorageEvent): void {
    // A null key is storage cleared. Every list is read again then, and a missing list is empty.
    for (const scope of [...this.#lists.keys()]) {
      if (event.key !== null && event.key !== KEY_PREFIX + scope) continue;
      this.#set(scope, this.#read(scope) ?? parseSkips(event.key === null ? null : event.newValue));
    }
  }

  #read(scope: string): SkipList | null {
    try {
      return parseSkips(window.localStorage.getItem(KEY_PREFIX + scope));
    } catch { return null; }
  }

  #write(scope: string, list: SkipList): void {
    try {
      if (Object.keys(list).length) window.localStorage.setItem(KEY_PREFIX + scope, stringifySkips(list));
      else window.localStorage.removeItem(KEY_PREFIX + scope);
    } catch { /* The skips stay in this page for the visit. */ }
  }

  /** Removes the saved lists of other servers that have gone untouched for a long time. */
  #forgetAbandoned(scope: string): void {
    try {
      const storage = window.localStorage;
      for (let index = storage.length - 1; index >= 0; index -= 1) {
        const key = storage.key(index);
        if (!key?.startsWith(KEY_PREFIX) || key === KEY_PREFIX + scope) continue;
        const newest = Math.max(0, ...Object.values(parseSkips(storage.getItem(key))).map(entry => entry.at));
        if (Date.now() - newest > ABANDONED_MS) storage.removeItem(key);
      }
    } catch { /* A list that stays does no harm. */ }
  }
}

export const reviewSkips = new ReviewSkipStore();
