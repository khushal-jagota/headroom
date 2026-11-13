import type { BackendSnapshotSource } from '../../../conversation/snapshot/contracts.ts';
import type { pack } from '../storage/journal_worker_contracts.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
export interface MacCatalogDependencies {
  readonly snapshots: BackendSnapshotSource;
  /** Cancel current provider probes without sealing later reads. */
  cancel_sources?(reason: unknown): Promise<void>;
  /** Seal and join actual provider probes; retry a failed native close on a later call. */
  stop_sources(options: StopOptions): Promise<void>;
  on_failure(error: Error): void;
}
export type CatalogAction =
  | { operation: 'encode'; command_id: string; snapshot: ReturnType<typeof pack> }
  | { operation: 'open'; result: number }
  | { operation: 'close_result'; result: number }
  | { operation: 'read'; reader: number }
  | { operation: 'close_reader'; reader: number };
export type CatalogWorkerRequest = { id: number; action: CatalogAction } | { stop: true };
export type CatalogWorkerReply = { id: number; value?: unknown; error?: string };
export interface CatalogResultDescriptor {
  result: number;
  byte_count: number;
}
export interface CatalogReaderDescriptor {
  reader: number;
  byte_count: number;
}
