/** Existing VPS/Mac update wire channel. The Python Mac archive format stays unchanged. */
import type { OwnedChildProcesses } from '../../../lifecycle/contracts.ts';
import type { MacUpdateOperations } from '../contracts.ts';
import type { UpdateMacReads } from '../routes.ts';
import type { PresenceJudgement, PresenceRecord } from '../../../executor/contracts.ts';
import type { FileUpdateAttemptStore } from '../attempts.ts';
import type { PublishedUpdate, UpdateProvider } from '../contracts.ts';

import type {
  MacUpdateCheck,
  MacUpdatePhase,
  MacUpdateReport,
  MacUpdateStatus,
} from '../../../executor/wire.generated.ts';
export type {
  MacRuntimeManifest,
  MacUpdateCheck,
  MacUpdatePhase,
  MacUpdatePrefetch,
  MacUpdateReport,
  MacUpdateStatus,
  MacUpdateTarget,
} from '../../../executor/wire.generated.ts';

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
  readonly signal: AbortSignal;
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
