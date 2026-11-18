/**
 * Real process primitives with containment off. A stop proves cleanup by process absence, so a
 * supervised launch completes on any host, with or without cgroup delegation.
 *
 * The launch bootstrap is its own process and always contains itself on Linux, so it still
 * creates a group where delegation allows. The real cgroup primitives are here only so that
 * cleanup removes that group, and a stop's kill reaches it, as production does.
 */
import { monotonic_seconds } from '../../lifecycle/configuration.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { DarwinWorkerProcesses } from '../../platform_runtime/darwin/worker_processes.ts';
import { LinuxWorkerProcesses } from '../../platform_runtime/linux/worker_processes.ts';
import { WorkerCgroups } from '../../platform_runtime/linux/worker_cgroups.ts';
import { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';

class UncontainedLinuxWorkerProcesses extends LinuxWorkerProcesses {
  // The base class types its flag as the literal `true`. Supervision reads it as a boolean.
  override readonly requires_containment = false as true;
}
export function uncontained_worker_supervision(
  one_shot_children: OwnedOneShotChildren,
): WorkerSupervisionRuntime {
  if (process.platform === 'darwin')
    return new WorkerSupervisionRuntime({
      primitives: new DarwinWorkerProcesses({
        children: one_shot_children,
        clock: monotonic_seconds,
      }),
      clock: monotonic_seconds,
    });
  return new WorkerSupervisionRuntime({
    primitives: new UncontainedLinuxWorkerProcesses({ clock: monotonic_seconds }),
    cgroups: new WorkerCgroups(),
    clock: monotonic_seconds,
  });
}
