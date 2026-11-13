/** Export-only direct proof. All process and service-manager calls stop at a fence. */
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';

interface StartupSpec {
  operation: 'startup';
  ambient: Record<string, string>;
  cases: {
    name: string;
    overrides: [string, string][];
    identity: [string, string][];
  }[];
  launches: {
    name: string;
    argv: string[];
    working_directory: string;
    worker_ticket_id: string | null;
    unit_directory: string;
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
    throw new Error('The startup comparison must not start a process');
  };
  Object.assign(
    childProcess,
    Object.fromEntries(Object.keys(original_process).map((name) => [name, forbidden])),
  );
  syncBuiltinESMExports();
  for (const name of Object.keys(process.env)) delete process.env[name];
  Object.assign(process.env, spec.ambient);
  try {
    const { child_environment } = await import('../../backends/codex_app_server/environment.ts');
    const { CodexAppServerClient } = await import('../../backends/codex_app_server/client.ts');
    const { WorkerUnit } = await import('../../worker_unit/index.ts');
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
      const calls: unknown[] = [];
      let unit_stops = 0;
      const original_create = WorkerUnit.create;
      WorkerUnit.create = async (environment, working_directory, ownership) => {
        calls.push({ operation: 'unit-create', environment, working_directory });
        const temporary = `${item.unit_directory}/t`;
        const unit = new WorkerUnit({
          child_processes: ownership.child_processes,
          name: 'panels-worker-' + '1'.repeat(32) + '.service',
          directory: item.unit_directory,
          environment: { ...environment, TMPDIR: temporary, TMP: temporary, TEMP: temporary },
          working_directory,
          slice_name: 'unit17-synthetic.slice',
        });
        Object.assign(unit, {
          stop: async () => {
            unit_stops += 1;
          },
        });
        return unit;
      };
      childProcess.spawn = ((
        command: string,
        args: readonly string[],
        options: {
          env: Record<string, string>;
          cwd: string;
        },
      ): never => {
        calls.push({
          operation: 'spawn',
          argv: [command, ...args],
          environment: options.env,
          working_directory: options.cwd,
        });
        throw new Error('synthetic stop before spawn');
      }) as unknown as typeof childProcess.spawn;
      syncBuiltinESMExports();
      const client = new CodexAppServerClient({
        child_processes: new ChildProcesses(),
        description: 'startup-parity',
        handler: {
          async on_notification() {},
          async on_server_request() {},
          async on_child_ended() {},
        },
      });
      Object.assign(client, {
        check_version: async (
          executable: string,
          environment: Record<string, string>,
          working_directory: string,
        ) => {
          calls.push({ operation: 'version', executable, environment, working_directory });
        },
      });
      try {
        await client.start({ ...item, environment: child_environment() });
      } catch (error) {
        if (!(error instanceof Error) || error.message !== 'synthetic stop before spawn')
          throw error;
      } finally {
        WorkerUnit.create = original_create;
        childProcess.spawn = forbidden as typeof childProcess.spawn;
        syncBuiltinESMExports();
      }
      launches.push({ name: item.name, calls, unit_stops });
    }
    return { cases, launches, provider_processes_started: 0, service_manager_calls: 0 };
  } finally {
    Object.assign(childProcess, original_process);
    syncBuiltinESMExports();
    for (const name of Object.keys(process.env)) delete process.env[name];
    Object.assign(process.env, original_environment);
  }
}
