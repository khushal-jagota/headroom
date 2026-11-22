// The desktop app opens with the last session's screens, then refreshes them.
//
// In the app, the screens keep their server reads and the last navigation view in their
// own IndexedDB. The next launch puts them back before the screens mount, so the window
// shows content at once. Every restored read refetches as soon as a screen shows it, and
// until fresh data replaces the saved copy on screen, the shell status says so.
//
// Only the app does this. The browser version opens as it always has. The native side
// deletes this database when the person changes the server, so one server's data never
// shows under another.
import { dehydrate, hydrate, type DehydratedState, type Query, type QueryClient } from "@tanstack/svelte-query";
import { derived, writable, type Readable } from "svelte/store";
import type { NavigationPublication } from "./navigation/publication";

const DATABASE = "panels-saved-session";
const STORE = "saved";
const RECORD = "last";
const SAVE_DELAY_MS = 1_000;
// The window waits for the saved copy before it mounts the screens. It never waits long.
const READ_LIMIT_MS = 1_000;

// The main chunk's URL carries the build's content hash. Response shapes can change with
// the screens, so a copy saved by another build is not used.
const SCREENS_BUILD = import.meta.url;

type SavedSession = {
  build: string;
  queries: DehydratedState;
  navigation: NavigationPublication | null;
};

export function inDesktopApp(): boolean {
  return typeof location !== "undefined" && location.protocol === "panels:";
}

/**
 * Server reads go through `resourceQuery`. A read can opt out, as the shell's update status does.
 * A read whose refetch failed keeps its data, and so does the saved copy: an offline session
 * must not erase the last good one.
 */
function savedForNextLaunch(query: Query): boolean {
  return query.state.data !== undefined && query.meta?.resourceBody === true && query.meta?.savedForNextLaunch !== false;
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function readSaved(): Promise<SavedSession | null> {
  const database = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const request = database.transaction(STORE).objectStore(STORE).get(RECORD);
      request.onsuccess = () => resolve((request.result as SavedSession | undefined) ?? null);
      request.onerror = () => reject(request.error);
    });
  } finally {
    database.close();
  }
}

async function writeSaved(session: SavedSession): Promise<void> {
  const database = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction(STORE, "readwrite");
      transaction.objectStore(STORE).put(session, RECORD);
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
    });
  } finally {
    database.close();
  }
}

// Restored reads that a screen has not yet refreshed, by query hash, with the time their
// saved data was fetched. A read leaves when its data changes or the cache drops it.
const unrefreshed = new Map<string, number>();
const restoredReadOnScreen = writable(false);
let savedNavigation: NavigationPublication | undefined;
const savedNavigationOnScreen = writable(false);

/** True while any screen shows data from the last session that the server has not yet replaced. */
export const savedSessionOnScreen: Readable<boolean> = derived(
  [restoredReadOnScreen, savedNavigationOnScreen],
  ([reads, navigation]) => reads || navigation
);

let client: QueryClient | undefined;
let latestNavigation: NavigationPublication | null = null;
let saveTimer: ReturnType<typeof setTimeout> | undefined;

function scheduleSave(): void {
  if (saveTimer !== undefined) return;
  saveTimer = setTimeout(saveNow, SAVE_DELAY_MS);
}

function saveNow(): void {
  clearTimeout(saveTimer);
  saveTimer = undefined;
  if (!client) return;
  const session: SavedSession = {
    build: SCREENS_BUILD,
    queries: dehydrate(client, { shouldDehydrateQuery: savedForNextLaunch }),
    navigation: latestNavigation,
  };
  void writeSaved(session).catch(() => undefined);
}

function followRestoredReads(queryClient: QueryClient): void {
  const cache = queryClient.getQueryCache();
  const update = () => {
    for (const [hash, savedAt] of unrefreshed) {
      const query = cache.get(hash);
      if (!query || query.state.dataUpdatedAt !== savedAt) unrefreshed.delete(hash);
    }
    restoredReadOnScreen.set([...unrefreshed.keys()].some((hash) => cache.get(hash)?.isActive() === true));
    if (unrefreshed.size === 0) stop();
  };
  const stop = cache.subscribe(update);
  update();
}

/**
 * In the app, put the last session back into the query cache, then keep saving it.
 * Call before the screens mount. Outside the app this does nothing.
 */
export async function restoreSavedSession(queryClient: QueryClient): Promise<void> {
  if (!inDesktopApp()) return;
  client = queryClient;
  const limit = new Promise<null>((resolve) => setTimeout(() => resolve(null), READ_LIMIT_MS));
  const saved = await Promise.race([readSaved().catch(() => null), limit]);
  if (saved && saved.build === SCREENS_BUILD) {
    try {
      hydrate(queryClient, saved.queries);
      for (const { queryHash } of saved.queries.queries) {
        const query = queryClient.getQueryCache().get(queryHash);
        if (!query) continue;
        unrefreshed.set(queryHash, query.state.dataUpdatedAt);
        // A read that never goes stale on its own still refetches once, when a screen shows it.
        query.invalidate();
      }
      followRestoredReads(queryClient);
      if (saved.navigation) {
        savedNavigation = Object.freeze({ ...saved.navigation, publicationId: 0, admissionToken: "saved-session" });
        latestNavigation = saved.navigation;
        savedNavigationOnScreen.set(true);
      }
    } catch {
      // A copy that does not restore is replaced by the next save.
    }
  }
  // Save after fresh data arrives. A session that only fails keeps the saved copy as it was.
  queryClient.getQueryCache().subscribe((event) => {
    if (event.type === "updated" && event.action.type === "success" && event.query.meta?.resourceBody === true) scheduleSave();
  });
}

/** The last session's navigation view, for the navigation owner to show until the server answers. */
export function savedNavigationView(): NavigationPublication | undefined {
  return savedNavigation;
}

/** The navigation owner reports each view it shows. A view from the server replaces the saved one. */
export function noteNavigationView(publication: NavigationPublication | undefined, current: boolean): void {
  if (!client || !publication || publication === savedNavigation) return;
  savedNavigationOnScreen.set(false);
  if (!current) return;
  latestNavigation = publication;
  scheduleSave();
}
