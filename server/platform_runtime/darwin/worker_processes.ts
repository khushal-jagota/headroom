/** macOS lacks pidfds. Only an immediate exact libproc birth check permits a signal. */
import { getPriority, setPriority } from 'node:os';
import type { LocalHostBootIdentity } from '../../conversation/local_child_contracts.ts';
import type { WorkerProcess, WorkerProcessPrimitives } from '../../worker_supervision/contracts.ts';
import { NICE } from '../../worker_supervision/config.ts';
import { monotonic_seconds } from '../../lifecycle/configuration.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { BackendCommands } from '../../conversation/snapshot/commands.ts';
import { observe_promise } from '../../core/promise_observer.ts';
import {
  darwin_worker_process_info,
  darwin_worker_process_snapshot,
  darwin_worker_process_exists,
  darwin_worker_process_birth,
  darwin_prepare_worker_session,
} from '../../executor/mac/platform.ts';
import type { DarwinWorkerProcessInfo } from '../../executor/mac/platform.ts';

export interface DarwinWorkerKernel {
  info(pid: number): DarwinWorkerProcessInfo | null;
  /** A missing PID returns null. Unreadable sysctl evidence throws. */
  birth(pid: number): string | null;
  snapshot(deadline: number): ReadonlyMap<number, WorkerProcess>;
  exists(pid: number): boolean;
  signal(pid: number, signum: number): void;
  prepare_session(): void;
}
let host_identity: LocalHostBootIdentity | undefined;
let host_probe: Promise<LocalHostBootIdentity> | undefined;
const uuid = (value: string): string => {
  if (!/^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$/.test(value))
    throw new Error('Mac host identity is invalid');
  return value.toLowerCase();
};
export class DarwinWorkerProcesses implements WorkerProcessPrimitives {
  readonly owns_isolated_session_members = false;
  readonly requires_containment = false;
  readonly #commands: Pick<BackendCommands, 'run'>;
  readonly #clock: () => number;
  readonly #native: DarwinWorkerKernel;
  constructor(options: {
    children?: OwnedOneShotChildren;
    commands?: Pick<BackendCommands, 'run'>;
    clock?: () => number;
    native?: DarwinWorkerKernel;
  }) {
    if (!options.commands && !options.children)
      throw new Error('Mac host probes require an existing child owner');
    this.#commands = options.commands ?? new BackendCommands(options.children!);
    this.#clock = options.clock ?? monotonic_seconds;
    this.#native = options.native ?? {
      info: darwin_worker_process_info,
      snapshot: (deadline) => darwin_worker_process_snapshot(deadline, this.#clock),
      exists: darwin_worker_process_exists,
      birth: darwin_worker_process_birth,
      signal: (pid, signum) => {
        process.kill(pid, signum);
      },
      prepare_session: darwin_prepare_worker_session,
    };
  }
  async host_boot(deadline: number): Promise<LocalHostBootIdentity> {
    if (host_identity) return host_identity;
    if (this.#clock() >= deadline) throw new Error('Worker inspection deadline expired');
    const owns_probe = host_probe === undefined;
    if (!host_probe) {
      const pending = this.#read_host_boot(deadline);
      host_probe = pending;
      void pending.then(
        (identity) => {
          host_identity = identity;
          if (host_probe === pending) host_probe = undefined;
        },
        () => {
          if (host_probe === pending) host_probe = undefined;
        },
      );
    }
    let observed: Awaited<ReturnType<typeof observe_promise<LocalHostBootIdentity>>>;
    try {
      observed = await observe_promise(host_probe, Math.max(0, deadline - this.#clock()) * 1000);
    } catch (error) {
      // A waiter gets its own probe after the prior owner releases failed evidence.
      // Its deadline and command owner remain its own. Owner failures never retry here.
      if (!owns_probe) return this.host_boot(deadline);
      throw error;
    }
    if (!observed.completed) throw new Error('Worker inspection deadline expired');
    return observed.value;
  }
  async #read_host_boot(deadline: number): Promise<LocalHostBootIdentity> {
    const output = async (command: readonly string[]): Promise<string> => {
      const remaining = deadline - this.#clock();
      if (remaining <= 0) throw new Error('Worker inspection deadline expired');
      try {
        const result = await this.#commands.run(command, { timeout_seconds: remaining });
        if (result.exit_code !== 0) throw new Error(result.standard_error);
        return result.standard_output.trim();
      } catch (cause) {
        throw new Error('Mac host identity command failed', { cause });
      }
    };
    const boot = uuid(await output(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']));
    const raw = await output(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice']);
    const machine = /"IOPlatformUUID"\s*=\s*"([0-9A-Fa-f-]{36})"/.exec(raw);
    if (!machine) throw new Error('Mac machine identity is unavailable');
    return { host: `darwin:${uuid(machine[1])}`, boot: `darwin:${boot}` };
  }
  async snapshot(deadline: number): Promise<ReadonlyMap<number, WorkerProcess>> {
    if (this.#clock() >= deadline) throw new Error('Worker inspection deadline expired');
    return this.#native.snapshot(deadline);
  }
  async is_absent(recorded: WorkerProcess, deadline: number): Promise<boolean> {
    if (this.#clock() >= deadline) throw new Error('Worker inspection deadline expired');
    try {
      if (!this.#native.exists(recorded.pid)) return true;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EPERM') throw error;
      // libproc can refuse a cross-user PID too. KERN_PROC_PID exposes its
      // actual start time without granting signal authority over that process.
      return this.#birth_absent(recorded);
    }
    try {
      const current = this.#native.info(recorded.pid);
      return current === null || current.birth !== recorded.birth || current.zombie;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EPERM') return this.#birth_absent(recorded);
      return false;
    }
  }
  #birth_absent(recorded: WorkerProcess): boolean {
    try {
      const birth = this.#native.birth(recorded.pid);
      return birth === null || birth !== recorded.birth;
    } catch {
      return false;
    }
  }
  async signal(recorded: WorkerProcess, signum: number, deadline: number): Promise<boolean> {
    if (this.#clock() >= deadline) return false;
    try {
      const current = this.#native.info(recorded.pid);
      if (current === null || current.birth !== recorded.birth) return false;
      // No asynchronous boundary separates this check from the signal.
      this.#native.signal(recorded.pid, signum);
      return true;
    } catch (error) {
      return (error as NodeJS.ErrnoException).code === 'ESRCH';
    }
  }
  prepare_launch(options: { lower_priority: boolean }): void {
    this.#native.prepare_session();
    if (options.lower_priority) setPriority(0, Math.max(NICE, getPriority(0)));
  }
}
