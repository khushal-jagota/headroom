/** Bounded release commands own and reap their exact ChildProcess handle. */
import { spawn } from 'node:child_process';
import { access, stat } from 'node:fs/promises';
import { constants } from 'node:fs';
import { delimiter, join } from 'node:path';
import { AppValidationError } from './contracts.ts';
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
export function run_release_command(
  argv: readonly string[],
  options: ReleaseCommandOptions,
): Promise<ReleaseCommandResult> {
  if (options.timeout_seconds !== null && options.timeout_seconds <= 0)
    return Promise.reject(new AppValidationError('runtime admission exceeded its time budget'));
  return new Promise((resolve, reject) => {
    const child = spawn(argv[0], argv.slice(1), {
      cwd: options.cwd,
      env: options.environment,
      stdio: ['ignore', 'pipe', 'pipe'],
      shell: false,
    });
    const stdout: Buffer[] = [],
      stderr: Buffer[] = [];
    let bytes = 0,
      failure: unknown;
    const fail = (error: unknown) => {
      if (failure !== undefined) return;
      failure = error;
      child.kill('SIGKILL');
      // Descendants cannot retain our bounded pipe after the owned child exits.
      child.stdout.destroy();
      child.stderr.destroy();
    };
    const timer =
      options.timeout_seconds === null
        ? undefined
        : setTimeout(
            () => fail(new AppValidationError(`release command timed out: ${argv[0]}`)),
            options.timeout_seconds * 1000,
          );
    const capture = (target: Buffer[]) => (chunk: Buffer) => {
      if (failure !== undefined) return;
      bytes += chunk.length;
      if (bytes > MAX_BUILD_COMMAND_OUTPUT_BYTES) {
        fail(new AppValidationError(`release command output exceeded limit: ${argv[0]}`));
        return;
      }
      target.push(chunk);
    };
    child.stdout.on('data', capture(stdout));
    child.stderr.on('data', capture(stderr));
    child.stdout.on('error', fail);
    child.stderr.on('error', fail);
    child.on('error', fail);
    child.on('close', (code, signal) => {
      clearTimeout(timer);
      if (failure !== undefined) {
        reject(failure);
        return;
      }
      try {
        const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
        const result = {
          code: code ?? -1,
          stdout: decoder.decode(Buffer.concat(stdout)),
          stderr: decoder.decode(Buffer.concat(stderr)),
        };
        if (signal !== null || (result.code !== 0 && !options.allow_nonzero))
          throw new AppValidationError(
            `release command failed: ${argv[0]}: ${(result.stderr || result.stdout).slice(-2000)}`,
          );
        resolve(result);
      } catch (error) {
        reject(error);
      }
    });
  });
}
