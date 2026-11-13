import type { ChildProcess } from 'node:child_process';
import { constants } from 'node:os';
import { randomUUID } from 'node:crypto';
import { join } from 'node:path';
import { python_string_repr } from '../../../core/python_repr.ts';
import { python_os_error_message } from '../../../core/os_errors.ts';
import { is_os_error } from '../../../core/system_errors.ts';
import { stop_one_shot_child } from '../../../lifecycle/one_shot_children.ts';
import { run_release_command } from '../../release/process.ts';
import { AppValidationError } from '../../release/contracts.ts';
import { INSTALLED_RUNTIME_CLASSIFIER_SOURCE } from '../../release/units.ts';
import { healthy } from './health.ts';
import {
  assert_installer_path,
  installer_test_mode,
  SERVICE_COMMAND_TIMEOUT_SECONDS,
  INTERRUPTION_CAPTURE_BUDGET_SECONDS,
  DATABASE_RESTORE_TIMEOUT_SECONDS,
} from './configuration.ts';
import type {
  InstallerCommandOptions,
  InstallerEffects,
  InstallerExecution,
  InstallerPhase,
  InstallerRequest,
} from './contracts.ts';

const command_repr = (argv: readonly string[]): string =>
  '[' + argv.map(python_string_repr).join(', ') + ']';
