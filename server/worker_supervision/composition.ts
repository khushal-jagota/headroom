/** Platform primitives borrow the invocation's existing owner for host probes. */
import type { OwnedOneShotChildren } from '../lifecycle/contracts.ts';
import { DarwinWorkerProcesses } from '../platform_runtime/darwin/worker_processes.ts';
import { LinuxWorkerProcesses } from '../platform_runtime/linux/worker_processes.ts';
import { WorkerCgroups } from '../platform_runtime/linux/worker_cgroups.ts';
import { WorkerSupervisionRuntime } from './runtime.ts';

export function create_worker_supervision_runtime(options: {
  one_shot_children: OwnedOneShotChildren;
  clock?: () => number;
}): WorkerSupervisionRuntime {
  if (process.platform === 'linux')
    return new WorkerSupervisionRuntime({
      primitives: new LinuxWorkerProcesses({ clock: options.clock }),
      cgroups: new WorkerCgroups(),
      clock: options.clock,
    });
  if (process.platform === 'darwin')
    return new WorkerSupervisionRuntime({
      primitives: new DarwinWorkerProcesses({
        children: options.one_shot_children,
        clock: options.clock,
      }),
      clock: options.clock,
    });
  throw new Error('Worker supervision requires Linux or macOS');
}
