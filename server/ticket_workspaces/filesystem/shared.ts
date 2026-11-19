import { randomUUID } from 'node:crypto';
import {
  constants,
  lstatSync,
  readFileSync,
  writeFileSync,
  renameSync,
  mkdirSync,
  openSync,
  closeSync,
  fstatSync,
} from 'node:fs';
import { lstat, readdir, readFile, rename, writeFile } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { flock } from '../../core/locks/flock.ts';
import { git as shared_git } from '../../environments/ticket_scratch/collect.ts';
import { OWNERSHIP_FILE, TICKET_ID_PATTERN } from '../contracts.ts';
import type { WorkspaceOptions, WorkspaceIdentity, WorkspaceMarker } from '../contracts.ts';
export const ticket_match = new RegExp(`^(${TICKET_ID_PATTERN})(?:-|$)`);
export const ticket_mentions = new RegExp(`${TICKET_ID_PATTERN}(?![a-z0-9])`, 'g');
export const token = () => randomUUID().replaceAll('-', '');
export function missing(error: unknown): boolean {
  return (
    error instanceof Error && 'code' in error && ['ENOENT', 'ENOTDIR'].includes(String(error.code))
  );
}
export function stat_sync(path: string) {
  try {
    return lstatSync(path);
  } catch (error) {
    if (missing(error)) return null;
    throw error;
  }
}
export async function entry_stat(path: string) {
  try {
    return await lstat(path);
  } catch (error) {
    if (missing(error)) return null;
    throw error;
  }
}
export function uuid(value: unknown): string {
  if (
    typeof value !== 'string' ||
    !/^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$/.test(
      value,
    )
  )
    throw new Error('Invalid workspace identity');
  return value.replaceAll('-', '').toLowerCase();
}
export function write_json_sync(path: string, value: unknown): void {
  const temporary = path + `.${token()}.tmp`;
  writeFileSync(temporary, JSON.stringify(value, null, 2) + '\n');
  renameSync(temporary, path);
}
export async function write_json(path: string, value: unknown): Promise<void> {
  const temporary = path + `.${token()}.tmp`;
  await writeFile(temporary, JSON.stringify(value, null, 2) + '\n');
  await rename(temporary, path);
}
export async function json<T>(path: string): Promise<T> {
  return JSON.parse(await readFile(path, 'utf8')) as T;
}
export function admit(path: string, options: WorkspaceOptions): string {
  options.signal?.throwIfAborted();
  const absolute = resolve(path);
  return options.admit_path?.(absolute) ?? absolute;
}
export async function git(path: string, args: readonly string[], options: WorkspaceOptions = {}) {
  const timeout = AbortSignal.timeout(
    (args[0] === 'worktree' && args[1] === 'add') || args[0] === 'clone' || args[0] === 'fetch'
      ? 30 * 60 * 1000
      : 60 * 1000,
  );
  const signal = options.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  const environment = {
    ...(options.git_execution?.environment ?? process.env),
    GIT_OPTIONAL_LOCKS: '0',
    GIT_TERMINAL_PROMPT: '0',
  };
  // Shared child owner strips inherited Git overrides. Explicit flags survive that filter.
  return shared_git(path, ['-c', 'core.askPass=', ...args], {
    ...options,
    signal,
    git_execution: options.git_execution ? { ...options.git_execution, environment } : undefined,
  });
}
export function read_workspace_identity(ticket_root: string): WorkspaceIdentity {
  if (stat_sync(ticket_root)?.isSymbolicLink() || stat_sync(dirname(ticket_root))?.isSymbolicLink())
    throw new Error('Ticket workspace must not be a symlink');
  const marker = join(ticket_root, OWNERSHIP_FILE);
  if (stat_sync(marker)?.isSymbolicLink())
    throw new Error('Ticket ownership must not be a symlink');
  const value = JSON.parse(readFileSync(marker, 'utf8')) as WorkspaceMarker;
  return { machine_id: uuid(value.machine_id), workspace_id: uuid(value.workspace_id) };
}
export function workspace_identity(
  ticket_root: string,
  options: { create?: boolean } = {},
): WorkspaceIdentity {
  ticket_root = resolve(ticket_root);
  if (stat_sync(ticket_root)?.isSymbolicLink() || stat_sync(dirname(ticket_root))?.isSymbolicLink())
    throw new Error('Ticket workspace must not be a symlink');
  if (options.create !== false) mkdirSync(ticket_root, { recursive: true });
  const descriptor = openSync(ticket_root, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    if (flock(descriptor, 'exclusive')) throw new Error('Cannot lock Ticket workspace');
    const opened = fstatSync(descriptor),
      attached = lstatSync(ticket_root);
    if (opened.dev !== attached.dev || opened.ino !== attached.ino)
      throw new Error('Ticket workspace changed before ownership inspection');
    const machine_path = join(dirname(dirname(ticket_root)), '.workspace-machine-id');
    const machine = openSync(
      machine_path,
      constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW,
      0o600,
    );
    let machine_id: string;
    try {
      if (flock(machine, 'exclusive')) throw new Error('Cannot lock machine identity');
      const contents = readFileSync(machine, 'utf8').trim();
      machine_id = contents ? uuid(contents) : token();
      if (!contents) writeFileSync(machine, machine_id);
    } finally {
      closeSync(machine);
    }
    const marker_path = join(ticket_root, OWNERSHIP_FILE);
    if (stat_sync(marker_path)?.isSymbolicLink())
      throw new Error('Ticket ownership must not be a symlink');
    const marker = stat_sync(marker_path)
      ? (JSON.parse(readFileSync(marker_path, 'utf8')) as Partial<WorkspaceMarker>)
      : {};
    const ticket_id = ticket_match.exec(basename(ticket_root))?.[1] ?? basename(ticket_root);
    if (marker.ticket_id !== undefined && marker.ticket_id !== ticket_id)
      throw new Error('Ticket workspace ownership does not match');
    if (marker.machine_id !== undefined && marker.machine_id !== machine_id)
      throw new Error('Ticket workspace belongs to another machine');
    const workspace_id = marker.workspace_id ? uuid(marker.workspace_id) : token();
    if (!marker.ticket_id || !marker.machine_id || !marker.workspace_id)
      write_json_sync(marker_path, { ...marker, ticket_id, machine_id, workspace_id });
    return { machine_id, workspace_id };
  } finally {
    closeSync(descriptor);
  }
}
export async function checkouts(root: string): Promise<string[]> {
  const found: string[] = [];
  async function visit(path: string): Promise<void> {
    if (!(await entry_stat(path))?.isDirectory()) return;
    const entries = await readdir(path, { withFileTypes: true });
    if (entries.some((entry) => entry.name === '.git')) found.push(path);
    for (const entry of entries)
      if (entry.isDirectory() && !['.git', '.venv', 'node_modules'].includes(entry.name))
        await visit(join(path, entry.name));
  }
  await visit(root);
  return found;
}
