/** Real bootstrap argv and private journals. Only the exact scripted CLI executes. */
import childProcess from 'node:child_process';
import type { SpawnOptions } from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import {
  accessSync,
  appendFileSync,
  constants,
  mkdirSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Part2Spec } from './part2_parity.ts';
import type { WorkerScratch as Scratch } from '../../worker_scratch.ts';
import * as records from '../../../worker_supervision/records.ts';
import { cleanup_result } from '../../../worker_supervision/inspection.ts';

export function selected_node_executable(): string {
  for (const directory of (process.env.PATH ?? '').split(':')) {
    const candidate = resolve(directory || '.', 'node');
    try {
      accessSync(candidate, constants.X_OK);
    } catch {
      continue;
    }
    if (realpathSync(candidate) !== realpathSync(process.execPath)) {
      throw new Error('The scripted SDK node prefix must resolve to this evaluator runtime');
    }
    return candidate;
  }
  throw new Error('The scripted SDK requires node on its isolated PATH');
}

export async function install_owned_launch_boundary(spec: Part2Spec): Promise<() => void> {
  const nodePath = selected_node_executable();
  const original = {
    spawn: childProcess.spawn,
    spawnSync: childProcess.spawnSync,
    exec: childProcess.exec,
    execSync: childProcess.execSync,
    execFile: childProcess.execFile,
    execFileSync: childProcess.execFileSync,
    fork: childProcess.fork,
  };
  const metadata = `${spec.trace}.wrapped.json`;
  const directory = `${spec.workspace}/contained-unit`;
  const path = `${directory}/${'3'.repeat(32)}.json`;
  const bootstrap = fileURLToPath(
    new URL('../../../worker_supervision/launch.ts', import.meta.url),
  );
  const denied = (): never => {
    throw new Error('Unexpected process API in the owned CLI proof');
  };
  const spawn = (command: string, arguments_: readonly string[] = [], options?: SpawnOptions) => {
    const actual = [command, ...arguments_];
    let delegated = actual;
    const wrapped = command === process.execPath && arguments_[0] === bootstrap;
    if (wrapped) {
      if (arguments_[1] !== path) denied();
      delegated = [...arguments_.slice(2)];
    }
    const script =
      delegated[0] === spec.executable ||
      ((delegated[0] === 'node' ||
        delegated[0] === nodePath ||
        delegated[0] === process.execPath) &&
        delegated[1] === spec.executable);
    if (!script) denied();
    if (delegated[0] !== spec.executable && options?.env?.PATH !== process.env.PATH) denied();
    const probe = delegated.includes('--version') || delegated.includes('-v');
    if (!probe)
      writeFileSync(
        metadata,
        JSON.stringify({
          launch_argv: actual,
          delegated_argv: delegated,
          wrapped_argv: wrapped ? actual : null,
        }),
      );
    // Bootstrap execution and cgroup authority stay outside this adapter proof.
    return original.spawn(delegated[0], delegated.slice(1), options ?? {});
  };
  Object.assign(childProcess, {
    spawn,
    spawnSync: denied,
    exec: denied,
    execSync: denied,
    execFile: denied,
    execFileSync: denied,
    fork: denied,
  });
  syncBuiltinESMExports();
  // Install the subprocess fence before importing any containment/control implementation.
  const { WorkerSupervisor } = await import('../../../worker_supervision/service.ts');
  const { WorkerScratch } = await import('../../worker_scratch.ts');
  const originalCreate = WorkerSupervisor.create;
  const originalScratch = WorkerScratch.create;
  WorkerSupervisor.create = async (environment, working_directory, options) => {
    if (environment.PLAN_TICKET_ID !== 'unit18-synthetic-worker') denied();
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const runtime = options.runtime;
    const context = await runtime.context(runtime.clock() + 5);
    // Keep the real instance: argv uses the supervisor's private runtime to
    // choose its launch transport. Only custody execution stays scripted here.
    const supervisor = await originalCreate.call(
      WorkerSupervisor,
      { ...environment, PLAN_WORKER_PROCESS_ROOT: directory },
      working_directory,
      options,
    );
    if (supervisor == null) return denied();
    // Keep the shared fixture's exact journal identity across both adapters.
    renameSync(supervisor.path, path);
    Object.defineProperty(supervisor, 'path', { value: path });
    const record_path = path;
    const started = async (pid: number) => {
      const root = (await context.primitives.snapshot(runtime.clock() + 5)).get(pid);
      if (root === undefined) denied();
      const record = await records.read(record_path);
      Object.assign(record, { root, processes: [root], state: 'running' });
      await records.write(record_path, record);
    };
    let stopped = false;
    const stop = async (native_stop?: () => Promise<void>) => {
      if (stopped) return cleanup_result(true, 'Owned scripted child ended');
      await native_stop?.();
      stopped = true;
      const record = await records.read(record_path);
      record.state = 'complete';
      record.reason = 'Owned scripted child ended';
      await records.write(record_path, record);
      appendFileSync(spec.trace, `${JSON.stringify({ kind: 'contained-stop', record_path })}\n`);
      return cleanup_result(true, 'Owned scripted child ended');
    };
    Object.assign(supervisor, {
      started,
      spawned: started,
      stop,
      cancel_unspawned: () => stop(),
      settled: async () => {},
      identity: async () => {
        const record = await records.read(record_path);
        return record.root == null
          ? null
          : {
              pid: record.root.pid,
              birth: record.root.birth,
              boot: record.boot!,
              unit: null,
              supervision_record: record_path,
            };
      },
    });
    Object.defineProperty(supervisor, 'shutdown_settled', { value: Promise.resolve() });
    return supervisor;
  };
  WorkerScratch.create = async (environment) => {
    const temporary = `${directory}/t`;
    mkdirSync(temporary, { mode: 0o700 });
    let closed = false;
    return {
      directory: temporary,
      environment: { ...environment, TMPDIR: temporary, TMP: temporary, TEMP: temporary },
      close: async () => {
        if (closed) return;
        closed = true;
        rmSync(temporary, { recursive: true });
      },
    } as unknown as Scratch;
  };
  return () => {
    WorkerSupervisor.create = originalCreate;
    WorkerScratch.create = originalScratch;
    Object.assign(childProcess, original);
    syncBuiltinESMExports();
  };
}
