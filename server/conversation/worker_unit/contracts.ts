import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';

export class WorkerUnitError extends Error {}

export interface WorkerUnitValues {
  child_processes: OwnedChildProcesses;
  name: string;
  directory: string;
  environment: Record<string, string>;
  working_directory: string;
  slice_name: string;
}
