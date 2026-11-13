import type { BackendSnapshotSource } from '../../../conversation/snapshot/contracts.ts';
import type { pack } from '../storage/journal_worker_contracts.ts';
export interface MacCatalogDependencies {
  readonly snapshots: BackendSnapshotSource;
  /** Seal and join actual provider probes; retry a failed native close on a later call. */
  stop_sources(): Promise<void>;
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
