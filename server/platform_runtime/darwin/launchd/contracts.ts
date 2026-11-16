/** Transient observations for one best-effort sweep, never durable custody. */
export interface MarkedWorkerProcess {
  readonly pid: number;
  readonly birth: string;
}
export interface MarkedWorkerProcessScan {
  readonly processes: readonly MarkedWorkerProcess[];
  readonly unreadable: number;
}
export interface MarkedWorkerProcessKernel {
  scan(label: string, deadline: number): MarkedWorkerProcessScan;
  signal(process: MarkedWorkerProcess, label: string, signum: number, deadline: number): boolean;
}
