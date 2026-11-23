import { fetchJson, PlannerFetchError, type FetchOptions } from "./api";
import { SCREEN_REFRESH_WAIT_MS } from "./requestLimit/config";
import { refreshScreen } from "./resourceVersions/refresh";

let ownWriteRefresh: Promise<void> = Promise.resolve();

function startRefresh(): void {
  ownWriteRefresh = refreshScreen().catch(() => undefined);
}

// The browser's write door. The write ends when the server answers, so a busy mark
// means only that the server did not answer yet. The screen then catches up through
// the ordinary refresh, which starts at once and which the caller does not wait for.
// A refused write changed nothing, so it refreshes nothing. A write that got no
// answer can still be saved, so the refresh shows what happened.
export async function mutateJson<T = unknown>(
  path: string,
  options: FetchOptions = {}
): Promise<T> {
  let result: T;
  try {
    result = await fetchJson<T>(path, options);
  } catch (error) {
    if (error instanceof PlannerFetchError && (error.code === "timeout" || error.code === "network")) startRefresh();
    throw error;
  }
  startRefresh();
  return result;
}

/**
 * For a control that closes on save and then shows the saved value from a read.
 * It waits a short, limited time for the refresh, so the old value does not flash.
 */
export async function mutateJsonAndWaitForScreen<T = unknown>(
  path: string,
  options: FetchOptions = {}
): Promise<T> {
  const result = await mutateJson<T>(path, options);
  await screenShowsOwnWrite();
  return result;
}

/** The refresh that the last own write started, or the wait limit, whichever is first. */
export function screenShowsOwnWrite(): Promise<void> {
  let timer: ReturnType<typeof setTimeout>;
  const limit = new Promise<void>((resolve) => { timer = setTimeout(resolve, SCREEN_REFRESH_WAIT_MS); });
  return Promise.race([ownWriteRefresh, limit]).finally(() => clearTimeout(timer));
}
