export const COMPOSITION_STATE_EVENT = "panels:composition-state";

export type ReleaseSnapshot = {
  updateAvailable: boolean;
  compositionActive: boolean;
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
    compositionActive: false,
    reloadRequested: false
  };
  let started = false;
  let reloadStarted = false;
  let checkInFlight: Promise<void> | null = null;
  const subscribers = new Set<(value: ReleaseSnapshot) => void>();

  function publish(update: Partial<ReleaseSnapshot>): void {
    snapshot = { ...snapshot, ...update };
    for (const subscriber of subscribers) subscriber(snapshot);
  }

  function reloadIfReady(): void {
    if (
      !reloadStarted &&
      snapshot.reloadRequested &&
      snapshot.updateAvailable &&
      !snapshot.compositionActive
    ) {
      reloadStarted = true;
      reload();
    }
  }

  function check(): Promise<void> {
    if (checkInFlight) return checkInFlight;
    checkInFlight = (async () => {
      try {
        const response = await fetchMeta("/api/meta", { cache: "no-store" });
        if (!response.ok) return;
        const meta = (await response.json()) as MetaResponse;
        if (options.bootSha && typeof meta.app_sha === "string") {
          const updateAvailable = meta.app_sha !== options.bootSha;
          if (!updateAvailable) reloadStarted = false;
          publish({
            updateAvailable,
            reloadRequested: updateAvailable ? snapshot.reloadRequested : false
          });
        }
      } catch {
        // A failed release check must not disturb the current app. Resume retries it.
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

  async function recheckThenReloadIfReady(): Promise<void> {
    const activeCheck = checkInFlight;
    if (activeCheck) await activeCheck;
    await check();
    reloadIfReady();
  }

  function onVisibilityChange(): void {
    if (options.document.visibilityState === "visible") void checkThenReloadIfReady();
  }

  function onPageShow(): void {
    void checkThenReloadIfReady();
  }

  function onCompositionState(event: Event): void {
    const active = (event as CustomEvent<{ active?: unknown }>).detail?.active === true;
    publish({ compositionActive: active });
    if (!active) void recheckThenReloadIfReady();
  }

  function start(): void {
    if (started) return;
    started = true;
    options.document.addEventListener("visibilitychange", onVisibilityChange);
    options.document.addEventListener(COMPOSITION_STATE_EVENT, onCompositionState);
    options.window.addEventListener("pageshow", onPageShow);
    void check();
  }

  function stop(): void {
    if (!started) return;
    started = false;
    options.document.removeEventListener("visibilitychange", onVisibilityChange);
    options.document.removeEventListener(COMPOSITION_STATE_EVENT, onCompositionState);
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

  return { start, stop, check, requestReload, subscribe };
}
