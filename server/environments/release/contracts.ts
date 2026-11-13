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

export type ArchiveUnpackRequest = { readonly spool_root: string } & (
  | {
      readonly purpose: 'application';
      readonly destination: string;
      readonly archive_sha256: string;
      readonly expected_sha: string;
    }
  | {
      readonly purpose: 'mac-inspection';
      readonly archive_sha256: string;
      readonly archive_size: number;
    }
);
/** These two kinds alone enter the Mac service's deliberate conflict boundary. */
export class MacArchiveInspectionError extends Error {
  readonly kind: 'archive' | 'refusal';
  constructor(kind: 'archive' | 'refusal', message: string) {
    super(message);
    this.kind = kind;
  }
}
export class MacArchiveJsonError extends Error {
  readonly original: Error;
  constructor(original: Error) {
    super(original.message);
    this.original = original;
  }
}
export interface MacArchiveInspection {
  readonly manifest: unknown;
  readonly python: boolean;
}
export type MacInspectionFailure = { type: 'mac-inspection-error'; message: string } & (
  | { kind: 'archive' | 'refusal' }
  | {
      kind: 'os';
      name: string;
      code: string;
      errno: number;
      syscall?: string;
      path?: string;
      dest?: string;
    }
  | { kind: 'json' | 'unexpected'; name: string }
);
