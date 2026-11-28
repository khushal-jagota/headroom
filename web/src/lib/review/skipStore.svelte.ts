import { dropUnseen, limitSkips, parseSkips, sameSkips, skipKey, skipMarks, stringifySkips, withSkip, type SkipList } from './skips';
import { reviewHistoryKey } from './return';
import type { NavigationReviewItem } from '../navigation/contracts';

const KEY_PREFIX = 'panels:review-skipped:';
const NONE: SkipList = Object.freeze({});
const EMPTY: ReadonlyMap<string, NavigationReviewItem> = new Map();
/** A list nobody has touched for this long belongs to a server that is gone. */
const ABANDONED_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * The one owner of the skipped cards on this device. Each server has its own list, named by the
 * server's installation, so a skip never applies to another server (the desktop app can change
 * servers). Panels has one owner per server and the page has no sign-in, so the list has no owner part.
 * That is accepted on purpose. The stored shape is versioned, so the key can change if an owner ever appears.
 *
 * A list is read from storage once per server. Every change reads storage again first and writes
 * the merged list back, so two tabs do not overwrite each other. A `storage` event brings another
 * tab's change here. Storage can be empty or blocked: the list then lives in this page for the visit.
 */
class ReviewSkipStore {
  #lists = new Map<string, SkipList>();
  /** The newest version of each card this page has loaded from the queue, by server. The queue loads one window at a time. */
  #known = new Map<string, Map<string, NavigationReviewItem>>();
  /** The cards of the walk through the queue's windows that is in progress, by server. */
  #walks = new Map<string, { started: boolean; keys: Set<string> }>();
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

  /** The cards the page has loaded so far, whichever window held them. Reactive. */
  known(scope: string | null): ReadonlyMap<string, NavigationReviewItem> {
    void this.#version;
    return (scope && this.#known.get(scope)) || EMPTY;
  }

  /** Cards the page loaded outside a walk, such as the tail of the queue. They count as known, and they say nothing of who left. */
  know(scope: string, items: readonly NavigationReviewItem[]): void {
    this.#remember(scope, items);
  }

  /**
   * One window of the queue the page has loaded. `first` says it is the window at the top of the queue, which
   * starts a walk. `last` says that no window follows. When the walk that began at the top reaches the last
   * window, every card of the queue has been seen: a skip for a card in none of them is for a card that left.
   */
  observe(scope: string, items: readonly NavigationReviewItem[], position: { first: boolean; last: boolean }): void {
    const walk = this.#walks.get(scope) ?? { started: false, keys: new Set<string>() };
    this.#walks.set(scope, walk);
    if (position.first) { walk.started = true; walk.keys.clear(); }
    this.#remember(scope, items);
    for (const item of items) walk.keys.add(skipKey(reviewHistoryKey(item)));
    if (position.last && walk.started) this.#change(scope, list => dropUnseen(list, walk.keys));
  }

  #remember(scope: string, items: readonly NavigationReviewItem[]): void {
    let known = this.#known.get(scope);
    if (!known) this.#known.set(scope, known = new Map());
    let changed = false;
    for (const item of items) {
      const key = skipKey(reviewHistoryKey(item));
      const before = known.get(key);
      if (!before || JSON.stringify(skipMarks(before)) !== JSON.stringify(skipMarks(item))) changed = true;
      known.set(key, item);
    }
    if (changed) this.#version += 1;
  }

  #change(scope: string, change: (list: SkipList) => SkipList): void {
    // Storage is read again, so a skip made in another tab since is kept. Blocked storage leaves this page's own list.
    const base = this.#read(scope) ?? this.#lists.get(scope) ?? NONE;
    const next = limitSkips(change(base), new Set(this.#known.get(scope)?.keys()));
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
