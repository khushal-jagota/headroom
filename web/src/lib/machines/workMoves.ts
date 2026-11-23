import type { WorkMoveOwner, WorkMoveRequest, WorkMoveResult, WorkMoveBatchResult } from "../../../../server/work_moves/contracts";
import { mutateJson } from "../mutate";

/** A null owner is the separate, explicit Move everything choice. */
export async function submitWorkMove(owner: WorkMoveOwner | null, machineId: string): Promise<WorkMoveResult[]> {
  if (owner === null) {
    const result = await mutateJson<WorkMoveBatchResult>("/api/work-moves/everything", {method: "POST", body: {machine_id: machineId}});
    return result.results;
  }
  const body: WorkMoveRequest = {owner, machine_id: machineId};
  return [await mutateJson<WorkMoveResult>("/api/work-moves", {method: "POST", body})];
}

export function workMoveResultText(result: WorkMoveResult): string {
  return result.error ? `${result.moved ? "Moved, but " : ""}${result.error}` : result.moved ? "Moved" : "Already on this machine";
}
