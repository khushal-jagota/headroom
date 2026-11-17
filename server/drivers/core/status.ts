/** The driver half of the backend card pipeline. */
import type {
  BackendProbeEnvironment,
  BackendSnapshot,
} from '../../conversation/snapshot/contracts.ts';

/** What the card probe runs and says for one executable. Plain data. */
export interface ProbeRecipe {
  readonly executable_name: string;
  readonly missing_binary_diagnosis: string;
  readonly identity_arguments: readonly string[] | null;
  readonly login_command: string | null;
  readonly registry_package_name: string | null;
  readonly native_path_marker: string | null;
  readonly native_update_command: readonly string[] | null;
  readonly manual_only_detail: string;
}
export interface StatusProbeContext {
  /** The one probe environment the backend runtime owns and shares. */
  readonly environment: BackendProbeEnvironment;
  readonly refresh: boolean;
  readonly discover_update_advisory: boolean;
  readonly signal?: AbortSignal;
  readonly now: Date;
  /** The numerical version of a model id, asked of every driver in list order. */
  model_version(model_id: string): readonly number[];
}
/** A card before the service stamps its backend key. */
export type BackendSnapshotDraft = Omit<BackendSnapshot, 'backend_key'>;
export interface DriverStatus {
  probe(context: StatusProbeContext): Promise<BackendSnapshotDraft>;
  /** A value whose change makes a kept card stale. Null: nothing to compare. */
  freshness_key?(now: Date): string | null;
  /** Machine mode re-runs `--version` before reusing a kept card. */
  readonly version_detects_upgrade: boolean;
}
