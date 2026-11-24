import { mutateJson } from "./mutate";
import { WORKER_CLEANUP_RECHECK_LIMIT_MS } from "./requestLimit/config";

export type WorkerCleanupResult = { complete: boolean; reason: string; scope?: string };

export function recheckWorkerCleanup(ticketId: string): Promise<WorkerCleanupResult> {
  return mutateJson<WorkerCleanupResult>(
    `/api/tickets/${encodeURIComponent(ticketId)}/recheck-worker-cleanup`,
    { method: "POST", body: {}, limitMs: WORKER_CLEANUP_RECHECK_LIMIT_MS }
  );
}

export function workerCleanupMessage(result: WorkerCleanupResult): string {
  return `Worker cleanup ${result.complete ? "complete" : "not proven"}. ${result.reason}`;
}
