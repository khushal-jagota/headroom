import { limitSkips, parseSkips, pruneSkips, sameSkips, stringifySkips, withSkip, type SkipList } from './skips';
import type { NavigationReviewItem } from '../navigation/contracts';

const KEY_PREFIX = 'panels:review-skipped:';
const NONE: SkipList = Object.freeze({});

/**
 * The one owner of the skipped cards on this device. Each server has its own list, named by
 * the server's installation, so a skip never applies to another server (the desktop app can
 * change servers). The page has no owner identity: there is one owner per server.
 *
 * A list is read from storage once per server. Every change reads storage again first and writes
 * the merged list back, so two tabs do not overwrite each other. A `storage` event brings another
 * tab's change here. Storage can be empty or blocked: the list then lives in this page for the visit.
 */
class ReviewSkipStore {
  #lists = new Map<string, SkipList>();
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
    }
    return list;
  }

  skip(scope: string, item: NavigationReviewItem): void {
    this.#change(scope, list => withSkip(list, item, Date.now()));
  }

  /** See `pruneSkips`. */
  prune(scope: string, items: readonly NavigationReviewItem[], wholeQueue: boolean): void {
    this.#change(scope, list => pruneSkips(list, items, wholeQueue));
  }

  #change(scope: string, change: (list: SkipList) => SkipList): void {
    // Storage is read again, so a skip made in another tab since is kept. Blocked storage leaves this page's own list.
    const base = this.#read(scope) ?? this.#lists.get(scope) ?? NONE;
    const next = limitSkips(change(base));
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
}

export const reviewSkips = new ReviewSkipStore();
