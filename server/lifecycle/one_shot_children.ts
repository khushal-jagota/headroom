import { ChildProcesses } from './owned_processes.ts';
import type {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  SpawnOptions,
  SpawnOptionsWithoutStdio,
} from 'node:child_process';
import type { OwnedChildProcesses, OwnedOneShotChildren } from './contracts.ts';

/** Only call with a child returned by the invocation's dedicated-group spawn. */
export function stop_one_shot_child(child: ChildProcess): void {
  if (child.pid === undefined || child.exitCode !== null || child.signalCode !== null) return;
  try {
    if (process.platform === 'win32') child.kill('SIGKILL');
    else process.kill(-child.pid, 'SIGKILL');
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ESRCH')) throw error;
  }
}

/** A command outcome is not a process-close boundary. This owner retains the latter. */
export class OneShotChildren implements OwnedOneShotChildren {
  readonly #controller = new AbortController();
  readonly #pending = new Set<Promise<void>>();
  readonly #stops = new Map<ChildProcess, () => void>();
  readonly #failures: unknown[] = [];
  #sealed = false;
  readonly child_processes: OwnedChildProcesses;
  constructor(child_processes: OwnedChildProcesses = new ChildProcesses()) {
    this.child_processes = child_processes;
  }
  get signal(): AbortSignal {
    return this.#controller.signal;
  }
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptionsWithoutStdio,
    stop?: (child: ChildProcess) => void,
  ): ChildProcessWithoutNullStreams;
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptions,
    stop?: (child: ChildProcess) => void,
  ): ChildProcess;
  spawn(
    command: string,
    args: readonly string[],
    options: SpawnOptions,
    stop: (child: ChildProcess) => void = stop_one_shot_child,
  ): ChildProcess {
    if (this.#sealed) throw new Error('One-shot process admission is closed');
    let release!: () => void;
    const actual = new Promise<void>((resolve) => {
      release = resolve;
    });
    this.#pending.add(actual);
    let child: ChildProcess;
    try {
      child = this.child_processes.spawn(command, args, options);
    } catch (error) {
      this.#pending.delete(actual);
      release();
      throw error;
    }
    const finish = () => {
      this.#stops.delete(child);
      this.#pending.delete(actual);
      release();
    };
    child.once('close', finish);
    // A failed spawn also emits close. Do not convert error into proven closure.
    child.on('error', () => {});
    this.#stops.set(child, () => stop(child));
    // A synchronous injected spawn can reenter shutdown before it returns its child.
    if (this.#sealed) this.#stop(child);
    return child;
  }
  #stop(child: ChildProcess): void {
    try {
      this.#stops.get(child)?.();
    } catch (error) {
      this.#failures.push(error);
    }
  }
  seal(): void {
    if (this.#sealed) return;
    this.#sealed = true;
    // Stop the owned handles even if an abort listener throws or starts more work.
    try {
      this.#controller.abort(new Error('One-shot processes stopped at shutdown'));
    } finally {
      for (const child of this.#stops.keys()) this.#stop(child);
    }
  }
  async settled(): Promise<void> {
    while (this.#pending.size) await Promise.all([...this.#pending]);
    if (this.#failures.length)
      throw new AggregateError(this.#failures, 'One-shot children did not close cleanly');
  }
}
