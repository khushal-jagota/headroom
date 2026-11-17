/** Linux process evidence and signals bound to one process incarnation. */
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { getPriority, setPriority } from 'node:os';
import { join } from 'node:path';
import type { LocalHostBootIdentity } from '../../conversation/local_child_contracts.ts';
import type { WorkerProcess, WorkerProcessPrimitives } from '../../worker_supervision/contracts.ts';
import { NICE } from '../../worker_supervision/config.ts';
import { monotonic_seconds } from '../../lifecycle/configuration.ts';
import { linux_worker_process_support } from '../../lifecycle/linux_process.ts';
import type { LinuxWorkerProcessSupport } from '../../lifecycle/linux_process.ts';

export function parse_linux_worker_process(pid: number, stat: string): WorkerProcess {
  const closing = stat.lastIndexOf(')');
  const fields = stat
    .slice(closing + 1)
    .trim()
    .split(/\s+/);
  if (closing < 0 || fields.length < 20 || !/^\d+$/.test(fields[19]))
    throw new Error('Linux process identity is unreadable');
  const [parent, group, session] = fields.slice(1, 4).map(Number);
  if (![parent, group, session].every(Number.isSafeInteger))
    throw new Error('Linux process identity is unreadable');
  return {
    pid,
    birth: fields[19],
    parent,
    group,
    session,
    zombie: fields[0] === 'Z' || fields[0] === 'X',
  };
}
export class LinuxWorkerProcesses implements WorkerProcessPrimitives {
  readonly owns_isolated_session_members = true;
  readonly requires_containment = true;
  readonly #proc_root: string;
  readonly #machine_id: string;
  readonly #clock: () => number;
  readonly #native: () => LinuxWorkerProcessSupport;
  constructor(
    options: {
      proc_root?: string;
      machine_id?: string;
      clock?: () => number;
      native?: () => LinuxWorkerProcessSupport;
    } = {},
  ) {
    this.#proc_root = options.proc_root ?? '/proc';
    this.#machine_id = options.machine_id ?? '/etc/machine-id';
    this.#clock = options.clock ?? monotonic_seconds;
    this.#native = options.native ?? linux_worker_process_support;
  }
  async host_boot(deadline: number): Promise<LocalHostBootIdentity> {
    if (this.#clock() >= deadline) throw new Error('Worker inspection deadline expired');
    const machine = readFileSync(this.#machine_id, 'utf8');
    const boot = readFileSync(join(this.#proc_root, 'sys/kernel/random/boot_id'), 'utf8');
    return { host: `linux:${machine.trim()}`, boot: boot.trim() };
  }
  // Reads are synchronous on purpose: an awaited read returns only when the busy main thread
  // returns, so the deadline would measure main-thread load instead of /proc work.
  #read(pid: number): WorkerProcess | null {
    try {
      return parse_linux_worker_process(
        pid,
        readFileSync(join(this.#proc_root, String(pid), 'stat'), 'utf8'),
      );
    } catch (error) {
      if (['ENOENT', 'ESRCH'].includes((error as NodeJS.ErrnoException).code ?? '')) return null;
      throw error;
    }
  }
  async snapshot(deadline: number): Promise<ReadonlyMap<number, WorkerProcess>> {
    const found = new Map<number, WorkerProcess>();
    for (const entry of readdirSync(this.#proc_root)) {
      if (this.#clock() >= deadline) throw new Error('Worker inspection deadline expired');
      if (!/^\d+$/.test(entry)) continue;
      const current = this.#read(Number(entry));
      if (current) found.set(current.pid, current);
    }
    return found;
  }
  async is_absent(recorded: WorkerProcess, deadline: number): Promise<boolean> {
    if (this.#clock() >= deadline) throw new Error('Worker inspection deadline expired');
    const current = this.#read(recorded.pid);
    return current === null || current.birth !== recorded.birth || current.zombie;
  }
  async signal(recorded: WorkerProcess, signum: number, deadline: number): Promise<boolean> {
    if (this.#clock() >= deadline) return false;
    return this.#native().signal_exact(recorded.pid, recorded.birth, signum, () => {
      try {
        return parse_linux_worker_process(
          recorded.pid,
          readFileSync(join(this.#proc_root, String(recorded.pid), 'stat'), 'utf8'),
        );
      } catch (error) {
        if (['ENOENT', 'ESRCH'].includes((error as NodeJS.ErrnoException).code ?? '')) return null;
        throw error;
      }
    });
  }
  prepare_launch(): void {
    this.#native().prepare_session();
    setPriority(0, Math.max(NICE, getPriority(0)));
    const path = join(this.#proc_root, 'self/oom_score_adj');
    const score = Number(readFileSync(path, 'utf8').trim());
    if (!Number.isSafeInteger(score)) throw new Error('Worker OOM priority is unreadable');
    writeFileSync(path, String(Math.max(0, score)));
  }
}
