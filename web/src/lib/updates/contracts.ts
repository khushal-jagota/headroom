/** The JSON projection of `GET /api/updates` (self-update contract, section 10). */
import type { MacStatus } from "../ticketMachines";
import type { MachineUpdateState, UpdateInterruption, UpdateAttemptView } from "../../../../server/environments/updates/view_contracts";
export type { MachineUpdateState, UpdateInterruption };
import type { ClientCompatibility, ClientIdentity } from "../../../../server/client_versions/contracts";

export type UpdateActivity = "idle" | "busy" | "unknown";
export type UpdateState = "up_to_date" | "available" | "updating" | "failed" | "mac_failed" | "mac_unreachable";
export type UpdateAction = "update" | "retry" | "retry_mac";
export type { UpdateAttemptState } from "../../../../server/environments/updates/view_contracts";

/** The ready release. The page reads only the SHA it installs. */
export type AvailableUpdate = { app_sha: string; compatibility?: ClientCompatibility };

export type UpdateAttempt = UpdateAttemptView;

export type UpdateStatus = {
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
  /** Executor health with the Mac update status, for the info dialog only. */
  mac_details: MacStatus | null;
};

export type InstallUpdateBody = { app_sha: string };
