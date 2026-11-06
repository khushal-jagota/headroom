import { writable } from "svelte/store";

export type ReleaseSnapshot = {
  updateAvailable: boolean;
  reloadRequested: boolean;
};

type MetaResponse = {
  app_sha?: unknown;
};

type ReleaseDocument = Pick<Document, "addEventListener" | "removeEventListener" | "visibilityState">;
type ReleaseWindow = Pick<Window, "addEventListener" | "removeEventListener">;

type ReleaseMonitorOptions = {
  bootSha: string | null;
  document: ReleaseDocument;
  window: ReleaseWindow;
  fetchMeta?: typeof fetch;
  reload?: () => void;
};

export function readBootAppSha(document: Pick<Document, "querySelector">): string | null {
  const value = document.querySelector<HTMLMetaElement>('meta[name="panels-app-sha"]')?.content.trim();
  return value || null;
}

export function createReleaseMonitor(options: ReleaseMonitorOptions) {
  const fetchMeta = options.fetchMeta ?? fetch;
  const reload = options.reload ?? (() => window.location.reload());
  let snapshot: ReleaseSnapshot = {
    updateAvailable: false,
    reloadRequested: false
  };
  let started = false;
  let reloadStarted = false;
  let checkInFlight: Promise<boolean> | null = null;
  const subscribers = new Set<(value: ReleaseSnapshot) => void>();

  function publish(update: Partial<ReleaseSnapshot>): void {
    snapshot = { ...snapshot, ...update };
    for (const subscriber of subscribers) subscriber(snapshot);
  }

  function reloadIfReady(): void {
    if (
      !reloadStarted &&
      snapshot.reloadRequested &&
      snapshot.updateAvailable
    ) {
      reloadStarted = true;
      reload();
    }
  }

  function observeAppSha(appSha: string | null): void {
    if (!options.bootSha || !appSha) return;
    const updateAvailable = appSha !== options.bootSha;
    if (!updateAvailable) reloadStarted = false;
    publish({ updateAvailable, reloadRequested: updateAvailable ? snapshot.reloadRequested : false });
    reloadIfReady();
  }

  function check(): Promise<boolean> {
    if (checkInFlight) return checkInFlight;
    checkInFlight = (async () => {
      try {
        const response = await fetchMeta("/api/meta", { cache: "no-store" });
        if (!response.ok) return false;
        const meta = (await response.json()) as MetaResponse;
        if (options.bootSha && typeof meta.app_sha === "string") {
          const updateAvailable = meta.app_sha !== options.bootSha;
          if (!updateAvailable) reloadStarted = false;
          publish({
            updateAvailable,
            reloadRequested: updateAvailable ? snapshot.reloadRequested : false
          });
          return !updateAvailable;
        }
        return options.bootSha === null;
      } catch {
        // A failed update check leaves the current app unchanged.
        return false;
      } finally {
        checkInFlight = null;
      }
    })();
    return checkInFlight;
  }

  async function checkThenReloadIfReady(): Promise<void> {
    await check();
    reloadIfReady();
  }

  function onVisibilityChange(): void {
    if (options.document.visibilityState === "visible") void checkThenReloadIfReady();
  }

  function onPageShow(): void {
    void checkThenReloadIfReady();
  }

  function start(): void {
    if (started) return;
    started = true;
    options.document.addEventListener("visibilitychange", onVisibilityChange);
    options.window.addEventListener("pageshow", onPageShow);
    void check();
  }

  function stop(): void {
    if (!started) return;
    started = false;
    options.document.removeEventListener("visibilitychange", onVisibilityChange);
    options.window.removeEventListener("pageshow", onPageShow);
  }

  function requestReload(): void {
    if (!snapshot.updateAvailable) return;
    publish({ reloadRequested: true });
    void checkThenReloadIfReady();
  }

  function subscribe(subscriber: (value: ReleaseSnapshot) => void): () => void {
    subscribers.add(subscriber);
    subscriber(snapshot);
    return () => subscribers.delete(subscriber);
  }

  return { start, stop, check, requestReload, subscribe, observeAppSha };
}

// One monitor for the whole app. ShellStatus is mounted once per breakpoint and both
// mounts stay in the DOM, so a monitor owned by the component would poll twice and
// hold two independent reload requests.
export const releaseState = writable<ReleaseSnapshot>({
  updateAvailable: false,
  reloadRequested: false
});

let sharedMonitor: ReturnType<typeof createReleaseMonitor> | null = null;
let sharedBootSha: string | null = null;
let unsubscribeShared: (() => void) | null = null;

export function startReleaseMonitor(): void {
  if (sharedMonitor) return;
  sharedBootSha = readBootAppSha(document);
  const monitor = createReleaseMonitor({ bootSha: sharedBootSha, document, window });
  sharedMonitor = monitor;
  unsubscribeShared = monitor.subscribe((value) => releaseState.set(value));
  monitor.start();
}

export function stopReleaseMonitor(): void {
  if (!sharedMonitor) return;
  sharedMonitor.stop();
  unsubscribeShared?.();
  unsubscribeShared = null;
  sharedMonitor = null;
  sharedBootSha = null;
}

export function requestReleaseReload(): void {
  sharedMonitor?.requestReload();
}

/** Attach the page version. The server rejects an outdated browser. */
export async function mutationHeaders(headers?: HeadersInit): Promise<Record<string, string>> {
  const result = Object.fromEntries(new Headers(headers).entries());
  if (sharedBootSha) result["X-Panels-App-SHA"] = sharedBootSha;
  return result;
}

/** Reconciled update status supplies the running version without a second status read. */
export function observeRunningAppSha(appSha: string | null): void {
  sharedMonitor?.observeAppSha(appSha);
}
