/** Existing VPS/Mac update wire channel. The Python Mac archive format stays unchanged. */
import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import type { MacUpdateOperations } from '../contracts.ts';
import type { UpdateMacReads } from '../routes.ts';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import type { PresenceJudgement, PresenceRecord } from '../../../executor/contracts.ts';
import type { NumericJSON } from '../../../core/http/numeric_json.ts';
import type { FileUpdateAttemptStore } from '../attempts.ts';
import type { PublishedUpdate, UpdateMode, UpdateProvider } from '../contracts.ts';

export type MacUpdatePhase =
  'waiting' | 'downloading' | 'ready_to_restart' | 'current' | 'failed' | 'reverted';
export interface MacUpdateTarget {
  readonly request_id: string;
  readonly application_revision: string;
  readonly mode: UpdateMode;
  readonly activate: boolean;
  readonly archive_sha256: string;
  readonly archive_size: NumericJSON<number>;
  readonly archive_path: string;
}
export interface MacUpdatePrefetch {
  readonly application_revision: string;
  readonly archive_sha256: string;
  readonly archive_size: NumericJSON<number>;
  readonly archive_path: string;
}
export interface MacUpdateCheck {
  readonly schema_version: number;
  readonly target: MacUpdateTarget | null;
  readonly prefetch: MacUpdatePrefetch | null;
}
export interface MacUpdateReport {
  readonly request_id: string;
  readonly application_revision: string;
  readonly phase: MacUpdatePhase;
  readonly detail: string | null;
  readonly identity: ExecutorIdentity | null;
}
export interface MacUpdateStatus {
  readonly request_id: string;
  readonly target_revision: string;
  readonly phase: MacUpdatePhase | 'unavailable';
  readonly detail: string | null;
}
export interface MacRuntimeManifest {
  readonly schema_version: 1;
  readonly application_revision: string;
  readonly platform: 'darwin';
  readonly architecture: 'arm64';
}
export interface MacUpdateHealthRead {
  readonly presence: PresenceJudgement['presence'];
  readonly identity: PresenceRecord['identity'];
}
export interface MacUpdateCompositionContext {
  readonly root: string;
  readonly attempts: FileUpdateAttemptStore;
  readonly provider: UpdateProvider | null;
  readonly current_app_sha: string | null;
  readonly child_processes: OwnedChildProcesses;
  ready_release(): PublishedUpdate | null;
  emit_change(): void;
}
export interface MacUpdateServiceContract {
  prepare(release: PublishedUpdate): Promise<void>;
  check(): MacUpdateCheck;
  status(): MacUpdateStatus | null;
  phase(attempt_id: string): MacUpdatePhase | null;
  reported_at(attempt_id: string): number | null;
  report(report: MacUpdateReport, health: MacUpdateHealthRead | null): MacUpdateStatus;
  archive(request_id: string): string;
  prefetch_archive(archive_sha256: string): string;
}

export interface MacUpdateComposition {
  readonly service: MacUpdateServiceContract;
  operations: MacUpdateOperations;
  reads: UpdateMacReads;
}
