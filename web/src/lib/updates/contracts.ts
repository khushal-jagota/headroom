/** The JSON projection of environments/update_contracts.py. */
export type UpdateMode = "now" | "idle";
export type UpdateActivity = "idle" | "busy" | "unknown";
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
};

export type OwnerUpdateRequest = {
  request_id: string;
  release: PublishedUpdate;
  mode: UpdateMode;
  state: UpdateRequestState;
  created_at: string;
  detail: string | null;
  confirm_activity?: boolean;
};

export type UpdateStatus = {
  configured: boolean;
  available: PublishedUpdate | null;
  request: OwnerUpdateRequest | null;
  activity: UpdateActivity;
  detail: string | null;
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
