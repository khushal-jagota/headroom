/** Export-only companion. No service manager or provider child may start here. */
import childProcess from 'node:child_process';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { syncBuiltinESMExports } from 'node:module';
import { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';
import * as records from '../../../worker_supervision/records.ts';

type CatalogSpec = {
  operation: 'catalogs';
  cases: { name: string; info: unknown }[];
};

type VersionSpec = {
  operation: 'versions';
  executable: string;
  cases: { name: string; stdout: string; exit_code: number; failure?: string }[];
};

type ByteSpec = {
  operation: 'bytes';
  environment: Record<string, string>;
  working_directory: string;
  commands: string[][];
  owner: {
    pid: number;
    birth: string;
    parent: number;
    group: number;
    session: number;
    zombie: boolean;
  };
  uuid: string;
};

function outcome(call: () => unknown): unknown {
  try {
    return call();
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    return { error: error.message };
  }
}

export async function run_part1_parity(
  spec: ByteSpec | VersionSpec | CatalogSpec,
): Promise<unknown> {
  const attempts: string[] = [];
  const original = {
    spawn: childProcess.spawn,
    spawnSync: childProcess.spawnSync,
    exec: childProcess.exec,
    execSync: childProcess.execSync,
    execFile: childProcess.execFile,
    execFileSync: childProcess.execFileSync,
    fork: childProcess.fork,
  };
  const forbidden = (...args: unknown[]): never => {
    attempts.push(String(args[0]));
    throw new Error('The byte comparison must not start a process');
  };
  Object.assign(childProcess, {
    spawn: forbidden,
    spawnSync: forbidden,
    exec: forbidden,
    execSync: forbidden,
    execFile: forbidden,
    execFileSync: forbidden,
    fork: forbidden,
  });
  syncBuiltinESMExports();
  try {
    if (spec.operation === 'catalogs') {
      const { claude_model_catalog_from_init } =
        await import('../../backends/claude_model_catalog.ts');
      return spec.cases.map((item) => ({
        name: item.name,
        result: (() => {
          try {
            return claude_model_catalog_from_init(item.info);
          } catch (error) {
            if (error instanceof TypeError) return { error_type: error.name };
            if (!(error instanceof Error)) throw error;
            return { error: error.message };
          }
        })(),
      }));
    }
    if (spec.operation === 'versions') {
      const { validate_cli_version, cli_version_error } =
        await import('../../backends/cli_compatibility.ts');
      return spec.cases.map((item) => {
        try {
          if (item.failure) throw cli_version_error('claude', spec.executable, 'check');
          validate_cli_version('claude', spec.executable, item.stdout, item.exit_code);
          return { name: item.name, result: null };
        } catch (error) {
          if (!(error instanceof Error)) throw error;
          return { name: item.name, result: error.message };
        }
      });
    }
    const original_pid = Object.getOwnPropertyDescriptor(process, 'pid')!;
    const original_uuid = crypto.randomUUID;
    Object.defineProperty(process, 'pid', { value: spec.owner.pid });
    crypto.randomUUID = (() => spec.uuid) as typeof crypto.randomUUID;
    syncBuiltinESMExports();
    const runtime = new WorkerSupervisionRuntime({
      primitives: {
        owns_isolated_session_members: false,
        requires_containment: false,
        host_boot: async () => ({ host: 'synthetic-host', boot: 'synthetic-boot' }),
        snapshot: async () => new Map([[spec.owner.pid, spec.owner]]),
        is_absent: async () => {
          throw new Error('Unexpected native inspection');
        },
        signal: async () => {
          throw new Error('Unexpected native signal');
        },
        prepare_launch() {
          throw new Error('Unexpected native launch preparation');
        },
      },
    });
    try {
      const { WorkerSupervisor } = await import('../../../worker_supervision/service.ts');
      const supervisor = await WorkerSupervisor.create(spec.environment, spec.working_directory, {
        runtime,
      });
      if (!supervisor) throw new Error('Worker supervision was not created');
      const pending = await records.read(supervisor.path);
      const argv = spec.commands.map((command) =>
        outcome(() => {
          const result = supervisor.argv(command);
          const prefix = [
            process.execPath,
            fileURLToPath(new URL('../../../worker_supervision/launch.ts', import.meta.url)),
            supervisor.path,
          ];
          if (JSON.stringify(result.slice(0, 3)) !== JSON.stringify(prefix))
            throw new Error('Wrong raw Node bootstrap argv');
          return result;
        }),
      );
      await supervisor.cancel_unspawned();
      return {
        argv,
        environment: supervisor.environment,
        pending,
        revoked: await records.read(supervisor.path),
        child_launch_attempts: attempts,
      };
    } finally {
      await runtime.close();
      Object.defineProperty(process, 'pid', original_pid);
      crypto.randomUUID = original_uuid;
      syncBuiltinESMExports();
    }
  } finally {
    Object.assign(childProcess, original);
    syncBuiltinESMExports();
  }
}