export class CalledProcessError extends Error {
  readonly argv: readonly string[];
  readonly returncode: number;
  readonly signal: NodeJS.Signals | null;
  constructor(argv: readonly string[], returncode: number, signal: NodeJS.Signals | null) {
    super(
      signal === null
        ? `Command '${command_repr(argv)}' returned non-zero exit status ${returncode}.`
        : `Command '${command_repr(argv)}' died with <Signals.${signal}: ${constants.signals[signal]}>.`,
    );
    this.name = 'CalledProcessError';
    this.argv = argv;
    this.returncode = returncode;
    this.signal = signal;
  }
}
export class TimeoutExpired extends Error {
  readonly argv: readonly string[];
  readonly timeout_seconds: number;
  constructor(argv: readonly string[], timeout_seconds: number) {
    super(`Command '${command_repr(argv)}' timed out after ${timeout_seconds.toFixed(1)} seconds`);
    this.name = 'TimeoutExpired';
    this.argv = argv;
    this.timeout_seconds = timeout_seconds;
  }
}
export function error_detail(error: unknown): { name: string; message: string } {
  if (!(error instanceof Error)) return { name: 'Error', message: String(error) };
  if ('code' in error && typeof error.code === 'string') {
    const names: Readonly<Record<string, string>> = {
      ENOENT: 'FileNotFoundError',
      EEXIST: 'FileExistsError',
      EACCES: 'PermissionError',
      EPERM: 'PermissionError',
      ENOTDIR: 'NotADirectoryError',
      EISDIR: 'IsADirectoryError',
      EPIPE: 'BrokenPipeError',
      ESRCH: 'ProcessLookupError',
      ETIMEDOUT: 'TimeoutError',
      ECONNREFUSED: 'ConnectionRefusedError',
      ECONNRESET: 'ConnectionResetError',
    };
    return { name: names[error.code] ?? 'OSError', message: python_os_error_message(error) };
  }
  return { name: error.name, message: error.message };
}
export function phase(execution: InstallerExecution, timeout_seconds: number): InstallerPhase {
  execution.signal.throwIfAborted();
  return { ...execution, deadline: performance.now() / 1000 + timeout_seconds, timeout_seconds };
}
export function assert_phase(value: InstallerPhase, argv: readonly string[]): void {
  value.signal.throwIfAborted();
  if (performance.now() / 1000 >= value.deadline)
    throw new TimeoutExpired(argv, value.timeout_seconds);
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
    () => fail(new TimeoutExpired(argv, value.timeout_seconds)),
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
      stdio: ['ignore', options.output, options.output],
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
    if (options.check && returncode !== 0) throw new CalledProcessError(argv, returncode, signal);
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
async function runtime(request: InstallerRequest, value: InstallerPhase): Promise<string> {
  assert_installer_path(request.current_root, value.environment);
  assert_installer_path(join(request.current_root, 'app'), value.environment);
  const argv = [
    request.node_executable,
    '--input-type=module',
    '-e',
    INSTALLED_RUNTIME_CLASSIFIER_SOURCE,
    join(request.current_root, 'app'),
    request.admitted_legacy_null_sha ?? '',
  ];
  assert_phase(value, argv);
  let result;
  try {
    result = await run_release_command(
      argv,
      { timeout_seconds: null, environment: { ...value.environment } },
      value,
    );
  } catch (error) {
    assert_phase(value, argv);
    throw error;
  }
  assert_phase(value, argv);
  const selected = result.stdout.trim();
  if (!['node-v2', 'python-v1', 'python-legacy-null'].includes(selected))
    throw new AppValidationError('Unsupported installed runtime classification');
  return selected;
}
export function create_installer_effects(
  request: InstallerRequest,
  execution: InstallerExecution,
): InstallerEffects {
  assert_installer_path(request.current_root, execution.environment);
  assert_installer_path(request.database, execution.environment);
  assert_installer_path(request.data_directory, execution.environment);
  return {
    async service(action) {
      if (installer_test_mode(execution.environment))
        throw new Error('Installer service actions are unavailable in tests');
      const value = phase(execution, SERVICE_COMMAND_TIMEOUT_SECONDS);
      await run_installer_command(['systemctl', '--user', action, request.service_unit], value, {
        check: true,
        output: 'inherit',
      });
    },
    async capture() {
      const value = phase(execution, INTERRUPTION_CAPTURE_BUDGET_SECONDS);
      try {
        const selected = await runtime(request, value);
        const app = join(request.current_root, 'app'),
          capture_id = randomUUID().replaceAll('-', '');
        assert_installer_path(request.database, execution.environment);
        assert_installer_path(app, execution.environment);
        assert_installer_path(
          join(
            app,
            selected === 'node-v2'
              ? 'server/environments/updates/interruption_capture_main.ts'
              : '.venv/bin/python',
          ),
          execution.environment,
        );
        const argv =
          selected === 'node-v2'
            ? [
                request.node_executable,
                join(app, 'server/environments/updates/interruption_capture_main.ts'),
                request.database,
                request.attempt_id,
                capture_id,
              ]
            : [
                join(app, '.venv/bin/python'),
                '-I',
                '-m',
                'planner.environments.interruption_capture',
                request.database,
                request.attempt_id,
                capture_id,
              ];
        return {
          kind: 'returned',
          returncode: await run_installer_command(argv, value, { check: false, output: 'ignore' }),
        };
      } catch (error) {
        execution.signal.throwIfAborted();
        if (!(
          is_os_error(error) ||
          error instanceof CalledProcessError ||
          error instanceof TimeoutExpired ||
          error instanceof AppValidationError
        ))
          throw error;
        return { kind: 'unavailable', detail: error_detail(error).message };
      }
    },
    async restore(snapshot) {
      assert_installer_path(snapshot, execution.environment);
      const value = phase(execution, DATABASE_RESTORE_TIMEOUT_SECONDS);
      const selected = await runtime(request, value),
        app = join(request.current_root, 'app');
      assert_installer_path(request.database, execution.environment);
      assert_installer_path(app, execution.environment);
      assert_installer_path(
        join(
          app,
          selected === 'node-v2' ? 'server/environments/updates/restore_main.ts' : 'bin/panels',
        ),
        execution.environment,
      );
      const argv =
        selected === 'node-v2'
          ? [
              request.node_executable,
              join(app, 'server/environments/updates/restore_main.ts'),
              snapshot,
              request.database,
            ]
          : [
              join(app, 'bin/panels'),
              'environment',
              'restore',
              '--snapshot',
              snapshot,
              '--destination-db',
              request.database,
              '--live-stopped',
            ];
      await run_installer_command(argv, value, { check: true, output: 'inherit' });
    },
    healthy(sha) {
      return healthy(request.health_url, sha, request.health_timeout_seconds, execution.signal);
    },
  };
}
