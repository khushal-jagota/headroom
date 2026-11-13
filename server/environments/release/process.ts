/** Bounded release commands own and reap their exact ChildProcess handle. */
import type { ChildProcess } from 'node:child_process';
import { access, mkdtemp, open, rm, stat } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { constants } from 'node:fs';
import { delimiter, join } from 'node:path';
import { AppValidationError } from './contracts.ts';
import type { ReleaseExecution } from './contracts.ts';
import { stop_one_shot_child } from '../../lifecycle/one_shot_children.ts';
import { MAX_BUILD_COMMAND_OUTPUT_BYTES } from './process_configuration.ts';

export interface ReleaseCommandResult {
  readonly code: number;
  readonly stdout: string;
  readonly stderr: string;
}
export interface ReleaseCommandOptions {
  /** Null is source-equivalent unbounded build work. Runtime admission never uses it. */
  readonly timeout_seconds: number | null;
  readonly cwd?: string;
  readonly environment?: NodeJS.ProcessEnv;
  readonly allow_nonzero?: boolean;
}
export async function find_release_executable(
  name: string,
  environment: NodeJS.ProcessEnv = process.env,
): Promise<string | null> {
  const candidates = name.includes('/')
    ? [name]
    : (environment.PATH ?? '/usr/bin:/bin')
        .split(delimiter)
        .map((directory) => join(directory || '.', name));
  for (const path of candidates) {
    try {
      if (!(await stat(path)).isFile()) continue;
      await access(path, constants.X_OK);
      return path;
    } catch (error) {
      if (!(
        error instanceof Error &&
        'code' in error &&
        ['ENOENT', 'ENOTDIR', 'EACCES'].includes(String(error.code))
      ))
        throw error;
    }
  }
  return null;
}
export function assert_release_execution(execution: ReleaseExecution): void {
  execution.signal.throwIfAborted();
  if (execution.deadline !== null && performance.now() / 1000 >= execution.deadline)
    throw new AppValidationError('release operation exceeded its time budget');
}
export async function run_release_command(
  argv: readonly string[],
  options: ReleaseCommandOptions,
  execution: ReleaseExecution,
): Promise<ReleaseCommandResult> {
  assert_release_execution(execution);
  if (options.timeout_seconds !== null && options.timeout_seconds <= 0)
    throw new AppValidationError('release operation exceeded its time budget');
  const until = Math.min(
    execution.deadline ?? Infinity,
    options.timeout_seconds === null
      ? Infinity
      : performance.now() / 1000 + options.timeout_seconds,
  );
  let state: string | undefined, output: FileHandle | undefined, errors: FileHandle | undefined;
  let child: ChildProcess | undefined,
    exited = false,
    failure: unknown;
  let release!: () => void;
  const closed = new Promise<void>((resolve) => {
    release = resolve;
  });
  let spawned = false;
  const fail = (error: unknown) => {
    failure ??= error;
    if (child && !exited) {
      try {
        stop_one_shot_child(child);
      } catch (cause) {
        failure = new AggregateError([failure, cause], 'release command force failed');
      }
    }
  };
  const abort = () => fail(execution.signal.reason);
  execution.signal.addEventListener('abort', abort);
  const timer = Number.isFinite(until)
    ? setTimeout(
        () => fail(new AppValidationError(`release command timed out: ${argv[0]}`)),
        Math.max(0, until - performance.now() / 1000) * 1000,
      )
    : undefined;
  const fence = () => {
    if (failure !== undefined) throw failure;
    assert_release_execution(execution);
    if (performance.now() / 1000 >= until)
      throw new AppValidationError(`release command timed out: ${argv[0]}`);
  };
  try {
    fence();
    state = await mkdtemp(join(tmpdir(), 'panels-build-command-'));
    fence();
    output = await open(join(state, 'stdout'), 'wx+', 0o600);
    fence();
    errors = await open(join(state, 'stderr'), 'wx+', 0o600);
    fence();
    child = execution.child_processes.spawn(argv[0]!, argv.slice(1), {
      cwd: options.cwd,
      env: options.environment,
      stdio: ['ignore', output.fd, errors.fd],
      shell: false,
    });
    spawned = true;
    let code: number | null = null,
      signal: NodeJS.Signals | null = null;
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
      release();
    });
    fence();
    await closed;
    fence();
    // File-backed output cannot delay close through a descendant's inherited pipe.
    if ((await output.stat()).size + (await errors.stat()).size > MAX_BUILD_COMMAND_OUTPUT_BYTES)
      throw new AppValidationError(`release command output exceeded limit: ${argv[0]}`);
    let captured = 0;
    const decode = async (file: FileHandle): Promise<string> => {
      const chunks: Buffer[] = [],
        buffer = Buffer.alloc(64 * 1024);
      let position = 0;
      for (;;) {
        fence();
        const { bytesRead } = await file.read(buffer, 0, buffer.length, position);
        if (!bytesRead) break;
        captured += bytesRead;
        if (captured > MAX_BUILD_COMMAND_OUTPUT_BYTES)
          throw new AppValidationError(`release command output exceeded limit: ${argv[0]}`);
        chunks.push(Buffer.from(buffer.subarray(0, bytesRead)));
        position += bytesRead;
      }
      return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
        Buffer.concat(chunks),
      );
    };
    const result = { code: code ?? -1, stdout: await decode(output), stderr: await decode(errors) };
    fence();
    if (signal !== null || (result.code !== 0 && !options.allow_nonzero))
      throw new AppValidationError(
        `release command failed: ${argv[0]}: ${(result.stderr || result.stdout).slice(-2000)}`,
      );
    return result;
  } catch (error) {
    fail(error);
    throw failure;
  } finally {
    if (spawned) await closed;
    clearTimeout(timer);
    execution.signal.removeEventListener('abort', abort);
    try {
      await errors?.close();
    } finally {
      try {
        await output?.close();
      } finally {
        if (state) await rm(state, { recursive: true, force: true });
      }
    }
  }
}
