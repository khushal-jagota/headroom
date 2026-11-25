import type { WorkMoveItem, WorkMoveOperation, WorkMoveOwner, WorkMovePhase, WorkMoveResult } from "../../../../server/work_moves/contracts";
import { fetchJson } from "../api";
import { queryClient } from "../queryClient";

/** A null owner is the separate, explicit Move everything choice. Do not wait for
 * unrelated query refetches after the server accepts this background operation. */
export async function submitWorkMove(owner: WorkMoveOwner | null, machineId: string): Promise<WorkMoveOperation> {
  const operation = await fetchJson<WorkMoveOperation>("/api/work-moves/operations", {
    method: "POST", body: owner === null ? {machine_id: machineId} : {owner, machine_id: machineId}
  });
  void queryClient.invalidateQueries();
  return operation;
}

export function sameWorkMoveOwner(left: WorkMoveOwner, right: WorkMoveOwner): boolean {
  return left.kind === right.kind && (left.kind === "chief" || (right.kind !== "chief" && left.id === right.id));
}

/** Persisted operations, including moves begun elsewhere, determine what a reopened
 * screen shows. A bulk move also supplies the progress for its individual owners. */
export function latestWorkMove(operations: readonly WorkMoveOperation[], owner: WorkMoveOwner | null, machineId?: string): WorkMoveOperation | null {
  const matches = operations.filter(operation => owner === null
    ? operation.everything && (!machineId || operation.machine_id === machineId)
    : operation.items.some(item => sameWorkMoveOwner(item.owner, owner)));
  return matches.reduce<WorkMoveOperation | null>((latest, operation) =>
    latest === null || operation.started_at_ms > latest.started_at_ms ? operation : latest, null);
}

export const workMovePhaseText: Record<WorkMovePhase, string> = {
  queued: "Queued",
  stopping: "Stop the old agent",
  verifying: "Check clean and pushed work",
  recording: "Change the destination",
  recovering: "Recover the workspace",
  starting: "Start the new conversation",
  complete: "Complete",
  failed: "Failed"
};

export const workMoveWaitText: Record<WorkMovePhase, string> = {
  queued: "Wait for this move to start.",
  stopping: "Wait for the old agent and its processes to stop.",
  verifying: "Wait for the source machine to check local work and its remote copy.",
  recording: "Wait for the destination record to change.",
  recovering: "Wait for the destination machine to prepare the workspace and recover the published branch.",
  starting: "Wait for the destination machine to start the agent and accept its recovery prompt.",
  complete: "",
  failed: ""
};

export function workMoveItemPhaseText(item: WorkMoveItem): string {
  return item.phase === "queued" && item.conversation_id !== null
    ? "Recovery prompt queued" : workMovePhaseText[item.phase];
}

export function workMoveItemWaitText(item: WorkMoveItem): string {
  return item.phase === "queued" && item.conversation_id !== null
    ? "Wait for the destination machine to accept its recovery prompt." : workMoveWaitText[item.phase];
}

export function workMoveResultText(result: WorkMoveResult): string {
  return result.error ? `${result.moved ? "Moved, but " : ""}${result.error}` : result.moved ? "Moved" : "Already on this machine";
}

export function workMoveNextAction(item: WorkMoveItem): string {
  if (!item.result?.error) return "";
  return item.result.moved
    ? "The destination changed. Fix the reported problem, then choose “New conversation” there and send a message."
    : "The destination did not change. Fix the reported problem, then retry the move. If the source remains stopped, choose “New conversation” there and send a message.";
}

export function workMoveDuration(startedAt: number, finishedAt: number | null, now: number): string {
  const seconds = Math.max(0, ((finishedAt ?? now) - startedAt) / 1000);
  if (seconds < 60) return `${finishedAt === null ? Math.floor(seconds) : seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
}
