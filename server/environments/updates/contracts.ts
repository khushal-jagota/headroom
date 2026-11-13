/** Persisted and in-process contracts from Python's update_contracts.py. */
import type { ConversationSystem } from '../../conversation/contracts.ts';
import type { Database } from '../../core/database/contracts.ts';
import type { NumericJSON } from '../../core/http/numeric_json.ts';

export type UpdateMode = 'now' | 'idle';
export type UpdateActivity = 'idle' | 'busy' | 'unknown';
export type UpdateAttemptState = 'waiting' | 'preparing' | 'installing' | 'done' | 'failed';
export type UpdateExperienceState =
  'up_to_date' | 'available' | 'waiting' | 'updating' | 'failed' | 'mac_failed' | 'mac_unreachable';
export type UpdateAction = 'update' | 'cancel' | 'update_now' | 'retry' | 'retry_mac';

export interface RuntimePlatform {
  distribution: string;
  version: string;
  minimum_glibc: string;
}
interface RuntimeBoundary {
  system: string;
  architecture: string;
  platforms: RuntimePlatform[];
  node_major: number;
  cpu_features: string[];
  external_libraries: string[];
  external_executables: string[];
  inventory_sha256: string;
}
export interface PythonRuntimeContract extends RuntimeBoundary {
  version: 1;
  python_version: string;
}
export interface NodeRuntimeContract extends RuntimeBoundary {
  version: 2;
  system: 'linux';
  architecture: 'x86_64';
  node_major: 22;
  minimum_node_version: '22.18.0';
}
export type RuntimeContract = PythonRuntimeContract | NodeRuntimeContract;
export interface AppManifest {
  app_sha: string;
  source_digest: string;
  artifact_digest: string;
  runtime_contract: RuntimeContract | null;
}
export interface PublishedMacUpdate {
  artifact_id: NumericJSON<number>;
  archive_sha256: string;
  archive_size: NumericJSON<number>;
}
export interface PublishedUpdate {
  publication_run_id: NumericJSON<number>;
  artifact_id: NumericJSON<number>;
  proof_artifact_id: NumericJSON<number>;
  manifest: AppManifest;
  archive_sha256: string;
  schema_floor: string;
  expires_at: string;
  mac: PublishedMacUpdate | null;
}
export interface UpdateAttempt {
  attempt_id: string;
  from_sha: string;
  to_sha: string;
  mode: UpdateMode;
  confirm_activity: boolean;
  state: UpdateAttemptState;
  created_at: string;
  updated_at: string;
  release: PublishedUpdate;
  detail: string | null;
  deadline_at: string | null;
  backup: string | null;
  steps: Record<string, string>;
  mac_attempt_id: string | null;
  extra: Record<string, unknown>;
}
export interface UpdateStatus {
  state: UpdateExperienceState;
  allowed_actions: readonly UpdateAction[];
  activity: UpdateActivity;
  available: PublishedUpdate | null;
  attempt: UpdateAttempt | null;
  app_sha: string | null;
  detail: string | null;
  deadline_at: string | null;
}
export interface DeploymentStatus {
  state: 'idle' | 'preparing' | 'restarting' | 'problem' | 'back_up';
  deployed_sha: string | null;
  target_sha: string | null;
  outcome: 'failed' | 'succeeded' | null;
  detail: string | null;
  valid_until: string | null;
  success_display_until: string | null;
}
export type UpdateExperience = readonly [
  UpdateExperienceState,
  readonly UpdateAction[],
  string | null,
  string | null,
];
export interface UpdateExperienceOptions {
  mac_applies: boolean;
  mac_phase: string | null;
  mac_reported_at: string | null;
  mac_report_seconds: number;
  now: string;
}
export interface UpdateProvider {
  available(): Promise<PublishedUpdate | null>;
  download_artifact(
    artifact_id: NumericJSON<number>,
    destination: string,
    signal?: AbortSignal,
  ): Promise<void>;
}
/** Required adapters; callers cannot silently replace an unavailable operation. */
export interface ReleaseStaging {
  stage(provider: UpdateProvider, release: PublishedUpdate, current_root: string): Promise<string>;
}
export interface InstallerOperations {
  require_exact_schema_frontier(database: string, staged_app: string): void | Promise<void>;
  schema_change_pending(database: string, staged_app: string): boolean;
  create_backup(
    database: string,
    backup_directory: string,
    deployed_revision: string,
  ): Promise<string>;
  prepare(options: {
    current_root: string;
    data_directory: string;
    attempt_id: string;
    service_unit: string;
    health_url: string;
    health_timeout_seconds: number;
  }): string[];
  launch(attempt_id: string, command: readonly string[]): Promise<void>;
}
export interface MacUpdateOperations {
  prepare(release: PublishedUpdate): Promise<void>;
  phase(attempt_id: string): string | null;
  reported_at(attempt_id: string): number | null;
  configured(): boolean;
}
export interface UpdateRuntimeContext {
  database: Database;
  conversation(): ConversationSystem | null;
  assert_database_owned(): void;
}
