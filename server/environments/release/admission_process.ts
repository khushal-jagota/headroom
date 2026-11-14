/** Probe-local capture and cleanup, backed by the invocation's actual native owner. */
import { randomUUID } from 'node:crypto';
import { open, unlink } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import type { ChildProcess } from 'node:child_process';
import { join } from 'node:path';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import { stop_one_shot_child } from '../../lifecycle/one_shot_children.ts';
import { observe_promise } from '../../core/promise_observer.ts';
import { python_os_error_message } from '../../core/os_errors.ts';
import { AppValidationError } from './contracts.ts';
import {
  RUNTIME_PROBE_TIMEOUT_SECONDS,
  RUNTIME_PROBE_REPORT_CHARACTERS,
  RUNTIME_PROBE_DIAGNOSTIC_BYTES,
} from './process_configuration.ts';

/** The bounded observer failed. This promise still owns actual close and capture cleanup. */
export class RuntimeProbeUnsettled extends AppValidationError {
  readonly settled: Promise<void>;
  readonly cause: unknown;
  constructor(settled: Promise<void>, cause: unknown) {
    super('runtime probe force failed; actual child cleanup is still pending');
    this.settled = settled;
    this.cause = cause;
  }
}

export function probe_environment(state: string): NodeJS.ProcessEnv {
  return {
    PATH: '/usr/local/bin:/usr/bin:/bin',
    HOME: state,
    TMPDIR: state,
    XDG_CONFIG_HOME: join(state, 'config'),
    XDG_CACHE_HOME: join(state, 'cache'),
    LANG: 'C.UTF-8',
    PYTHONDONTWRITEBYTECODE: '1',
    PLAN_DISPATCH_ENABLED: 'false',
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1',
    DISABLE_AUTOUPDATER: '1',
    CODEX_HOME: join(state, 'codex'),
    CLAUDE_CONFIG_DIR: join(state, 'claude'),
    HERMES_HOME: join(state, 'hermes'),
    PLAN_HERMES_HOME: join(state, 'hermes'),
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    PLAN_CONFIG_PATH: join(state, 'config.yaml'),
    PLAN_DB_PATH: join(state, 'data/probe.sqlite'),
    PLAN_LOGS_DIR: join(state, 'logs'),
    PLAN_BACKUP_DIR: join(state, 'backups'),
    PLAN_APP_ROOT: join(state, 'app'),
    PLAN_WORKSPACE_ROOT: join(state, 'workspace'),
    PLAN_TICKET_SCRATCH_ROOT: join(state, 'scratch'),
    PLAN_TICKET_SCRATCH_DATABASE: join(state, 'data/probe.sqlite'),
    PLAN_DISPATCHER_LOCK_PATH: join(state, 'dispatcher.lock'),
    PLAN_SERVER_CONTROL_SOCKET: join(state, 'control.sock'),
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(state, 'lifecycle.lock'),
    PLAN_CREDENTIAL_KEY_PATH: join(state, 'credential.key'),
  };
}
async function tail(file: FileHandle): Promise<string> {
  const size = (await file.stat()).size,
    buffer = Buffer.alloc(Math.min(size, RUNTIME_PROBE_DIAGNOSTIC_BYTES));
  const { bytesRead } = await file.read(
    buffer,
    0,
    buffer.length,
    Math.max(0, size - buffer.length),
  );
  return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
    buffer.subarray(0, bytesRead),
  );
}
async function json_text(file: FileHandle): Promise<string> {
  const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
  let text = '',
    characters = 0,
    position = 0;
  const buffer = Buffer.alloc(64 * 1024);
  while (true) {
    const { bytesRead } = await file.read(buffer, 0, buffer.length, position);
    const piece = decoder.decode(buffer.subarray(0, bytesRead), { stream: bytesRead !== 0 });
    characters += Array.from(piece).length;
    if (characters > RUNTIME_PROBE_REPORT_CHARACTERS)
      throw new AppValidationError('runtime probe report is too large');
    text += piece;
    if (!bytesRead) return text;
    position += bytesRead;
  }
}
async function run_owned(
  argv: readonly string[],
  state: string,
  deadline: number,
  owner: OwnedChildProcesses,
  json_output: boolean,
): Promise<unknown> {
  if (deadline <= performance.now() / 1000)
    throw new AppValidationError('runtime admission exceeded its time budget');
  const path = join(state, `probe-${randomUUID()}`),
    error_path = path + '.stderr';
  const output = await open(path, 'wx+', 0o600);
  let errors: FileHandle | undefined;
  let timeout: AbortSignal | undefined,
    abort: (() => void) | undefined;
  let actually_closed = false,
    deferred_cleanup: Promise<void> | undefined;
  const cleanup = async (): Promise<void> => {
    try {
      if (errors !== undefined && errors !== output) await errors.close();
    } finally {
      await output.close();
      await unlink(path);
      if (errors !== undefined && errors !== output) await unlink(error_path);
    }
  };
  try {
    errors = json_output ? await open(error_path, 'wx+', 0o600) : output;
    const now = performance.now() / 1000,
      until = Math.min(deadline, now + RUNTIME_PROBE_TIMEOUT_SECONDS);
    if (until <= now) throw new AppValidationError('runtime admission exceeded its time budget');
    let close!: (value: { code: number | null; signal: NodeJS.Signals | null }) => void;
    const closed = new Promise<{ code: number | null; signal: NodeJS.Signals | null }>(
      (resolve) => {
        close = resolve;
      },
    );
    let exited = false,
      failure: unknown;
    let force_failed!: () => void;
    const force_failure = new Promise<null>((resolve) => {
      force_failed = () => resolve(null);
    });
    let child: ChildProcess;
    const fail = (cause: unknown) => {
      failure ??= cause;
      if (exited || child.pid === undefined || child.exitCode !== null || child.signalCode !== null)
        return;
      try {
        stop_one_shot_child(child);
      } catch (error) {
        failure = new AggregateError([failure, error], 'runtime probe force failed');
        force_failed();
      }
    };
    // Owner reserves actual close before spawn and publishes this exact handle before any await.
    child = owner.spawn(argv[0]!, argv.slice(1), {
      cwd: state,
      env: probe_environment(state),
      stdio: ['ignore', output.fd, errors.fd],
    });
    child.once('exit', () => {
      exited = true;
    });
    child.on('error', (error) => {
      if (child.pid === undefined) exited = true;
      fail(error);
    });
    child.once('close', (code, signal) => {
      exited = true;
      actually_closed = true;
      close({ code, signal });
    });
    timeout = AbortSignal.timeout(
      Math.max(1, Math.ceil((until - performance.now() / 1000) * 1000)),
    );
    abort = () => fail(new AppValidationError('runtime probe failed: command timed out'));
    timeout.addEventListener('abort', abort, { once: true });
    let result = await Promise.race([closed, force_failure]);
    if (result === null) {
      const observed = await observe_promise(
        closed,
        Math.max(0, deadline - performance.now() / 1000) * 1000,
      );
      if (!observed.completed) {
        const observation = new AppValidationError(
          'Runtime probe did not close before its original admission deadline',
        );
        if (!actually_closed) {
          deferred_cleanup = closed.then(cleanup);
          // The caller receives this actual continuation even after its bounded observer fails.
          void deferred_cleanup.catch(() => {});
          throw new RuntimeProbeUnsettled(
            deferred_cleanup,
            new AggregateError([failure, observation], 'runtime probe force and close failed'),
          );
        }
        throw failure;
      }
      result = observed.value;
    }
    if (performance.now() / 1000 >= until)
      fail(new AppValidationError('runtime probe failed: command timed out'));
    timeout.removeEventListener('abort', abort);
    if (failure !== undefined) throw failure;
    if (result.code !== 0 || result.signal !== null)
      throw new AppValidationError(`runtime probe failed: ${await tail(errors)}`);
    if (!json_output) return (await tail(output)).trim();
    const text = await json_text(output);
    try {
      return JSON.parse(text) as unknown;
    } catch {
      throw new AppValidationError('runtime probe returned no valid JSON');
    }
  } finally {
    if (timeout && abort) timeout.removeEventListener('abort', abort);
    if (deferred_cleanup === undefined) await cleanup();
  }
}
export async function run_runtime_probe(
  argv: readonly string[],
  state: string,
  deadline: number,
  child_processes: OwnedChildProcesses,
): Promise<string> {
  return (await runtime_probe(argv, state, deadline, child_processes, false)) as string;
}
async function runtime_probe(
  argv: readonly string[],
  state: string,
  deadline: number,
  child_processes: OwnedChildProcesses,
  json_output: boolean,
): Promise<unknown> {
  try {
    return await run_owned(argv, state, deadline, child_processes, json_output);
  } catch (error) {
    if (error instanceof Error && 'code' in error && typeof error.code === 'string')
      throw new AppValidationError(`runtime probe failed: ${python_os_error_message(error)}`);
    throw error;
  }
}
export function run_runtime_json_probe(
  argv: readonly string[],
  state: string,
  deadline: number,
  child_processes: OwnedChildProcesses,
): Promise<unknown> {
  return runtime_probe(argv, state, deadline, child_processes, true);
}
