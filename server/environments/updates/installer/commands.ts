import { InstallationRecord } from './record.ts';
import { release_path, selected_release } from './selection.ts';
import type { ChildProcess } from 'node:child_process';
import { constants } from 'node:os';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { realpathSync } from 'node:fs';
import { is_os_error } from '../../../core/system_errors.ts';
import { stop_one_shot_child } from '../../../lifecycle/one_shot_children.ts';
import { AppValidationError } from '../../release/contracts.ts';
import { healthy } from './health.ts';
import {
  assert_installer_path,
  assert_installer_health_url,
  installer_test_mode,
  SERVICE_COMMAND_TIMEOUT_SECONDS,
  INTERRUPTION_CAPTURE_BUDGET_SECONDS,
  DATABASE_SNAPSHOT_TIMEOUT_SECONDS,
  DATABASE_RESTORE_TIMEOUT_SECONDS,
  SCHEMA_STEP_TIMEOUT_SECONDS,
} from './configuration.ts';
import type {
  InstallerCommandOptions,
  InstallerEffects,
  InstallerExecution,
  InstallerPhase,
  InstallerRequest,
} from './contracts.ts';

export class InstallerCommandFailure extends Error {
  readonly argv: readonly string[];
  readonly returncode: number;
  readonly signal: NodeJS.Signals | null;
  constructor(argv: readonly string[], returncode: number, signal: NodeJS.Signals | null) {
    super(
      signal === null
        ? `Command ${JSON.stringify(argv)} exited with status ${returncode}.`
        : `Command ${JSON.stringify(argv)} stopped with signal ${signal}.`,
    );
    this.name = 'InstallerCommandFailure';
    this.argv = argv;
    this.returncode = returncode;
    this.signal = signal;
  }
}
export class InstallerCommandTimeout extends Error {
  readonly argv: readonly string[];
  readonly timeout_seconds: number;
  constructor(argv: readonly string[], timeout_seconds: number) {
    super(`Command ${JSON.stringify(argv)} timed out after ${timeout_seconds} seconds`);
    this.name = 'InstallerCommandTimeout';
    this.argv = argv;
    this.timeout_seconds = timeout_seconds;
  }
}
export function error_detail(error: unknown): { name: string; message: string } {
  if (!(error instanceof Error)) return { name: 'Error', message: String(error) };
  return { name: error.name, message: error.message };
}
export function phase(execution: InstallerExecution, timeout_seconds: number): InstallerPhase {
  execution.signal.throwIfAborted();
  return { ...execution, deadline: performance.now() / 1000 + timeout_seconds, timeout_seconds };
}
export function assert_phase(value: InstallerPhase, argv: readonly string[]): void {
  value.signal.throwIfAborted();
  if (performance.now() / 1000 >= value.deadline)
    throw new InstallerCommandTimeout(argv, value.timeout_seconds);
}
/** Inherited/discarded output and real return status differ from the build runner. */
export async function run_installer_command(
  argv: readonly string[],
  value: InstallerPhase,
  options: InstallerCommandOptions,
): Promise<number> {
  assert_phase(value, argv);
  let child: ChildProcess | undefined,
    failure: unknown,
    exited = false;
  let code: number | null = null,
    signal: NodeJS.Signals | null = null;
  let resolve_close!: () => void;
  const closed = new Promise<void>((resolve) => {
    resolve_close = resolve;
  });
  const fail = (error: unknown) => {
    failure ??= error;
    if (child && !exited) {
      try {
        stop_one_shot_child(child);
      } catch (cause) {
        failure = new AggregateError([failure, cause], 'installer command cleanup failed');
      }
    }
  };
  const abort = () => fail(value.signal.reason);
  value.signal.addEventListener('abort', abort);
  const timer = setTimeout(
    () => fail(new InstallerCommandTimeout(argv, value.timeout_seconds)),
    Math.max(0, value.deadline - performance.now() / 1000) * 1000,
  );
  const fence = () => {
    if (failure !== undefined) throw failure;
    assert_phase(value, argv);
  };
  try {
    fence();
    child = value.child_processes.spawn(argv[0]!, argv.slice(1), {
      env: { ...value.environment },
      stdio: [
        'ignore',
        options.output,
        options.output,
        ...(options.lifetime_lock_fd === undefined ? [] : [options.lifetime_lock_fd]),
      ],
      shell: false,
    });
    child.once('exit', () => {
      exited = true;
    });
    child.on('error', (error) => {
      if (child?.pid === undefined) exited = true;
      fail(error);
    });
    child.once('close', (status, reason) => {
      exited = true;
      code = status;
      signal = reason;
      resolve_close();
    });
    fence();
    await closed;
    fence();
    const returncode = signal === null ? (code ?? -1) : -constants.signals[signal];
    if (options.check && returncode !== 0)
      throw new InstallerCommandFailure(argv, returncode, signal);
    return returncode;
  } catch (error) {
    fail(error);
    throw failure;
  } finally {
    if (child) await closed;
    clearTimeout(timer);
    value.signal.removeEventListener('abort', abort);
  }
}
export function create_installer_effects(
  request: InstallerRequest,
  execution: InstallerExecution,
): InstallerEffects {
  assert_installer_path(request.current_root, execution.environment);
  assert_installer_path(request.database, execution.environment);
  assert_installer_path(request.data_directory, execution.environment);
  assert_installer_health_url(request.health_url, execution.environment);
  const darwin = process.platform === 'darwin';
  let mac: Promise<import('../darwin/service.ts').DarwinHomeService> | undefined;
  const home = () =>
    (mac ??= import('../darwin/service.ts').then(({ create_darwin_home_io, DarwinHomeService }) => {
      const service = new DarwinHomeService(
        request.current_root,
        request.service_unit,
        create_darwin_home_io(
          { child_processes: execution.child_processes, signal: execution.signal, deadline: null },
          execution.environment,
        ),
      );
      const attempt = new InstallationRecord(request).read();
      const pinned = release_path(request.current_root, String(attempt.from_sha));
      if (realpathSync(request.node_executable) !== join(pinned, 'node/bin/node'))
        throw new AppValidationError('Installer Node differs from the retained installation');
      return service;
    }));
  let effects:
    | Promise<ReturnType<typeof import('../darwin/service.ts').create_darwin_installer_effects>>
    | undefined;
  const home_effects = () =>
    (effects ??= import('../darwin/service.ts').then(({ create_darwin_installer_effects }) =>
      create_darwin_installer_effects(home, (sha) =>
        healthy(
          request.health_url,
          sha,
          request.health_timeout_seconds,
          execution.signal,
          execution.environment,
        ),
      ),
    ));
  return {
    ...(darwin
      ? {
          async resume_interrupted() {
            // The coordinator was recreated after a crash or login. An absent job
            // is permitted here; any loaded job still has to authenticate.
            (await home()).phase = 'stopped';
          },
          async validate_service() {
            await (await home_effects()).validate_service();
          },
          async recover_unchanged(sha: string) {
            return (await home_effects()).recover_unchanged(sha);
          },
          async finish_success() {
            return (await home_effects()).finish_success();
          },
          async finish_rollback() {
            return (await home_effects()).finish_rollback();
          },
        }
      : {}),
    async service(action) {
      if (installer_test_mode(execution.environment))
        throw new Error('Installer service actions are unavailable in tests');
      if (darwin) {
        return (await home_effects()).service(action);
      }
      const value = phase(execution, SERVICE_COMMAND_TIMEOUT_SECONDS);
      await run_installer_command(['systemctl', '--user', action, request.service_unit], value, {
        check: true,
        output: 'inherit',
      });
    },
    async capture() {
      try {
        const app = join(request.current_root, 'app'),
          capture_id = randomUUID().replaceAll('-', ''),
          main = join(app, 'server/environments/updates/interruption_capture_main.ts');
        assert_installer_path(request.database, execution.environment);
        assert_installer_path(app, execution.environment);
        assert_installer_path(main, execution.environment);
        const argv = [
          request.node_executable,
          main,
          request.database,
          request.attempt_id,
          capture_id,
        ];
        return {
          kind: 'returned',
          returncode: await run_installer_command(
            argv,
            phase(execution, INTERRUPTION_CAPTURE_BUDGET_SECONDS),
            { check: false, output: 'ignore' },
          ),
        };
      } catch (error) {
        execution.signal.throwIfAborted();
        if (!(
          is_os_error(error) ||
          error instanceof InstallerCommandFailure ||
          error instanceof InstallerCommandTimeout ||
          error instanceof AppValidationError
        ))
          throw error;
        return { kind: 'unavailable', detail: error_detail(error).message };
      }
    },
    async snapshot(deployed_revision, lifetime_lock_fd) {
      const value = phase(execution, DATABASE_SNAPSHOT_TIMEOUT_SECONDS);
      const app = join(request.current_root, 'app'),
        main = join(app, 'server/environments/updates/snapshot_main.ts');
      for (const path of [request.database, request.backup_directory, app, main])
        assert_installer_path(path, execution.environment);
      await run_installer_command(
        [
          request.node_executable,
          main,
          request.database,
          request.backup_directory,
          deployed_revision,
          request.data_directory,
          request.attempt_id,
        ],
        value,
        { check: true, output: 'inherit', lifetime_lock_fd },
      );
    },
    async restore(snapshot, lifetime_lock_fd) {
      assert_installer_path(snapshot, execution.environment);
      const value = phase(execution, DATABASE_RESTORE_TIMEOUT_SECONDS);
      const previous = release_path(
        request.current_root,
        String(new InstallationRecord(request).read().from_sha),
      );
      const app = previous,
        main = join(app, 'server/environments/updates/restore_main.ts');
      assert_installer_path(request.database, execution.environment);
      assert_installer_path(app, execution.environment);
      assert_installer_path(main, execution.environment);
      const argv = [request.node_executable, main, snapshot, request.database];
      await run_installer_command(argv, value, {
        check: true,
        output: 'inherit',
        lifetime_lock_fd,
      });
    },
    async schema_steps(lifetime_lock_fd, backup_taken) {
      const value = phase(execution, SCHEMA_STEP_TIMEOUT_SECONDS);
      const app = selected_release(request.current_root);
      const node =
        app === join(request.current_root, 'app')
          ? request.node_executable
          : join(app, 'node/bin/node');
      const main = join(app, 'server/environments/updates/schema_step_main.ts');
      assert_installer_path(request.database, execution.environment);
      assert_installer_path(app, execution.environment);
      assert_installer_path(main, execution.environment);
      await run_installer_command(
        [node, main, request.database, backup_taken ? 'backup' : 'no-backup'],
        value,
        { check: true, output: 'inherit', lifetime_lock_fd },
      );
    },
    healthy(sha, prepared_attempt_id) {
      assert_installer_health_url(request.health_url, execution.environment);
      return healthy(
        request.health_url,
        sha,
        request.health_timeout_seconds,
        execution.signal,
        execution.environment,
        false,
        undefined,
        prepared_attempt_id,
      );
    },
  };
}
