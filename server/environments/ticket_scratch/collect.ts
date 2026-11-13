import { spawn } from 'node:child_process';
import type { SpawnOptions } from 'node:child_process';
import { lstat, readdir, stat } from 'node:fs/promises';
import { constants, homedir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { createReadAccess } from '../../core/database/index.ts';
import { stop_one_shot_child } from '../../lifecycle/one_shot_children.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { expand_path, resolved_path } from '../paths.ts';
import { LEGACY_HOME_FOLDERS, SCRATCH_FOLDERS } from './configuration.ts';
import type {
  GitResult,
  ScratchLayout,
  ScratchPassOptions,
  TicketScratch,
  TicketStates,
  Worktree,
} from './contracts.ts';
import {
  compare_paths,
  compare_strings,
  finished,
  inside,
  ticket_id_of_branch,
  ticket_id_of_name,
  ticket_id_of_path,
} from './logic.ts';

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
): Promise<GitResult> {
  options.signal?.throwIfAborted();
  directory = options.admit_path?.(directory) ?? directory;
  return new Promise((resolve, reject) => {
    const launch_options: SpawnOptions = {
      env: Object.fromEntries(
        Object.entries(options.git_execution?.environment ?? process.env).filter(
          ([key]) => !key.startsWith('GIT_'),
        ),
      ),
      stdio: ['ignore', 'pipe', 'pipe'],
    };
    const argv = ['-C', directory, ...args];
    const child = options.git_execution
      ? options.git_execution.children.spawn('git', argv, launch_options)
      : spawn('git', argv, launch_options);
    const stdout: Buffer[] = [],
      stderr: Buffer[] = [];
    let failure: Error | undefined;
    child.stdout!.on('data', (chunk: Buffer) => stdout.push(chunk));
    child.stderr!.on('data', (chunk: Buffer) => stderr.push(chunk));
    child.once('error', (error) => {
      failure = error;
    });
    const abort = () => {
      if (options.git_execution) stop_one_shot_child(child);
      else child.kill('SIGTERM');
      child.stdout!.destroy();
      child.stderr!.destroy();
    };
    options.signal?.addEventListener('abort', abort, { once: true });
    child.once('close', (code, signal) => {
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
export function read_ticket_states(sql: ReadSql): TicketStates {
  return new Map(
    sql
      .all<{ id: string; stage: string; updated_at: number }>(
        'SELECT id, stage, updated_at FROM tickets',
      )
      .map((row) => [String(row.id), [String(row.stage), Number(row.updated_at)] as const]),
  );
}
export async function read_ticket_states_from(db_path: string): Promise<TicketStates> {
  const reader = createReadAccess(
    await resolved_path(isAbsolute(db_path) ? db_path : process.cwd() + '/' + db_path),
  );
  try {
    return reader.read(read_ticket_states);
  } finally {
    reader.close();
  }
}
export async function list_worktrees(
  repository: string,
  options: ScratchPassOptions = {},
): Promise<Worktree[]> {
  const output = await git(repository, ['worktree', 'list', '--porcelain'], options);
  if (output.returncode !== 0) return [];
  const result: Worktree[] = [];
  for (const block of output.stdout.trim().split('\n\n').slice(1)) {
    let path: string | null = null,
      branch: string | null = null;
    for (const line of block.split('\n')) {
      if (line.startsWith('worktree ')) path = line.slice('worktree '.length);
      else if (line.startsWith('branch '))
        branch = line.slice('branch '.length).replace(/^refs\/heads\//, '');
    }
    if (path !== null) result.push({ path, branch });
  }
  return result;
}
export async function collect_ticket_scratch(
  layout: ScratchLayout,
  states: TicketStates,
  now: number,
  options: ScratchPassOptions = {},
): Promise<TicketScratch[]> {
  options.signal?.throwIfAborted();
  if (options.admit_path)
    layout = {
      repository: options.admit_path(layout.repository),
      folders: layout.folders.map((path) => options.admit_path!(path)),
    };
  if ((await entry_stat(join(layout.repository, '.git'), true)) === null) return [];
  const entries = new Map<string, Set<string>>(),
    branches = new Map<string, Set<string>>();
  const add = (map: Map<string, Set<string>>, owner: string, value: string) => {
    if (!map.has(owner)) map.set(owner, new Set());
    map.get(owner)!.add(value);
  };
  for (const folder of layout.folders) {
    options.signal?.throwIfAborted();
    if (!(await entry_stat(folder))?.isDirectory()) continue;
    for (const child of await readdir(folder)) {
      const owner = ticket_id_of_name(child);
      if (owner !== null) add(entries, owner, join(folder, child));
    }
  }
  for (const worktree of await list_worktrees(layout.repository, options)) {
    if (layout.folders.some((folder) => inside(worktree.path, folder))) continue;
    const owner = ticket_id_of_path(worktree.path) ?? ticket_id_of_branch(worktree.branch);
    if (owner !== null) add(entries, owner, worktree.path);
  }
  const local = await git(
    layout.repository,
    ['for-each-ref', '--format=%(refname:short)', 'refs/heads'],
    options,
  );
  if (local.returncode === 0) {
    for (const branch of local.stdout.trim().split(/\s+/).filter(Boolean)) {
      const owner = ticket_id_of_branch(branch);
      if (owner !== null) add(branches, owner, branch);
    }
  }
  options.signal?.throwIfAborted();
  return [...new Set([...entries.keys(), ...branches.keys()])]
    .sort(compare_strings)
    .filter((owner) => finished(owner, states, now))
    .map((ticket_id) => ({
      ticket_id,
      entries: [...(entries.get(ticket_id) ?? [])].sort(compare_paths),
      branches: [...(branches.get(ticket_id) ?? [])].sort(compare_strings),
    }));
}
