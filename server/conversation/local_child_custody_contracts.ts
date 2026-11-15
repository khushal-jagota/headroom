/** Durable source evidence matches the existing custody table and Python boundary. */
import type { StopOptions } from '../lifecycle/contracts.ts';
import type { LocalChildIdentity, LocalHostBootIdentity } from './local_child_contracts.ts';

export interface LocalChildCustodyRow {
  readonly conversation_id: string;
  readonly generation: string;
  readonly state: 'launch_pending' | 'started' | 'retired';
  readonly pid: number | null;
  readonly birth: string | null;
  readonly boot: string | null;
  readonly unit: string | null;
  readonly runtime_epoch: string | null;
  readonly occurrence_id: string | null;
  readonly host: string | null;
  readonly supervision_record: string | null;
}

export type RecordedSourceSettlement = 'undelivered' | 'retired';

export interface LocalChildCustodyInspection {
  host_boot_identity(): Promise<LocalHostBootIdentity | null>;
  retirement_row_proven(row: LocalChildCustodyRow, options?: StopOptions): Promise<boolean>;
  stop_recorded_child(identity: LocalChildIdentity, host: string | null, options?: StopOptions): Promise<boolean>;
  inspect_supervision(path: string, identity?: LocalChildIdentity, options?: StopOptions): Promise<boolean>;
  stop_supervision(path: string, identity?: LocalChildIdentity, options?: StopOptions): Promise<boolean>;
}
