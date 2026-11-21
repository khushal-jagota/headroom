import type { WorkerSetup } from "./navigation/contracts";
import { formatDuration } from "./conversation/transcript";

const WORKER_SETUP_STEP_LABELS: Record<WorkerSetup["step"], string> = {
  preparing_workspace: "preparing workspace",
  starting_worker: "starting Worker",
};

/** What a Ticket reads while its Worker is being prepared: "Setting up · preparing workspace · 1m 40s". */
export function workerSetupSentence(setup: Pick<WorkerSetup, "step">, elapsedSeconds: number | null): string {
  const step = WORKER_SETUP_STEP_LABELS[setup.step];
  return ["Setting up", ...(step ? [step] : []), ...(elapsedSeconds === null ? [] : [formatDuration(elapsedSeconds)])].join(" · ");
}

/** The instant setup began, in the milliseconds the elapsed clock counts from. */
export function workerSetupStartedAtUnixMilliseconds(setup: WorkerSetup | null | undefined): number | null {
  return setup ? setup.began_at * 1_000 : null;
}
