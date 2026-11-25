import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { ChildProcesses, signal_child } from '../../lifecycle/owned_processes.ts';
import type { SpawnOptions } from 'node:child_process';
import { lstat, stat } from 'node:fs/promises';
import { constants, homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { stop_one_shot_child } from '../../lifecycle/one_shot_children.ts';
import { expand_path, resolved_path } from '../paths.ts';
import { LEGACY_HOME_FOLDERS, SCRATCH_FOLDERS } from './configuration.ts';
import type { GitResult, ScratchLayout, ScratchPassOptions } from './contracts.ts';

export function missing(error: unknown): boolean {
  return (
    error instanceof Error && 'code' in error && ['ENOENT', 'ENOTDIR'].includes(String(error.code))
  );
}
export async function entry_stat(path: string, follow = false) {
  try {
    return await (follow ? stat(path) : lstat(path));
  } catch (error) {
    if (missing(error)) return null;
    throw error;
  }
}
/** Abort targets only the Git child this call owns; no process discovery or shell. */
export function git(
  directory: string,
  args: readonly string[],
  options: ScratchPassOptions = {},
  input?: string,
): Promise<GitResult> {
  options.signal?.throwIfAborted();
  directory = options.admit_path?.(directory) ?? directory;
  return new Promise((resolve, reject) => {
    const launch_options: SpawnOptions = {
      env: {
        ...Object.fromEntries(
          Object.entries(options.git_execution?.environment ?? process.env).filter(
            ([key]) => !key.startsWith('GIT_'),
          ),
        ),
        GIT_OPTIONAL_LOCKS: '0',
        GIT_TERMINAL_PROMPT: '0',
      },
      stdio: [input === undefined ? 'ignore' : 'pipe', 'pipe', 'pipe'],
    };
    const argv = ['-C', directory, ...args];
    const child = options.git_execution
      ? options.git_execution.children.spawn('git', argv, launch_options)
      : new ChildProcesses().spawn('git', argv, { ...launch_options, detached: false });
    const stdout: Buffer[] = [],
      stderr: Buffer[] = [];
    let failure: Error | undefined;
    const input_written =
      input === undefined
        ? Promise.resolve()
        : pipeline(Readable.from([input]), child.stdin!).catch((error: Error) => {
            failure = error;
          });
    child.stdout!.on('data', (chunk: Buffer) => stdout.push(chunk));
    child.stderr!.on('data', (chunk: Buffer) => stderr.push(chunk));
    child.once('error', (error) => {
      failure = error;
    });
    const abort = () => {
      if (options.git_execution) stop_one_shot_child(child);
      else signal_child(child, 'SIGTERM', false);
      child.stdout!.destroy();
      child.stderr!.destroy();
    };
    options.signal?.addEventListener('abort', abort, { once: true });
    child.once('close', async (code, signal) => {
      await input_written;
      options.signal?.removeEventListener('abort', abort);
      if (options.signal?.aborted) {
        reject(options.signal.reason);
        return;
      }
      if (failure) {
        reject(failure);
        return;
      }
      try {
        const decode = (chunks: Buffer[]) =>
          new TextDecoder('utf-8', { fatal: true, ignoreBOM: true })
            .decode(Buffer.concat(chunks))
            .replace(/\r\n?/g, '\n');
        resolve({
          returncode: signal ? -constants.signals[signal] : (code ?? 1),
          stdout: decode(stdout),
          stderr: decode(stderr),
        });
      } catch (error) {
        reject(error);
      }
    });
    // The signal can change after spawn and before listener registration.
    if (options.signal?.aborted) abort();
  });
}
export async function layout_under(
  root: string,
  home = homedir(),
  admit_path?: ScratchPassOptions['admit_path'],
): Promise<ScratchLayout> {
  // Path.resolve does not expand '~' here. Only the database admission gate does.
  const literal = (path: string) => {
    const absolute = isAbsolute(path) ? path : process.cwd() + '/' + path;
    return resolved_path(admit_path?.(absolute) ?? absolute);
  };
  [root, home] = await Promise.all([literal(root), literal(home)]);
  return {
    repository: join(root, 'repository'),
    folders: [
      ...SCRATCH_FOLDERS.map((name) => join(root, name)),
      ...LEGACY_HOME_FOLDERS.map((name) => join(home, name)),
    ],
  };
}
export async function layout_for(
  root: string | null,
  database: string | null,
  db_path: string,
  home?: string,
  admit_path?: ScratchPassOptions['admit_path'],
): Promise<ScratchLayout | null> {
  if (root === null || database === null) return null;
  const admitted_database = (path: string) => {
    const expanded = expand_path(path, home);
    return resolved_path(admit_path?.(expanded) ?? expanded);
  };
  const [configured, actual] = await Promise.all([
    admitted_database(database),
    admitted_database(db_path),
  ]);
  return configured === actual ? layout_under(root, home, admit_path) : null;
}
