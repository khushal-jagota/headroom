/** The JSON projection of `GET /api/updates` (self-update contract, section 10). */
import type { MachineUpdateState, UpdateInterruption, UpdateAttemptView, UpdateState, UpdateAction } from "../../../../server/environments/updates/view_contracts";
export type { MachineUpdateState, UpdateInterruption, UpdateState, UpdateAction };
import type { ClientCompatibility, ClientIdentity } from "../../../../server/client_versions/contracts";

export type UpdateActivity = "idle" | "busy" | "unknown";
export type { UpdateAttemptState } from "../../../../server/environments/updates/view_contracts";

/** The ready release. The page reads only the SHA it installs. */
export type AvailableUpdate = { app_sha: string; compatibility?: ClientCompatibility };

export type UpdateAttempt = UpdateAttemptView;

export type UpdateStatus = {
  /** The server admits ordinary writes, including conversation sends. */
  writes_admitted?: boolean;
  client_versions: readonly ClientIdentity[];
  machines: readonly MachineUpdateState[];
  available_machines: readonly MachineUpdateState[];
  interruption: UpdateInterruption;
  state: UpdateState;
  allowed_actions: UpdateAction[];
  activity: UpdateActivity;
  available: AvailableUpdate | null;
  attempt: UpdateAttempt | null;
  /** The running SHA. */
  app_sha: string;
  /** Plain text: the work an offered update interrupts, or why an update failed. */
  detail: string | null;
  /** Until when a lost connection still counts as updating. */
  deadline_at: string | null;
};

export type InstallUpdateBody = { app_sha: string };
