/** The recorded launchd service target is the authority for a Mac Worker. */
import { createHash } from 'node:crypto';
import { realpathSync } from 'node:fs';
import { setTimeout as pause } from 'node:timers/promises';
import type { OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { BackendCommands } from '../../../conversation/snapshot/commands.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import type {
  WorkerJobIdentity,
  WorkerJobPrimitives,
} from '../../../worker_supervision/contracts.ts';
import {
  darwin_marked_worker_processes,
  darwin_signal_marked_worker_process,
} from '../../../executor/mac/platform.ts';
import type { MarkedWorkerProcessKernel } from './contracts.ts';
import {
  WORKER_SWEEP_LIMIT_NOTICE,
  WORKER_SWEEP_SECONDS,
  WORKER_SWEEP_TERM_SECONDS,
} from './configuration.ts';

export function service_target(job: WorkerJobIdentity): string {
  if (
    !/^gui\/\d+$/.test(job.domain) ||
    !/^com\.panels\.worker\.[0-9a-f]{20}\.[0-9a-f]{32}$/.test(job.label)
  )
    throw new Error('Invalid Mac Worker job identity');
  if (job.domain !== `gui/${process.getuid!()}`)
    throw new Error('Mac Worker job belongs to another user');
  return `${job.domain}/${job.label}`;
}
export class DarwinWorkerJobs implements WorkerJobPrimitives {
  readonly #commands: Pick<BackendCommands, 'run'>;
  readonly #clock: () => number;
  readonly #kernel: MarkedWorkerProcessKernel;
  readonly #notice: (message: string) => void;
  constructor(options: {
    children?: OwnedOneShotChildren;
    commands?: Pick<BackendCommands, 'run'>;
    clock?: () => number;
    kernel?: MarkedWorkerProcessKernel;
    notice?: (message: string) => void;
  }) {
    if (!options.children && !options.commands)
      throw new Error('Mac Worker jobs require an owned command scope');
    this.#commands = options.commands ?? new BackendCommands(options.children!);
    this.#clock = options.clock ?? monotonic_seconds;
    this.#kernel = options.kernel ?? {
      scan: (label, deadline) => darwin_marked_worker_processes(label, deadline, this.#clock),
      signal: (process, label, signum, deadline) =>
        darwin_signal_marked_worker_process(process, label, signum, deadline, this.#clock),
    };
    this.#notice = options.notice ?? ((message) => console.warn(message));
  }
  identity(root: string, token: string): WorkerJobIdentity {
    if (!/^[0-9a-f]{32}$/.test(token)) throw new Error('Invalid Mac Worker launch token');
    return {
      domain: `gui/${process.getuid!()}`,
      label: `com.panels.worker.${createHash('sha256').update(realpathSync(root)).digest('hex').slice(0, 20)}.${token}`,
    };
  }
  async command(arguments_: readonly string[], deadline: number) {
    const remaining = deadline - this.#clock();
    if (remaining <= 0) throw new Error('Mac Worker job command deadline expired');
    return this.#commands.run(['/bin/launchctl', ...arguments_], { timeout_seconds: remaining });
  }
  async status(job: WorkerJobIdentity, deadline: number): Promise<'absent' | 'loaded' | 'running'> {
    const target = service_target(job);
    const result = await this.command(['print', target], deadline);
    if (result.exit_code === 0)
      return /^\s*state = running\s*$/m.test(result.standard_output) ? 'running' : 'loaded';
    // A failed command is not absence. Require launchctl's explicit missing-service
    // response and a reachable enclosing login domain.
    const diagnostic = `${result.standard_output}\n${result.standard_error}`;
    if (!diagnostic.includes(`Could not find service "${job.label}" in domain`))
      throw new Error(`Cannot inspect Mac Worker job: ${diagnostic.trim()}`);
    const domain = await this.command(['print', job.domain], deadline);
    if (domain.exit_code !== 0) throw new Error('Mac Worker launchd domain is unavailable');
    return 'absent';
  }
  async bootstrap(job: WorkerJobIdentity, plist: string, deadline: number): Promise<void> {
    service_target(job);
    const result = await this.command(['bootstrap', job.domain, plist], deadline);
    if (result.exit_code !== 0)
      throw new Error(
        `Mac Worker job bootstrap failed: ${result.standard_error || result.standard_output}`,
      );
  }
  async stop(job: WorkerJobIdentity, deadline: number): Promise<void> {
    const target = service_target(job);
    if ((await this.status(job, deadline)) !== 'absent') {
      const result = await this.command(['bootout', target], deadline);
      if ((await this.status(job, deadline)) !== 'absent')
        throw new Error(
          `Mac Worker job removal is not proven: ${result.standard_error || result.standard_output}`,
        );
    }
    await this.sweep(job, deadline);
  }
  sweep(job: WorkerJobIdentity, deadline: number): Promise<void> {
    service_target(job);
    return this.#sweep(job.label, Math.min(deadline, this.#clock() + WORKER_SWEEP_SECONDS));
  }
  async #sweep(label: string, deadline: number): Promise<void> {
    // This notice states a detection limit, not an observation of a survivor.
    // A failed or incomplete sweep never changes the proven job absence.
    const notice = (message: string) => {
      try {
        this.#notice(message);
      } catch {
        /* Logging cannot revoke job absence. */
      }
    };
    notice(WORKER_SWEEP_LIMIT_NOTICE);
    try {
      for (const signum of [15, 9]) {
        if (this.#clock() >= deadline) throw new Error('deadline expired');
        const snapshot = this.#kernel.scan(label, deadline);
        if (snapshot.unreadable)
          notice(`Mac Worker process sweep skipped ${snapshot.unreadable} unreadable processes.`);
        for (const process of snapshot.processes) {
          try {
            this.#kernel.signal(process, label, signum, deadline);
          } catch {
            notice('Mac Worker process sweep could not signal a marked process.');
          }
        }
        if (signum === 15 && snapshot.processes.length)
          await pause(
            Math.max(0, Math.min(WORKER_SWEEP_TERM_SECONDS, deadline - this.#clock())) * 1000,
          );
      }
    } catch {
      notice('Mac Worker process sweep did not finish. Job absence still permits restart.');
    }
  }
}
