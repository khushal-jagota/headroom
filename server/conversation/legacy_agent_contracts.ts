import type { LocalHostBootIdentity } from './local_child_contracts.ts';
import type { ProcessObservation, WorkerProcessRecord } from '../worker_supervision/contracts.ts';

export interface LegacyProcessDetails {
  readonly pid: number;
  readonly birth: string;
  readonly command: string;
  readonly owner: string;
  readonly age_seconds: number;
}
export interface LegacyAgentReport {
  readonly would_stop: readonly (LegacyProcessDetails & { record: string })[];
  readonly unproven: readonly { record: string; reason: string }[];
}
export interface LegacyAgentObservation {
  readonly host: LocalHostBootIdentity;
  readonly processes: ProcessObservation;
  details(pid: number): Promise<LegacyProcessDetails>;
  members?(record: WorkerProcessRecord): Promise<readonly number[]>;
  journal(path: string): Promise<WorkerProcessRecord>;
}
