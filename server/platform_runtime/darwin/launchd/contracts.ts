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

/** An attempt returns only after transport is ready, before provider admission. */
export interface WorkerJobStart<T> {
  readonly app: string | null;
  readonly command: readonly string[];
  attempt(command: readonly string[]): Promise<T>;
  remove_failed_attempt(): Promise<void>;
  notice(message: string): void;
}
