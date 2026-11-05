/** The JSON projection of environments/update_contracts.py. */
export type UpdateMode = "now" | "idle";
export type UpdateActivity = "idle" | "busy" | "unknown";
export type UpdateExperienceState = "up_to_date" | "available" | "waiting" | "updating" | "restarting" | "failed" | "complete";
export type UpdateAction = "update" | "cancel" | "update_now" | "retry";
export type UpdateRequestState =
  | "pending" | "dispatched" | "applying" | "applied" | "cancelled" | "failed";

export type PublishedUpdate = {
  publication_run_id: number;
  artifact_id: number;
  proof_artifact_id: number;
  manifest: { app_sha: string; source_digest: string; artifact_digest: string };
  archive_sha256: string;
  schema_floor: string;
  expires_at: string;
  mac?: { artifact_id: number; archive_sha256: string; archive_size: number } | null;
};

export type OwnerUpdateRequest = {
  request_id: string;
  release: PublishedUpdate;
  mode: UpdateMode;
  state: UpdateRequestState;
  created_at: string;
  detail: string | null;
  confirm_activity?: boolean;
  dispatched_at?: string | null;
  deadline_at?: string | null;
  deployment_id?: string | null;
};

export type UpdateStatus = {
  mac_status?: import("../ticketMachines").MacStatus;
  configured: boolean;
  available: PublishedUpdate | null;
  request: OwnerUpdateRequest | null;
  activity: UpdateActivity;
  detail: string | null;
  state: UpdateExperienceState;
  allowed_actions: UpdateAction[];
  phase: string | null;
  app_sha: string | null;
  deadline_at: string | null;
  restart_deadline_at: string | null;
};

export type CreateUpdateRequest = {
  publication_run_id: number;
  artifact_id: number;
  proof_artifact_id: number;
  app_sha: string;
  artifact_digest: string;
  archive_sha256: string;
  mode: UpdateMode;
  confirm_activity: boolean;
};
