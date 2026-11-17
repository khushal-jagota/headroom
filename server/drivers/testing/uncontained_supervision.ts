/**
 * Real process primitives with containment off. A stop proves cleanup by process absence, so a
 * supervised launch completes on any host, with or without cgroup delegation.
 */
import { monotonic_seconds } from '../../lifecycle/configuration.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { DarwinWorkerProcesses } from '../../platform_runtime/darwin/worker_processes.ts';
import { LinuxWorkerProcesses } from '../../platform_runtime/linux/worker_processes.ts';
import { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';

class UncontainedLinuxWorkerProcesses extends LinuxWorkerProcesses {
  // The base class types its flag as the literal `true`. Supervision reads it as a boolean.
  override readonly requires_containment = false as true;
}
export function uncontained_worker_supervision(
  one_shot_children: OwnedOneShotChildren,
): WorkerSupervisionRuntime {
  const primitives =
    process.platform === 'darwin'
      ? new DarwinWorkerProcesses({ children: one_shot_children, clock: monotonic_seconds })
      : new UncontainedLinuxWorkerProcesses({ clock: monotonic_seconds });
  return new WorkerSupervisionRuntime({ primitives, clock: monotonic_seconds });
}
