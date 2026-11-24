/** Persisted and in-process update contracts. */
import type {
  UpdateMode,
  UpdateAttemptState,
  MachineUpdateState,
  UpdateInterruption,
  UpdateAttemptView,
} from './view_contracts.ts';
export type {
  UpdateMode,
  UpdateAttemptState,
  MachineUpdateState,
  UpdateInterruption,
  UpdateAttemptView,
} from './view_contracts.ts';
import type { ConversationSystem } from '../../conversation/contracts.ts';
import type { ExecutorIdentity } from '../../execution_identity.ts';
import type { Database } from '../../core/database/contracts.ts';
import type { ClientCompatibility } from '../../client_versions/contracts.ts';

export type UpdateActivity = 'idle' | 'busy' | 'unknown';
export type UpdateExperienceState =
  | 'up_to_date'
  | 'check_unavailable'
  | 'available'
  | 'updating'
  | 'failed'
  | 'mac_failed'
  | 'mac_unreachable';
export type UpdateAction = 'update' | 'retry' | 'retry_mac';

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
export interface LinuxNodeRuntimeContract extends RuntimeBoundary {
  version: 2;
  system: 'linux';
  architecture: 'x86_64';
  node_major: 22;
  minimum_node_version: '22.18.0';
}
export interface DarwinNodeRuntimeContract {
  version: 2;
  system: 'darwin';
  architecture: 'arm64';
  role: 'home';
  minimum_os: '15.0';
  node_major: 22;
  minimum_node_version: '22.18.0';
  inventory_sha256: string;
  external_libraries: string[];
}
export type NodeRuntimeContract = LinuxNodeRuntimeContract | DarwinNodeRuntimeContract;
export type RuntimeContract = NodeRuntimeContract;
export interface AppManifest {
  app_sha: string;
  source_digest: string;
  artifact_digest: string;
  runtime_contract: RuntimeContract | null;
}
export type ArtifactSource = 'github-release';
export interface ReleaseParts {
  server: { app_sha: string; artifact_digest: string };
  screens: { sha256: string };
  machines: Record<string, { archive_sha256: string; build_id: string | null }>;
}
export interface PublishedMacUpdate {
  artifact_source?: ArtifactSource;
  artifact_id: number;
  archive_sha256: string;
  archive_size: number;
}
export type PublishedLinuxExecutorUpdate = PublishedMacUpdate;
export interface PublishedUpdate {
  artifact_source?: ArtifactSource;
  parts?: ReleaseParts;
  /** The offered release's limits, never the running server's limits. */
  compatibility?: ClientCompatibility;
  publication_run_id: number;
  artifact_id: number;
  proof_artifact_id: number;
  manifest: AppManifest;
  archive_sha256: string;
  schema_floor: string;
  expires_at: string | null;
  mac: PublishedMacUpdate | null;
  linux_executor?: PublishedLinuxExecutorUpdate | null;
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
  /** The installer snapshots the stopped database before it changes anything. */
  snapshot_required: boolean;
  steps: Record<string, string>;
  mac_attempt_id: string | null;
  extra: Record<string, unknown>;
}
export interface MachineUpdateMachine {
  machine_id: string;
  name: string;
  platform: string;
  present: boolean;
  identity: ExecutorIdentity | null;
}
export interface UpdateStatus {
  machines: readonly MachineUpdateState[];
  available_machines: readonly MachineUpdateState[];
  interruption: UpdateInterruption;
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
    artifact_id: number,
    destination: string,
    signal?: AbortSignal,
    source?: ArtifactSource,
  ): Promise<void>;
}
/** Required adapters; callers cannot silently replace an unavailable operation. */
export interface ReleaseStaging {
  stage(provider: UpdateProvider, release: PublishedUpdate, current_root: string): Promise<string>;
}
export interface InstallerOperations {
  readonly requires_snapshot?: boolean;
  /** Refuse a database off the staged chain. True when the installer must run steps. */
  node_schema_steps_pending(database: string, staged_app: string): boolean | Promise<boolean>;
  prepare(options: {
    current_root: string;
    database: string;
    data_directory: string;
    backup_directory: string;
    attempt_id: string;
    service_unit: string;
    health_url: string;
    health_timeout_seconds: number;
  }): string[];
  launch(attempt_id: string, command: readonly string[]): Promise<void>;
}
export interface MacUpdateOperations {
  machine_states(release?: PublishedUpdate | null): MachineUpdateState[];
  retry_machine(machine_id: string, attempt_id: string): void;
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
