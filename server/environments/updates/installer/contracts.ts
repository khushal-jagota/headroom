import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import type { ReleaseExecution } from '../../release/contracts.ts';

/** Canonical preparation facts. Database and attempt storage are independent. */
export interface InstallerRequest {
  readonly current_root: string;
  readonly database: string;
  readonly data_directory: string;
  readonly backup_directory: string;
  readonly attempt_id: string;
  readonly service_unit: string;
  readonly health_url: string;
  readonly health_timeout_seconds: number;
  readonly node_executable: string;
}
export interface InstallerExecution {
  readonly child_processes: OwnedChildProcesses;
  readonly signal: AbortSignal;
  readonly environment: Readonly<NodeJS.ProcessEnv>;
}
export interface InstallerPhase extends ReleaseExecution {
  readonly deadline: number;
  readonly timeout_seconds: number;
  readonly environment: Readonly<NodeJS.ProcessEnv>;
}
export type CaptureOutcome =
  | { readonly kind: 'returned'; readonly returncode: number }
  | { readonly kind: 'unavailable'; readonly detail: string };
export interface InstallerEffects {
  validate_service?(): Promise<void>;
  recover_unchanged?(sha: string): Promise<boolean>;
  finish_success?(): Promise<string | null>;
  finish_rollback?(): Promise<string | null>;
  service(action: 'stop' | 'start'): Promise<void>;
  capture(): Promise<CaptureOutcome>;
  /** After the stop and before the swap: the current app snapshots the quiet database. */
  snapshot(deployed_revision: string, lifetime_lock_fd: number): Promise<void>;
  restore(snapshot: string, lifetime_lock_fd?: number): Promise<void>;
  /** After the swap and before start: the new app brings the stopped database to its head. */
  schema_steps(lifetime_lock_fd: number, backup_taken: boolean): Promise<void>;
  healthy(sha: string): Promise<boolean>;
}
export interface InstallerIO {
  stderr(text: string): void;
}
export interface InstallerCommandOptions {
  readonly check: boolean;
  readonly output: 'inherit' | 'ignore';
  readonly lifetime_lock_fd?: number;
}
