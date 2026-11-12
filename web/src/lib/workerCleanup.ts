import { mutateJson } from "./mutate";

export type WorkerCleanupResult = { complete: boolean; reason: string; scope?: string };

export function recheckWorkerCleanup(ticketId: string): Promise<WorkerCleanupResult> {
  return mutateJson<WorkerCleanupResult>(
    `/api/tickets/${encodeURIComponent(ticketId)}/recheck-worker-cleanup`,
    { method: "POST", body: {} }
  );
}

export function workerCleanupMessage(result: WorkerCleanupResult): string {
  return `Worker cleanup ${result.complete ? "complete" : "not proven"}. ${result.reason}`;
}
