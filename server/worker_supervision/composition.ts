/** Platform primitives borrow the invocation's existing owner for host probes. */
import type { OwnedOneShotChildren } from '../lifecycle/contracts.ts';
import { DarwinWorkerProcesses } from '../platform_runtime/darwin/worker_processes.ts';
import { LinuxWorkerProcesses } from '../platform_runtime/linux/worker_processes.ts';
import { WorkerCgroups } from '../platform_runtime/linux/worker_cgroups.ts';
import { WorkerSupervisionRuntime } from './runtime.ts';
import { DarwinWorkerJobs } from '../platform_runtime/darwin/launchd/adapter.ts';

export function create_worker_supervision_runtime(options: {
  one_shot_children: OwnedOneShotChildren;
  clock?: () => number;
  /** Record paths a durable owner still names. Without it, no finished record is pruned. */
  record_paths_in_use?: () => ReadonlySet<string>;
}): WorkerSupervisionRuntime {
  const { record_paths_in_use } = options;
  if (process.platform === 'linux')
    return new WorkerSupervisionRuntime({
      primitives: new LinuxWorkerProcesses({ clock: options.clock }),
      cgroups: new WorkerCgroups(),
      clock: options.clock,
      record_paths_in_use,
    });
  if (process.platform === 'darwin')
    return new WorkerSupervisionRuntime({
      jobs: new DarwinWorkerJobs({ children: options.one_shot_children, clock: options.clock }),
      primitives: new DarwinWorkerProcesses({
        children: options.one_shot_children,
        clock: options.clock,
      }),
      clock: options.clock,
      record_paths_in_use,
    });
  throw new Error('Worker supervision requires Linux or macOS');
}
