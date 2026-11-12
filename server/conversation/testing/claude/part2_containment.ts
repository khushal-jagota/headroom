/** Test boundary: real WorkerUnit.argv, zero calls to a service manager. */
import childProcess from 'node:child_process';
import type { SpawnOptions } from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { appendFileSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import type { Part2Spec } from './part2_parity.ts';

export async function install_owned_launch_boundary(spec: Part2Spec): Promise<() => void> {
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
  const name = 'unit18-synthetic-contained.service';
  const directory = `${spec.workspace}/contained-unit`;
  const denied = (): never => {
    throw new Error('Unexpected process API in the owned CLI proof');
  };
  const spawn = (command: string, arguments_: readonly string[] = [], options?: SpawnOptions) => {
    const actual = [command, ...arguments_];
    let delegated = actual;
    if (command === 'systemd-run') {
      const separator = arguments_.indexOf('--');
      if (separator < 0 || !arguments_.includes(`--unit=${name}`)) denied();
      delegated = [...arguments_.slice(separator + 1)];
    }
    const script =
      delegated[0] === spec.executable ||
      (delegated[0] === process.execPath && delegated[1] === spec.executable);
    if (!script) denied();
    const probe = delegated.includes('--version') || delegated.includes('-v');
    if (!probe)
      writeFileSync(
        metadata,
        JSON.stringify({
          launch_argv: actual,
          delegated_argv: delegated,
          wrapped_argv: command === 'systemd-run' ? actual : null,
          environment_file:
            command === 'systemd-run' ? readFileSync(`${directory}/environment`, 'utf8') : null,
        }),
      );
    // This call can receive only the exact owned script, never systemd-run/systemctl.
    return original.spawn(delegated[0], delegated.slice(1), options);
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
  const { WorkerUnit, environment_file } = await import('../../worker_unit/index.ts');
  const originalCreate = WorkerUnit.create;
  WorkerUnit.create = async (environment, working_directory) => {
    if (environment.PLAN_TICKET_ID !== 'unit18-synthetic-worker') denied();
    mkdirSync(`${directory}/t`, { recursive: true, mode: 0o700 });
    const values = {
      ...environment,
      TMPDIR: `${directory}/t`,
      TMP: `${directory}/t`,
      TEMP: `${directory}/t`,
    };
    writeFileSync(`${directory}/environment`, environment_file(values), { mode: 0o600 });
    const unit = new WorkerUnit({
      name,
      directory,
      environment: values,
      working_directory,
      slice_name: 'panels-workers.slice',
    });
    let stopped = false;
    unit.stop = async () => {
      if (stopped) return;
      stopped = true;
      appendFileSync(spec.trace, `${JSON.stringify({ kind: 'contained-stop', name })}\n`);
      rmSync(directory, { recursive: true, force: true });
    };
    return unit;
  };
  return () => {
    WorkerUnit.create = originalCreate;
    Object.assign(childProcess, original);
    syncBuiltinESMExports();
  };
}
