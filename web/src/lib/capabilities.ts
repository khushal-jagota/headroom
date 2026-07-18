/**
 * capabilities.ts — the tri-state runtime capability signal for the Chief pane (S2b plan §5,
 * F11). `/api/meta` is fetched AFTER the child routes mount, so a boolean default would
 * transiently mount the wrong pane. Instead the Chief routes render NEITHER chat path until
 * meta resolves.
 *
 * - "unknown"  — meta not yet resolved: render a placeholder (spinner), never a chat path.
 * - "enabled"  — the relay backend owns the Chief: render the neutral pane.
 * - "disabled" — meta resolved with relay_chief_enabled === false: render the legacy ChatPanel.
 * - "error"    — the meta fetch FAILED: render an error/retry placeholder, NOT the legacy pane
 *                (a failure resolving to "disabled" would mount the wrong pane — the exact
 *                outcome the tri-state exists to prevent).
 *
 * A plain writable store — no resource cache — so the resource-catalogue completeness test is
 * unaffected (this file registers no resource).
 */

import { fetchJson } from "./api";
import { writable } from "svelte/store";

export type RelayChiefCapability = "unknown" | "enabled" | "disabled" | "error";

export const relayChief = writable<RelayChiefCapability>("unknown");

/** Resolve the capability from a successful `/api/meta` payload. ONLY an explicit boolean
 * decides: `true` -> "enabled", `false` -> "disabled" (legacy). Anything else (a missing or
 * non-boolean key) is NOT a valid answer and resolves to "error" (retry placeholder) — never
 * "disabled", so a shape drift can never silently mount the wrong pane. */
export function resolveRelayChiefFromMeta(meta: { relay_chief_enabled?: unknown }): void {
  if (meta.relay_chief_enabled === true) {
    relayChief.set("enabled");
  } else if (meta.relay_chief_enabled === false) {
    relayChief.set("disabled");
  } else {
    relayChief.set("error");
  }
}

/** A meta fetch failure resolves to "error" (NOT "disabled") so the Chief routes show a retry
 * placeholder rather than mounting the legacy pane. */
export function markRelayChiefMetaError(): void {
  relayChief.set("error");
}

/** Re-fetch `/api/meta` and re-resolve the capability. Drives the error-branch retry so a
 * transient meta failure does not leave Chief chat dead until a manual reload. Resets to
 * "unknown" while the retry is in flight, then resolves to enabled/disabled/error. */
export async function retryRelayChiefMeta(): Promise<void> {
  relayChief.set("unknown");
  try {
    const meta = await fetchJson<{ relay_chief_enabled?: unknown }>("/api/meta");
    resolveRelayChiefFromMeta(meta);
  } catch {
    markRelayChiefMetaError();
  }
}
