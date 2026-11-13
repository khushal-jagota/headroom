import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import type { ReleaseExecution } from '../../release/contracts.ts';

/** Canonical preparation facts. Database and attempt storage are independent. */
export interface InstallerRequest {
  readonly current_root: string;
  readonly database: string;
  readonly data_directory: string;
  readonly attempt_id: string;
  readonly service_unit: string;
  readonly health_url: string;
  readonly health_timeout_seconds: number;
  readonly node_executable: string;
  readonly admitted_legacy_null_sha: string | null;
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
  service(action: 'stop' | 'start'): Promise<void>;
  capture(): Promise<CaptureOutcome>;
  restore(snapshot: string): Promise<void>;
  healthy(sha: string): Promise<boolean>;
}
export interface InstallerIO {
  stderr(text: string): void;
}
export interface InstallerCommandOptions {
  readonly check: boolean;
  readonly output: 'inherit' | 'ignore';
}
