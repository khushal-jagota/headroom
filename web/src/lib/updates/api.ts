import { get, writable } from "svelte/store";
import { PlannerFetchError } from "../api";
import { queryClient } from "../queryClient";
import { UPDATE_STARTING_LIMIT_MS } from "./config";
import type { InstallUpdateBody, UpdateMode, UpdateNowBody, UpdateStatus } from "./contracts";

/** One owner press, mapped to its contract route. */
export type UpdateActionRequest =
  | { action: "install"; mode: UpdateMode }
  | { action: "cancel" | "now" | "retry" | "retry_mac" };

/** The server answer a press that starts an update was made against, until the answer moves.
 * Both card mounts read it, so the card shows Updating… at once. An attempt can be
 * recorded just after the action route answers, so the first read can still be the old answer. */
export const updateStarting = writable<string | null>(null);

const answerKey = (status: UpdateStatus) => `${status.state}:${status.attempt?.attempt_id ?? ""}`;

/** Clear the press once the server's answer is no longer the one it was made against. */
export function settleUpdateStarting(status: UpdateStatus | undefined): void {
  const pressedAgainst = get(updateStarting);
  if (pressedAgainst !== null && status && answerKey(status) !== pressedAgainst) updateStarting.set(null);
}

/** The route and body for an action, from the server answer the owner acted on. */
export function updateActionRoute(status: UpdateStatus, request: UpdateActionRequest): { path: string; body?: InstallUpdateBody | UpdateNowBody } {
  if (request.action === "install") {
    if (!status.available) throw new Error("This update is no longer available.");
    // The dialog's activity note is the confirmation for Update now.
    return { path: "/api/updates/install",
      body: { app_sha: status.available.app_sha, mode: request.mode, confirm_activity: request.mode === "now" } };
  }
  if (!status.attempt) throw new Error("This update changed. Close this dialog and try again.");
  const attempt = `/api/updates/attempts/${encodeURIComponent(status.attempt.attempt_id)}`;
  switch (request.action) {
    case "cancel": return { path: `${attempt}/cancel` };
    case "now": return { path: `${attempt}/now`, body: { confirm_activity: true } };
    case "retry": return { path: `${attempt}/retry` };
    case "retry_mac": return { path: `${attempt}/retry-mac` };
  }
}

let pressInFlight = false;

/** Post an owner update action and refresh the server's answer.
 *
 * Update controls name the running SHA they were shown, not the page's boot SHA, so
 * a page left behind by an earlier update can still act. The server refuses a press
 * made against a version that has since changed. A second press while one is in
 * flight is ignored.
 */
export async function performUpdateAction(status: UpdateStatus, request: UpdateActionRequest): Promise<void> {
  if (pressInFlight) return;
  const { path, body } = updateActionRoute(status, request);
  const starts = request.action !== "cancel" && !(request.action === "install" && request.mode === "idle");
  const pressedAgainst = answerKey(status);
  pressInFlight = true;
  if (starts) {
    updateStarting.set(pressedAgainst);
    setTimeout(() => { if (get(updateStarting) === pressedAgainst) updateStarting.set(null); }, UPDATE_STARTING_LIMIT_MS);
  }
  try {
    let response: Response;
    try {
      response = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Panels-App-SHA": status.app_sha },
        body: body === undefined ? undefined : JSON.stringify(body)
      });
    } catch { throw new PlannerFetchError("network error", "network"); }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const message = typeof payload.detail === "string" ? payload.detail : payload.error?.message;
      throw new Error(typeof message === "string" ? message : `HTTP ${response.status}`);
    }
  } catch (error) {
    if (starts) updateStarting.set(null);
    throw error;
  } finally {
    pressInFlight = false;
    await queryClient.invalidateQueries({ queryKey: ["updates"] });
  }
}
