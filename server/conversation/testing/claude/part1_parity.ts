/** Export-only companion. No service manager or provider child may start here. */
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import childProcess from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';

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
  name: string;
  directory: string;
  environment: Record<string, string>;
  working_directory: string;
  slice_name: string;
  commands: string[][];
  invalid_environment: Record<string, string>[];
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
    const { WorkerUnit, environment_file } = await import('../../worker_unit/index.ts');
    const unit = new WorkerUnit({
      child_processes: new ChildProcesses(),
      name: spec.name,
      directory: spec.directory,
      environment: spec.environment,
      working_directory: spec.working_directory,
      slice_name: spec.slice_name,
    });
    return {
      argv: spec.commands.map((command) => outcome(() => unit.argv(command))),
      environment_hex: Buffer.from(environment_file(spec.environment), 'utf8').toString('hex'),
      invalid_environment: spec.invalid_environment.map((value) =>
        outcome(() => environment_file(value)),
      ),
      child_launch_attempts: attempts,
    };
  } finally {
    Object.assign(childProcess, original);
    syncBuiltinESMExports();
  }
}
