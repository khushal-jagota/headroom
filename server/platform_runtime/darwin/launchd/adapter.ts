/** The recorded launchd service target is the authority for a Mac Worker. */
import { createHash } from 'node:crypto';
import { realpathSync } from 'node:fs';
import type { OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { BackendCommands } from '../../../conversation/snapshot/commands.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import type {
  WorkerJobIdentity,
  WorkerJobPrimitives,
} from '../../../worker_supervision/contracts.ts';

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
  constructor(options: {
    children?: OwnedOneShotChildren;
    commands?: Pick<BackendCommands, 'run'>;
    clock?: () => number;
  }) {
    if (!options.children && !options.commands)
      throw new Error('Mac Worker jobs require an owned command scope');
    this.#commands = options.commands ?? new BackendCommands(options.children!);
    this.#clock = options.clock ?? monotonic_seconds;
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
    if ((await this.status(job, deadline)) === 'absent') return;
    const result = await this.command(['bootout', target], deadline);
    if ((await this.status(job, deadline)) !== 'absent')
      throw new Error(
        `Mac Worker job removal is not proven: ${result.standard_error || result.standard_output}`,
      );
  }
}
