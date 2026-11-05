/** The JSON projection of `GET /api/updates` (self-update contract, section 10). */
import type { MacStatus } from "../ticketMachines";

export type UpdateMode = "now" | "idle";
export type UpdateActivity = "idle" | "busy" | "unknown";
export type UpdateState = "up_to_date" | "available" | "waiting" | "updating" | "failed" | "mac_failed" | "mac_unreachable";
export type UpdateAction = "update" | "cancel" | "update_now" | "retry" | "retry_mac";
export type UpdateAttemptState = "waiting" | "preparing" | "installing" | "done" | "failed";

/** The ready release. The page reads only the SHA it installs. */
export type AvailableUpdate = { app_sha: string };

export type UpdateAttempt = {
  attempt_id: string;
  from_sha: string;
  to_sha: string;
  mode: UpdateMode;
  state: UpdateAttemptState;
  detail: string | null;
  deadline_at: string | null;
};

export type UpdateStatus = {
  state: UpdateState;
  allowed_actions: UpdateAction[];
  activity: UpdateActivity;
  available: AvailableUpdate | null;
  attempt: UpdateAttempt | null;
  /** The running SHA. */
  app_sha: string;
  /** A plain sentence for the failed states. */
  detail: string | null;
  /** Until when a lost connection still counts as updating. */
  deadline_at: string | null;
  /** Executor health with the Mac update status, for the info dialog only. */
  mac_details: MacStatus | null;
};

export type InstallUpdateBody = { app_sha: string; mode: UpdateMode; confirm_activity: boolean };
export type UpdateNowBody = { confirm_activity: boolean };
