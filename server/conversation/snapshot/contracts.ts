/** Panels-owned backend cards. Provider protocols stay behind adapters. */
import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import type { JsonNumber } from '../../core/http/numeric_json.ts';
import type { Database } from '../../core/database/contracts.ts';
import type { BackendStateStore } from '../backend_state.ts';
import type { BackendCommandOptions } from './commands.ts';
import type { BackendUsageResult } from '../backend_usage/contracts.ts';
import type { RemoteCatalogSource } from '../model_resolution/contracts.ts';
import type { DriverSignInController } from '../../drivers/core/sign_in.ts';
import type { DriverStatus } from '../../drivers/core/status.ts';
import type { StopOptions } from '../../lifecycle/contracts.ts';
export type BackendInstallMethod =
  'npm_global' | 'bun_global' | 'pnpm_global' | 'homebrew' | 'native' | 'manual_only';
export type BackendIdentityStatus = 'authenticated' | 'unauthenticated' | 'unknown';
export interface BackendIdentity {
  readonly status: BackendIdentityStatus;
  readonly account_label: string | null;
  readonly detail: string | null;
  readonly login_command: string | null;
}
export interface BackendModel {
  readonly model_id: string;
  readonly display_name: string | null;
  readonly detail: string | null;
  readonly reasoning_effort_options: readonly string[] | null;
  readonly selected_variant: string | null;
  readonly version: readonly (number | JsonNumber)[];
  readonly resolved_model_id: string | null;
}
export interface BackendUpdateAdvisory {
  readonly install_method: BackendInstallMethod;
  readonly update_command: readonly string[] | null;
  readonly latest_version: string | null;
  readonly update_available: boolean;
  readonly detail: string;
}
export interface BackendSnapshot {
  readonly backend_key: BackendInstanceId;
  readonly installed: boolean;
  readonly executable_path: string | null;
  readonly version: string | null;
  readonly identity: BackendIdentity | null;
  readonly available_models: readonly BackendModel[];
  readonly reasoning_effort_options: readonly string[];
  readonly default_model_id: string | null;
  readonly default_reasoning_effort: string | null;
  readonly update_advisory: BackendUpdateAdvisory | null;
  readonly diagnoses: readonly string[];
  readonly models_complete: boolean;
}
export interface BackendUpdateResult {
  readonly outcome: 'succeeded' | 'unchanged' | 'failed';
  readonly detail: string;
  readonly output_tail: string;
}
export interface CommandOutcome {
  readonly exit_code: number;
  readonly standard_output: string;
  readonly standard_error: string;
}
export interface BackendProbeEnvironment {
  shutdown?(): Promise<void>;
  executable_path(executable_name: string): string | null;
  configured_executable_path(executable_name: string): string | null;
  real_path(path: string): string;
  user_local_npm_prefix(): string;
  prefix_is_owned_and_writable(prefix: string): boolean;
  run(argv: readonly string[], options: BackendCommandOptions): Promise<CommandOutcome>;
  latest_released_version(
    package_name: string,
    options: { timeout_seconds: number; signal?: AbortSignal },
  ): Promise<string | null>;
}
export interface SnapshotOptions {
  readonly signal?: AbortSignal;
  readonly refresh?: boolean;
  readonly discover_update_advisory?: boolean;
}
export interface BackendSnapshotSource {
  retained(backend_key: BackendInstanceId): BackendSnapshot | null;
  snapshot(backend_key: BackendInstanceId, options?: SnapshotOptions): Promise<BackendSnapshot>;
}
export interface BackendSnapshotServiceContract extends BackendSnapshotSource {
  snapshots(options?: SnapshotOptions): Promise<readonly BackendSnapshot[]>;
  update_backend(
    backend_key: BackendInstanceId,
    options?: { signal?: AbortSignal },
  ): Promise<BackendUpdateResult>;
}
export interface BackendHttpRuntime {
  shutdown?(options: StopOptions): Promise<void>;
  /** Actual cleanup completion, independent of any shutdown deadline observer. */
  settled(): Promise<void>;
  readonly database: Database;
  backend_snapshots: BackendSnapshotServiceContract;
  readonly backend_state: BackendStateStore | null;
  readonly backend_usage: {
    refresh(
      backend_key: BackendInstanceId,
      options?: { signal?: AbortSignal },
    ): Promise<BackendUsageResult>;
  };
  /** Card order and the unknown-backend choice list. */
  readonly backend_ids: readonly BackendInstanceId[];
  /** The backend's sign-in, or null when Panels cannot sign it in. */
  sign_in(backend_key: string): DriverSignInController | null;
  readonly machine_catalog: (machine_id: string) => RemoteCatalogSource | null;
}
/** One backend card: the instance it describes and the driver status that probes it. */
export interface BackendCardSource {
  readonly instance_id: BackendInstanceId;
  /** Machine mode runs this executable's `--version` before reusing a kept card. */
  readonly executable_name: string;
  readonly status: DriverStatus;
}
export interface BackendSnapshotServiceOptions {
  /** Card order. */
  readonly sources: readonly BackendCardSource[];
  readonly keep_complete_model_list?: (snapshot: BackendSnapshot) => void | Promise<void>;
  readonly environment: BackendProbeEnvironment;
  readonly now?: () => Date;
  readonly machine_catalog?: boolean;
}
export interface CatalogAnswer {
  readonly models: readonly BackendModel[];
  readonly reasoning_effort_options: readonly string[];
  readonly default_model_id: string | null;
  readonly default_reasoning_effort: string | null;
  readonly diagnoses: readonly string[];
  readonly complete: boolean;
}
export type CatalogReader = (options: {
  readonly environment: BackendProbeEnvironment;
  readonly executable_path: string;
  readonly version: string | null;
  readonly refresh: boolean;
}) => Promise<CatalogAnswer>;
