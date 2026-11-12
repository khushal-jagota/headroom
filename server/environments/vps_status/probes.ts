import { OneShotChildren } from '../../lifecycle/one_shot_children.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import { statfs, opendir, stat } from 'node:fs/promises';
import { platform } from 'node:os';
import { relative, isAbsolute } from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import { is_directory, is_file, is_symlink, resolved_path } from '../backup/evidence.ts';
import type { VpsStatusDependencies } from './contracts.ts';
import {
  DIRECTORY_ENTRIES,
  GIT_OUTPUT_CHARACTERS,
  PROCESS_OUTPUT_CHARACTERS,
  PROBE_TIMEOUT_MILLISECONDS,
} from './configuration.ts';
export async function bounded_children(root: string): Promise<string[]> {
  const directory = await opendir(root);
  const result: string[] = [];
  try {
    while (result.length < DIRECTORY_ENTRIES) {
      const entry = await directory.read();
      if (entry === null) break;
      result.push(root + '/' + entry.name);
    }
  } finally {
    await directory.close();
  }
  return result;
}
export async function contained(root: string, path: string): Promise<boolean> {
  const suffix = relative(await resolved_path(root), await resolved_path(path));
  return suffix !== '..' && !suffix.startsWith('../') && !isAbsolute(suffix);
}
export async function safe_under(root: string, path: string, directory: boolean): Promise<boolean> {
  return (
    (await is_directory(root)) &&
    !(await is_symlink(root)) &&
    !(await is_symlink(path)) &&
    (await (directory ? is_directory(path) : is_file(path))) &&
    (await contained(root, path))
  );
}
export async function modified_microseconds(path: string): Promise<bigint> {
  const metadata = await stat(path, { bigint: true });
  // Python stat exposes floating-point seconds before datetime rounds to microseconds.
  const seconds = Number(metadata.mtimeNs) / 1_000_000_000;
  const whole = Math.floor(seconds);
  return BigInt(whole) * 1_000_000n + BigInt(Math.round((seconds - whole) * 1_000_000));
}
export const characters = (value: string, limit: number): string =>
  Array.from(value).slice(0, limit).join('');
async function command(
  executable: string,
  arguments_: readonly string[],
  limit: number,
  shared?: OwnedOneShotChildren,
): Promise<string> {
  const children = shared ?? new OneShotChildren();
  try {
    return await new Promise<string>((resolve, reject) => {
      const child = children.spawn(executable, arguments_, { stdio: ['ignore', 'pipe', 'ignore'] });
      const decoder = new StringDecoder('utf8');
      let output = '';
      const timer = setTimeout(() => {
        child.kill('SIGKILL');
        reject(new Error('status probe timed out'));
      }, PROBE_TIMEOUT_MILLISECONDS);
      child.stdout!.on('data', (chunk: Buffer) => {
        output = characters(output + decoder.write(chunk), limit);
      });
      child.once('error', (error) => {
        clearTimeout(timer);
        reject(error);
      });
      child.once('close', (code) => {
        clearTimeout(timer);
        if (code !== 0) reject(new Error('status probe failed'));
        else resolve(characters(output + decoder.end(), limit));
      });
    });
  } finally {
    if (!shared) {
      children.seal();
      await children.settled();
    }
  }
}
/** Production passes its invocation-owned cohort. Plain typed CLI calls own theirs. */
export function create_vps_status_dependencies(
  children: OwnedOneShotChildren,
): VpsStatusDependencies {
  return {
    ...DEFAULT_DEPENDENCIES,
    process_lines: async () =>
      (
        await command(
          'ps',
          ['-axo', 'pid=,ppid=,etime=,command='],
          PROCESS_OUTPUT_CHARACTERS,
          children,
        )
      ).split(/\r?\n/),
    git_worktree_output: (root) =>
      command(
        'git',
        ['-C', root, 'worktree', 'list', '--porcelain'],
        GIT_OUTPUT_CHARACTERS,
        children,
      ),
  };
}
export const DEFAULT_DEPENDENCIES: VpsStatusDependencies = {
  now: () => new Date().toISOString(),
  platform_name: () =>
    platform() === 'darwin' ? 'Darwin' : platform() === 'linux' ? 'Linux' : platform(),
  async statvfs(path) {
    const value = await statfs(path, { bigint: true });
    return { f_frsize: value.bsize, f_blocks: value.blocks, f_bavail: value.bavail };
  },
  async process_lines() {
    return (
      await command('ps', ['-axo', 'pid=,ppid=,etime=,command='], PROCESS_OUTPUT_CHARACTERS)
    ).split(/\r?\n/);
  },
  git_worktree_output: (root) =>
    command('git', ['-C', root, 'worktree', 'list', '--porcelain'], GIT_OUTPUT_CHARACTERS),
  live_reference_paths: async () => [],
};
