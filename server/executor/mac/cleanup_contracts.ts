/** Private supervision inputs. The journal owner retains storage and wire encoding. */
import type { ChildReference, WorkerCleanupUncertainty } from '../wire.generated.ts';
import type { AgentProcessIdentity, AgentProcessPlatform } from './platform.ts';
import type { RuntimeRecordIO } from './runtime/records.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';

export interface CleanupCommandEvidence {
  readonly command_json: string | Uint8Array;
  readonly result_json: string | Uint8Array | null;
}

/** Each item preserves raw field presence before schema defaults are applied. */
export type ReadCleanupCommands = () => AsyncIterable<CleanupCommandEvidence>;

/** False proves absence or a reused PID. Errors retain uncertainty. */
export type InspectLegacyProcess = (
  identity: AgentProcessIdentity,
  deadline: number,
) => Promise<boolean>;

export interface CleanupReconciliationDependencies {
  readonly runtime: WorkerSupervisionRuntime;
  readonly read_commands: ReadCleanupCommands;
  readonly files: RuntimeRecordIO;
  readonly inspect_legacy: InspectLegacyProcess;
}

export interface LegacyCleanupDependencies extends Pick<
  CleanupReconciliationDependencies,
  'runtime' | 'files' | 'inspect_legacy'
> {
  readonly platform: Pick<AgentProcessPlatform, 'snapshot' | 'signal'>;
  readonly warn: (message: string) => void;
}

export interface CleanupSource {
  readonly source: ChildReference;
  readonly ticket_id: string;
}

export interface ExecutorCleanupSnapshot {
  readonly uncertainties: readonly WorkerCleanupUncertainty[];
  readonly sources: readonly CleanupSource[];
  readonly notice: string | null;
}

export interface CleanupSnapshotOptions {
  readonly ticket_id?: string;
  readonly revoke_pending?: boolean;
  readonly healthy_sources?: readonly ChildReference[];
  readonly preparing_sources?: readonly ChildReference[];
}
