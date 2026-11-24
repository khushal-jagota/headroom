import { failure_message } from './inspection.ts';
/** Standalone child bootstrap commits exact custody before replacing itself with the backend. */
import { fileURLToPath, pathToFileURL } from 'node:url';
import { basename } from 'node:path';
import { ChildProcesses, OneShotChildren } from '../lifecycle/owned_processes.ts';
import { LinuxWorkerProcesses } from '../platform_runtime/linux/worker_processes.ts';
import { DarwinWorkerProcesses } from '../platform_runtime/darwin/worker_processes.ts';
import { WorkerCgroups } from '../platform_runtime/linux/worker_cgroups.ts';
import { execve } from '../environments/release/posix.ts';
import { copy_environment } from '../core/environment.ts';
import { INSPECTION_SECONDS, START_SECONDS } from './config.ts';
import { validate_pending_shape } from './logic/ownership.ts';
import { WorkerSupervisionRuntime } from './runtime.ts';
import * as records from './records.ts';
import { with_worker_file_mask } from './file_mask.ts';

export async function prepare_launch(
  path: string,
  runtime: WorkerSupervisionRuntime,
): Promise<void> {
  await runtime.operation(async () =>
    records.locked(
      path,
      runtime.clock() + START_SECONDS,
      async () => {
        const record = await records.read(path);
        validate_pending_shape(record);
        if (record.launch_revoked || record.state === 'complete')
          throw new Error('Worker launch authorization was revoked before backend execution');
        try {
          const deadline = runtime.clock() + INSPECTION_SECONDS;
          let root = (await runtime.primitives.snapshot(deadline)).get(process.pid);
          if (!root) throw new Error('Worker bootstrap process identity is unavailable');
          Object.assign(record, {
            root,
            processes: [root],
            launch_phase: 'registered',
            state: 'registered',
          });
          await records.write(path, record);
          if (runtime.primitives.requires_containment) {
            try {
              if (!runtime.cgroups)
                throw new Error('Delegated Worker cgroup support is unavailable');
              record.cgroup = await runtime.cgroups.create(basename(path, '.json'));
              // Commit the group identity before any process enters it.
              await records.write(path, record);
              await runtime.cgroups.join(record.cgroup);
              record.cgroup_joined = true;
            } catch (error) {
              record.cgroup_error = `Worker cgroup custody unavailable: ${failure_message(error)}`;
            }
            await records.write(path, record);
          }
          // Only a Worker yields CPU and memory; an older record without the field is a Worker's.
          runtime.primitives.prepare_launch({ lower_priority: record.worker_priority !== false });
          root = (await runtime.primitives.snapshot(deadline)).get(process.pid);
          if (!root) throw new Error('Worker bootstrap process identity is unavailable');
          Object.assign(record, {
            root,
            processes: [root],
            isolated: true,
            launch_phase: 'running',
            state: 'running',
          });
          await records.write(path, record);
        } catch (error) {
          Object.assign(record, {
            state: 'bootstrap_failed',
            reason: `Worker launch failed: ${failure_message(error)}`,
          });
          await records.write(path, record);
          throw error;
        }
      },
      { clock: runtime.clock },
    ),
  );
}
async function main(): Promise<void> {
  const [path, executable, ...args] = process.argv.slice(2);
  if (!path || !executable)
    throw new Error('Worker bootstrap requires a journal and backend command');
  if (process.platform !== 'linux' && process.platform !== 'darwin')
    throw new Error('Worker supervision requires Linux or macOS');
  // This bootstrap is a separate invocation. Its probes use the existing native owner.
  const children = new ChildProcesses();
  const probes = new OneShotChildren(children);
  const runtime = new WorkerSupervisionRuntime({
    primitives:
      process.platform === 'linux'
        ? new LinuxWorkerProcesses()
        : new DarwinWorkerProcesses({ children: probes }),
    ...(process.platform === 'linux' ? { cgroups: new WorkerCgroups() } : {}),
  });
  try {
    await prepare_launch(path, runtime);
  } finally {
    await runtime.close();
    probes.seal();
    await probes.settled();
    children.seal();
    await children.settled();
  }
  const environment = copy_environment(process.env);
  delete environment.TMUX;
  delete environment.STY;
  with_worker_file_mask(() => execve(executable, [executable, ...args], environment));
}
if (
  process.argv[1] &&
  pathToFileURL(fileURLToPath(new URL(import.meta.url))).href ===
    pathToFileURL(process.argv[1]).href
)
  await main();
