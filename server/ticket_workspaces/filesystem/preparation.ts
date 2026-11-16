import { mkdir, readdir, realpath } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { OWNERSHIP_FILE, WORKTREE_DIRECTORY } from '../contracts.ts';
import type { RetirementRecord, WorkspaceMarker, WorkspaceOptions } from '../contracts.ts';
import { admit, entry_stat, git, json, workspace_identity, write_json } from './shared.ts';

export async function repository_root(
  folder: string | null,
  options: WorkspaceOptions = {},
): Promise<string | null> {
  if (folder === null || !(await entry_stat(admit(folder, options)))?.isDirectory()) return null;
  for (const candidate of [folder, join(folder, 'repository')]) {
    if (!(await entry_stat(candidate))?.isDirectory()) continue;
    const result = await git(candidate, ['rev-parse', '--show-toplevel'], options);
    if (result.returncode === 0) return realpath(result.stdout.trim());
  }
  const children: string[] = [];
  for (const child of await readdir(folder, { withFileTypes: true }))
    if (child.isDirectory() && (await entry_stat(join(folder, child.name, '.git'))))
      children.push(join(folder, child.name));
  if (children.length > 1)
    throw new Error(
      'Project folder contains repositories; select the repository folder explicitly',
    );
  if (children.length === 1) {
    const child = children[0]!;
    const result = await git(child, ['rev-parse', '--show-toplevel'], options);
    if (result.returncode !== 0) throw new Error(`Cannot inspect Project repository: ${child}`);
    return realpath(result.stdout.trim());
  }
  return null;
}
async function registered(
  repository: string,
  destination: string,
  branch?: string | null,
  options: WorkspaceOptions = {},
): Promise<boolean> {
  const result = await git(repository, ['worktree', 'list', '--porcelain'], options);
  if (result.returncode) throw new Error('Cannot inspect registered Ticket worktrees');
  return result.stdout
    .trim()
    .split('\n\n')
    .some((block) => {
      const lines = block.split('\n');
      return (
        lines.includes(`worktree ${destination}`) &&
        (branch == null || lines.includes(`branch refs/heads/${branch}`))
      );
    });
}
async function validate(
  repository: string,
  destination: string,
  branch?: string | null,
  options: WorkspaceOptions = {},
): Promise<void> {
  if (
    !(await entry_stat(join(destination, '.git')))?.isFile() ||
    !(await registered(repository, destination, branch, options))
  )
    throw new Error('Ticket workspace is not its registered Git worktree');
  const expected = await git(
    repository,
    ['rev-parse', '--path-format=absolute', '--git-common-dir'],
    options,
  );
  const current = await git(
    destination,
    ['rev-parse', '--path-format=absolute', '--git-common-dir'],
    options,
  );
  const actual = await git(destination, ['rev-parse', '--show-toplevel'], options);
  if (
    expected.returncode ||
    current.returncode ||
    actual.returncode ||
    expected.stdout !== current.stdout ||
    (await realpath(actual.stdout.trim())) !== destination
  )
    throw new Error('Ticket workspace belongs to another repository or path');
}
async function prove_complete(destination: string, options: WorkspaceOptions): Promise<void> {
  const metadata = await git(destination, ['rev-parse', '--absolute-git-dir'], options);
  if (metadata.returncode) throw new Error('Cannot verify the interrupted Ticket Git metadata');
  if (await entry_stat(join(metadata.stdout.trim(), 'locked')))
    throw new Error('Interrupted Ticket checkout is incomplete (Git initialization lock remains)');
  const head = await git(destination, ['ls-tree', '-r', '-z', 'HEAD'], options),
    index = await git(destination, ['ls-files', '-z'], options);
  if (head.returncode || index.returncode)
    throw new Error('Cannot verify the interrupted Ticket checkout');
  const indexed = new Set(index.stdout.split('\0'));
  for (const entry of head.stdout.split('\0').filter(Boolean)) {
    if (entry.startsWith('160000 ')) continue;
    const path = entry.slice(entry.indexOf('\t') + 1);
    if (!indexed.has(path) || !(await entry_stat(join(destination, path))))
      throw new Error(
        'Interrupted Ticket checkout is incomplete; complete the checkout before retry',
      );
  }
}
async function repair_previous(
  root: string,
  repository: string,
  ticket_id: string,
  options: WorkspaceOptions,
): Promise<void> {
  const retired = join(dirname(dirname(root)), '.ticket-retired');
  const info = await entry_stat(retired);
  if (!info) return;
  if (info.isSymbolicLink()) throw new Error('Retirement directory must not be a symlink');
  let removed = false;
  for (const name of await readdir(retired)) {
    if (!name.endsWith('.json')) continue;
    const record = await json<RetirementRecord>(join(retired, name));
    if (record.source !== root || record.ticket_id !== ticket_id) continue;
    const target = record.target;
    if (dirname(target) !== retired || (await entry_stat(target))?.isSymbolicLink())
      throw new Error('Invalid recorded retirement target');
    if (!(await entry_stat(target))) continue;
    const marker_path = join(target, OWNERSHIP_FILE),
      info = await entry_stat(marker_path);
    if (!info?.isFile() || info.isSymbolicLink())
      throw new Error('Retired checkout ownership is unavailable');
    const marker = await json<WorkspaceMarker>(marker_path);
    if (marker.ticket_id !== ticket_id)
      throw new Error('Retired checkout ownership does not match');
    if (marker.worktree === undefined) continue;
    if (marker.worktree !== join(root, WORKTREE_DIRECTORY))
      throw new Error('Retired checkout ownership does not match');
    if (marker.repository !== repository) continue;
    const checkout = join(target, WORKTREE_DIRECTORY),
      checkout_info = await entry_stat(checkout);
    if (!checkout_info) {
      removed = true;
      continue;
    }
    if (checkout_info.isSymbolicLink() || !(await entry_stat(join(checkout, '.git')))?.isFile())
      throw new Error('Retired checkout is not its recorded linked worktree');
    const expected = await git(
        repository,
        ['rev-parse', '--path-format=absolute', '--git-common-dir'],
        options,
      ),
      actual = await git(
        checkout,
        ['rev-parse', '--path-format=absolute', '--git-common-dir'],
        options,
      );
    if (expected.returncode || actual.returncode || expected.stdout !== actual.stdout)
      throw new Error('Retired checkout belongs to another repository');
    const repaired = await git(repository, ['worktree', 'repair', checkout], options);
    if (repaired.returncode)
      throw new Error(`Cannot repair retired Ticket worktree: ${repaired.stderr.trim()}`);
    await validate(repository, checkout, null, options);
  }
  if (removed && (await registered(repository, join(root, WORKTREE_DIRECTORY), null, options)))
    throw new Error('Retired checkout is missing but its former path remains registered');
}
export async function prepare_workspace(
  root: string,
  repository: string | null,
  ticket_id: string,
  options: WorkspaceOptions = {},
): Promise<string> {
  root = admit(root, options);
  const identity = workspace_identity(root);
  root = await realpath(root);
  const destination = join(root, WORKTREE_DIRECTORY),
    marker_path = join(root, OWNERSHIP_FILE);
  if ((await entry_stat(destination))?.isSymbolicLink())
    throw new Error('Ticket workspace must not be a symlink');
  const marker = await json<WorkspaceMarker>(marker_path);
  if (marker.ticket_id !== ticket_id) throw new Error('Ticket workspace ownership does not match');
  const selected = await repository_root(repository, options);
  if (marker.worktree !== undefined) {
    if (marker.worktree !== destination || marker.repository !== selected)
      throw new Error('Ticket workspace repository or path changed; owner review required');
    if (!marker.preparing) {
      if (!(await entry_stat(destination))?.isDirectory())
        throw new Error('The managed Ticket workspace is missing');
      if (selected) await validate(selected, destination, null, options);
      return destination;
    }
  } else {
    if (await entry_stat(destination))
      throw new Error('Ticket worktree exists without preparation ownership evidence');
    const branch = selected
      ? `ticket/${ticket_id}-workspace-${identity.workspace_id.slice(0, 12)}`
      : null;
    let initial_commit: string | null = null;
    if (selected) {
      const existing = await git(
        selected,
        ['show-ref', '--verify', '--quiet', `refs/heads/${branch}`],
        options,
      );
      if (existing.returncode === 0)
        throw new Error('Ticket branch exists without preparation ownership evidence');
      if (existing.returncode !== 1) throw new Error('Cannot inspect the Ticket branch');
      const head = await git(selected, ['rev-parse', '--verify', 'HEAD'], options);
      if (head.returncode) throw new Error("Cannot resolve the repository's initial commit");
      initial_commit = head.stdout.trim();
    }
    Object.assign(marker, {
      repository: selected,
      branch,
      worktree: destination,
      preparing: true,
      initial_commit,
    });
    await write_json(marker_path, marker);
  }
  if (selected === null) await mkdir(destination, { recursive: true });
  else if (await entry_stat(destination)) {
    await validate(selected, destination, marker.branch, options);
    await prove_complete(destination, options);
  } else {
    await repair_previous(root, selected, ticket_id, options);
    const existing = await git(
      selected,
      ['rev-parse', '--verify', '--quiet', `refs/heads/${marker.branch}`],
      options,
    );
    let args: string[];
    if (existing.returncode === 0) {
      if (existing.stdout.trim() !== marker.initial_commit)
        throw new Error('Interrupted Ticket branch changed; owner review required');
      args = ['worktree', 'add', destination, marker.branch!];
    } else if (existing.returncode === 1)
      args = ['worktree', 'add', '-b', marker.branch!, destination, marker.initial_commit!];
    else throw new Error('Cannot inspect the interrupted Ticket branch');
    const result = await git(selected, args, options);
    if (result.returncode)
      throw new Error(`Cannot prepare Ticket worktree: ${result.stderr.trim()}`);
    await validate(selected, destination, marker.branch, options);
  }
  await mkdir(join(root, 'artifacts'), { recursive: true });
  marker.preparing = false;
  await write_json(marker_path, marker);
  return destination;
}
