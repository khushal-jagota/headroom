/** Startup boundary: real owner code, synthetic custody/scratch, and no native child. */
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { fileURLToPath } from 'node:url';
import { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import type { WorkerSupervisor as Supervisor } from '../../../worker_supervision/service.ts';
import type { WorkerScratch as Scratch } from '../../worker_scratch.ts';

interface StartupSpec {
  ambient: Record<string, string>;
  cases: { name: string; overrides: [string, string][]; identity: [string, string][] }[];
  launches: {
    name: string;
    argv: string[];
    working_directory: string;
    worker_ticket_id: string | null;
    directory: string;
    failure: 'spawn' | 'scratch' | 'reporter' | 'version';
  }[];
}
export async function run_part1_parity(spec: StartupSpec): Promise<unknown> {
  const original_environment = { ...process.env };
  const original_process = {
    spawn: childProcess.spawn,
    spawnSync: childProcess.spawnSync,
    exec: childProcess.exec,
    execSync: childProcess.execSync,
    execFile: childProcess.execFile,
    execFileSync: childProcess.execFileSync,
    fork: childProcess.fork,
  };
  const forbidden = (): never => {
    throw new Error('Unexpected native launch');
  };
  Object.assign(
    childProcess,
    Object.fromEntries(Object.keys(original_process).map((name) => [name, forbidden])),
  );
  syncBuiltinESMExports();
  for (const name of Object.keys(process.env)) delete process.env[name];
  Object.assign(process.env, spec.ambient);
  const runtime = new WorkerSupervisionRuntime({
    primitives: {
      owns_isolated_session_members: false,
      requires_containment: false,
      host_boot: forbidden,
      snapshot: forbidden,
      is_absent: forbidden,
      signal: forbidden,
      prepare_launch: forbidden,
    },
  });
  try {
    const { child_environment } = await import('../../backends/codex_app_server/environment.ts');
    const { CodexAppServerClient } = await import('../../backends/codex_app_server/client.ts');
    const { WorkerSupervisor } = await import('../../../worker_supervision/service.ts');
    const { WorkerScratch } = await import('../../worker_scratch.ts');
    const cases = spec.cases.map((item) => {
      try {
        return { name: item.name, environment: child_environment(item), error: null };
      } catch (error) {
        if (!(error instanceof Error)) throw error;
        return { name: item.name, environment: null, error: error.message };
      }
    });
    const launches = [];
    for (const item of spec.launches) {
      const calls: Record<string, unknown>[] = [];
      const original_create = WorkerSupervisor.create,
        original_scratch = WorkerScratch.create;
      WorkerSupervisor.create = async (environment, working_directory) => {
        calls.push({
          operation: 'supervisor-create',
          environment: { ...environment },
          working_directory,
        });
        // Keep the real private runtime slot used by argv(), while fencing all
        // lifecycle operations below before any custody or native launch occurs.
        const supervisor = Object.assign(
          Reflect.construct(WorkerSupervisor, [
            environment,
            working_directory,
            item.directory,
            `${item.directory}/record.json`,
            runtime,
          ]) as Supervisor,
          {
            path: `${item.directory}/record.json`,
            working_directory,
            environment: { ...environment, PLAN_WORKER_PROCESS_ROOT: item.directory },
            cancel_unspawned: async () => {
              calls.push({ operation: 'cancel-unspawned' });
            },
            stop: async () => {
              calls.push({ operation: 'supervisor-stop' });
              return { complete: true, reason: 'no child', scope: 'synthetic' };
            },
            settled: async () => {},
          },
        );
        Object.defineProperty(supervisor, 'shutdown_settled', { value: Promise.resolve() });
        return supervisor;
      };
      WorkerScratch.create = async (environment) => {
        calls.push({ operation: 'scratch-create', environment: { ...environment } });
        if (item.failure === 'scratch') throw new Error('synthetic scratch failure');
        const temporary = `${item.directory}/scratch`;
        return {
          directory: temporary,
          environment: { ...environment, TMPDIR: temporary, TMP: temporary, TEMP: temporary },
          close: async () => {
            calls.push({ operation: 'scratch-close' });
          },
        } as unknown as Scratch;
      };
      childProcess.spawn = ((
        command: string,
        args: readonly string[],
        options: { env: Record<string, string>; cwd: string },
      ): never => {
        calls.push({
          operation: 'spawn',
          argv: [command, ...args],
          environment: { ...options.env },
          working_directory: options.cwd,
        });
        if (item.worker_ticket_id) {
          const expected = [
            process.execPath,
            fileURLToPath(new URL('../../../worker_supervision/launch.ts', import.meta.url)),
            `${item.directory}/record.json`,
            ...item.argv,
          ];
          if (JSON.stringify([command, ...args]) !== JSON.stringify(expected))
            throw new Error('Wrong raw Node bootstrap argv');
        }
        throw new Error('synthetic stop before spawn');
      }) as unknown as typeof childProcess.spawn;
      syncBuiltinESMExports();
      const client = new CodexAppServerClient({
        child_processes: new ChildProcesses(),
        worker_supervision: runtime,
        description: 'startup-parity',
        handler: {
          async on_notification() {},
          async on_server_request() {},
          async on_child_ended() {},
        },
      });
      Object.assign(client, {
        confirm_cli_compatible: async (
          executable: string,
          environment: Record<string, string>,
          working_directory: string,
        ) => {
          calls.push({
            operation: 'version',
            executable,
            environment: { ...environment },
            working_directory,
          });
          if (item.failure === 'version') throw new Error('synthetic version failure');
        },
      });
      let failure: string | null = null;
      try {
        await client.start({
          ...item,
          environment: child_environment(),
          supervision_reporter: async (path) => {
            calls.push({ operation: 'supervision-report', path });
            if (item.failure === 'reporter') throw new Error('synthetic reporter failure');
          },
        });
      } catch (error) {
        if (!(error instanceof Error)) throw error;
        failure = error.message;
      } finally {
        WorkerSupervisor.create = original_create;
        WorkerScratch.create = original_scratch;
        childProcess.spawn = forbidden as typeof childProcess.spawn;
        syncBuiltinESMExports();
      }
      launches.push({ name: item.name, calls, failure });
    }
    return { cases, launches, provider_processes_started: 0, service_manager_calls: 0 };
  } finally {
    await runtime.close();
    Object.assign(childProcess, original_process);
    syncBuiltinESMExports();
    for (const name of Object.keys(process.env)) delete process.env[name];
    Object.assign(process.env, original_environment);
  }
}
