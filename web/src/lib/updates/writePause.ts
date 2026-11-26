import { get, writable } from "svelte/store";
import type { UpdateStatus } from "./contracts";

export type UpdatePause = { blocked: boolean; openingScreens: boolean };
export type UpdateRefusal = "update_in_progress" | "screens_outdated";

/** The update status owns admission. A refusal closes the gap between two status reads. */
export function createUpdateWritePause() {
  const state = writable<UpdatePause>({ blocked: false, openingScreens: false });
  function observe(status: UpdateStatus | undefined, bootSha: string | null, starting = false): void {
    if (!status) {
      if (starting) state.set({ blocked: true, openingScreens: false });
      return;
    }
    const openingScreens = Boolean(bootSha && status.app_sha && bootSha !== status.app_sha);
    const preparing = status.attempt?.state === "preparing" || status.attempt?.state === "installing";
    state.set({ blocked: starting || preparing || status.writes_admitted === false || openingScreens, openingScreens });
  }
  function refuse(code: UpdateRefusal): void {
    state.set({ blocked: true, openingScreens: code === "screens_outdated" });
  }
  function wait(signal?: AbortSignal | null): Promise<void> {
    if (signal?.aborted) return Promise.reject(signal.reason);
    if (!get(state).blocked) return Promise.resolve();
    return new Promise<void>((resolve, reject) => {
      const abort = () => { unsubscribe(); reject(signal?.reason); };
      const unsubscribe = state.subscribe(value => {
        if (!value.blocked) {
          signal?.removeEventListener("abort", abort);
          unsubscribe();
          resolve();
        }
      });
      signal?.addEventListener("abort", abort, { once: true });
    });
  }
  return { state, observe, refuse, wait };
}

const sharedPause = createUpdateWritePause();
export const updatePause = sharedPause.state;
export const observeUpdateAdmission = sharedPause.observe;
export const waitForUpdateWrites = sharedPause.wait;
export const noteUpdateRefusal = sharedPause.refuse;

/** Only these two pre-handler responses prove that no write ran. */
export async function updateRefusal(response: Response): Promise<UpdateRefusal | null> {
  if (response.headers.get("X-Panels-Write-Admitted") !== "false" || response.status !== 409 && response.status !== 503) return null;
  const payload = await response.clone().json().catch(() => null);
  const code = payload?.error?.code;
  return response.status === 503 && code === "update_in_progress" || response.status === 409 && code === "screens_outdated" ? code : null;
}
