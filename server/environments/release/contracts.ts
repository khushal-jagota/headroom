/** Application identity and the pure database frontier. */
import { AppManifestValidationFailure } from '../command_contracts.ts';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
export type {
  AppManifest,
  NodeRuntimeContract,
  PythonRuntimeContract,
  RuntimeContract,
} from '../updates/contracts.ts';
export class AppValidationError extends AppManifestValidationFailure {
  constructor(message: string) {
    super('value', message);
    this.name = 'AppValidationError';
  }
}
export interface AppValidationOptions {
  expected_sha?: string;
  require_runtime?: boolean;
  verify_artifact_digest?: boolean;
}
export interface MigrationGraph {
  readonly version: 1;
  readonly source_digest: string;
  readonly revisions: Readonly<Record<string, readonly string[]>>;
}

/** Null deadline is explicit standalone build policy, never a server default. */
export interface ReleaseExecution {
  readonly child_processes: OwnedChildProcesses;
  readonly signal: AbortSignal;
  readonly deadline: number | null;
}
