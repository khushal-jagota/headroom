import type { WorkMoveOwner, WorkMoveRequest, WorkMoveResult, WorkMoveBatchResult } from "../../../../server/work_moves/contracts";
import { mutateJson } from "../mutate";
import { WORK_MOVE_EVERYTHING_LIMIT_MS, WORK_MOVE_LIMIT_MS } from "../requestLimit/config";

/** A null owner is the separate, explicit Move everything choice. */
export async function submitWorkMove(owner: WorkMoveOwner | null, machineId: string): Promise<WorkMoveResult[]> {
  if (owner === null) {
    const result = await mutateJson<WorkMoveBatchResult>("/api/work-moves/everything", {method: "POST", body: {machine_id: machineId}, limitMs: WORK_MOVE_EVERYTHING_LIMIT_MS});
    return result.results;
  }
  const body: WorkMoveRequest = {owner, machine_id: machineId};
  return [await mutateJson<WorkMoveResult>("/api/work-moves", {method: "POST", body, limitMs: WORK_MOVE_LIMIT_MS})];
}

export function workMoveResultText(result: WorkMoveResult): string {
  return result.error ? `${result.moved ? "Moved, but " : ""}${result.error}` : result.moved ? "Moved" : "Already on this machine";
}
