import { inspect_workspace_safety } from './safety.ts';
import { mkdir, readdir, realpath, rename, rm } from 'node:fs/promises';
import { prepare_agent_home } from '../../agent_homes/agent_home.ts';
import { dirname, join, resolve } from 'node:path';
import { OWNERSHIP_FILE, WORKTREE_DIRECTORY } from '../contracts.ts';
import type {
  ProjectRepository,
  RetirementRecord,
  WorkspaceMarker,
  WorkspaceOptions,
} from '../contracts.ts';
import { with_mirror } from './mirror.ts';
import { admit, entry_stat, git, json, token, workspace_identity, write_json } from './shared.ts';

async function validate_clone(
  destination: string,
  branch: string | null | undefined,
  options: WorkspaceOptions,
): Promise<void> {
  const metadata = await entry_stat(join(destination, '.git'));
  if (!metadata?.isDirectory() || metadata.isSymbolicLink())
    throw new Error('Ticket workspace is not its independent Git checkout');
  const actual = await git(destination, ['rev-parse', '--show-toplevel'], options);
  if (actual.returncode || resolve(actual.stdout.trim()) !== destination)
    throw new Error('Ticket workspace belongs to another repository or path');
  if (branch != null) {
    const current = await git(destination, ['symbolic-ref', '--short', 'HEAD'], options);
    if (current.returncode || current.stdout.trim() !== branch)
      throw new Error('Interrupted Ticket checkout branch changed; owner review required');
  }
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
async function fetch_published_revision(
  destination: string,
  revision: string,
  options: WorkspaceOptions,
): Promise<void> {
  if (!/^[0-9a-f]{40,64}$/.test(revision)) throw new Error('Published Ticket revision is invalid.');
  // The source proved publication. Fetch its exact commit without requiring the
  // local Ticket branch to exist on origin or selecting a newer remote tip.
  const fetched = await git(destination, ['fetch', 'origin', revision], options);
  const recovered = await git(destination, ['rev-parse', 'FETCH_HEAD^{commit}'], options);
  if (fetched.returncode || recovered.returncode || recovered.stdout.trim() !== revision)
    throw new Error('The published Ticket revision is unavailable on origin.');
}

async function recover_published_checkout(
  root: string,
  destination: string,
  repository: ProjectRepository | null,
  options: WorkspaceOptions,
): Promise<void> {
  const branch = repository?.published_branch;
  const revision = repository?.published_revision;
  if (branch == null && revision == null) return;
  const marker_path = join(root, OWNERSHIP_FILE);
  const marker = await json<WorkspaceMarker>(marker_path);
  if (
    revision != null &&
    marker.recovered_published_revision === revision &&
    marker.branch === branch
  )
    return;
  if (!branch || !revision || !/^[0-9a-f]{40,64}$/.test(revision))
    throw new Error('Published Ticket checkout identity is invalid.');
  if ((await git(destination, ['check-ref-format', '--branch', branch], options)).returncode)
    throw new Error('Published Ticket branch is invalid.');
  const { reason, advertised_refs } = await inspect_workspace_safety(
    root,
    undefined,
    new Set(),
    options,
  );
  if (reason) throw new Error(`Cannot recover published work: ${reason}`);
  await fetch_published_revision(destination, revision, options);
  const local = await git(
    destination,
    ['show-ref', '--verify', '--quiet', `refs/heads/${branch}`],
    options,
  );
  if (local.returncode === 0) {
    const remote_revisions = [...new Set(advertised_refs.get(destination)?.values() ?? [])];
    if (!remote_revisions.length) throw new Error('Cannot verify the destination Ticket branch.');
    const unpublished = await git(
      destination,
      ['rev-list', '-n', '1', branch, '--not', ...remote_revisions],
      options,
    );
    if (unpublished.returncode || unpublished.stdout.trim())
      throw new Error('The destination has unpublished work on the Ticket branch.');
  }
  if ((await git(destination, ['switch', '-C', branch, revision], options)).returncode)
    throw new Error('Cannot recover the published Ticket branch.');
  marker.recovered_published_revision = revision;
  marker.branch = branch;
  await write_json(marker_path, marker);
}

export async function prepare_workspace(
  root: string,
  ticket_id: string,
  repository: ProjectRepository | null,
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
  await prepare_agent_home(root, 'worker');
  const repository_url = repository?.repository_url ?? null;
  const default_branch = repository?.default_branch ?? null;
  if (default_branch !== null && repository_url === null)
    throw new Error('Cannot prepare Ticket workspace: default_branch requires repository_url');
  if (marker.worktree !== undefined && marker.checkout_kind === 'clone') {
    if (marker.worktree !== destination)
      throw new Error('Ticket workspace path changed; owner review required');
    if (!(await entry_stat(destination)) && marker.preparing) {
      const temporary = join(root, marker.temporary ?? '');
      if (dirname(temporary) !== root || !marker.temporary?.startsWith('.clone-'))
        throw new Error('Interrupted Ticket clone ownership is invalid');
      await validate_clone(temporary, marker.branch, options);
      await prove_complete(temporary, options);
      await rename(temporary, destination);
    }
    if (!(await entry_stat(destination))?.isDirectory())
      throw new Error('The managed Ticket workspace is missing or incomplete');
    await validate_clone(destination, marker.preparing ? marker.branch : null, options);
    await recover_published_checkout(root, destination, repository, options);
    if (repository?.published_branch != null) marker.branch = repository.published_branch;
    if (repository?.published_revision != null)
      marker.recovered_published_revision = repository.published_revision;
    if (marker.preparing) await prove_complete(destination, options);
    await mkdir(join(root, 'artifacts'), { recursive: true });
    marker.preparing = false;
    await write_json(marker_path, marker);
    return destination;
  }
  const selected = marker.repository ?? null;
  if (marker.worktree !== undefined) {
    if (marker.worktree !== destination || (selected === null && repository_url !== null))
      throw new Error('Ticket workspace repository or path changed; owner review required');
    if (!marker.preparing) {
      if (!(await entry_stat(destination))?.isDirectory())
        throw new Error('The managed Ticket workspace is missing');
      if (selected) await validate(selected, destination, null, options);
      await recover_published_checkout(root, destination, repository, options);
      return destination;
    }
  } else {
    if (await entry_stat(destination))
      throw new Error('Ticket worktree exists without preparation ownership evidence');
    if (repository_url !== null) {
      const branch =
        repository?.published_branch ??
        `ticket/${ticket_id}-workspace-${identity.workspace_id.slice(0, 12)}`;
      const temporary = join(root, `.clone-${token()}`);
      let recorded = false;
      try {
        const cloned = await with_mirror(repository_url, options, (mirror) => {
          const args = ['clone', '--filter=blob:none', '--single-branch', '--no-local'];
          if (mirror !== null) args.push('--reference', mirror, '--dissociate');
          const source_branch = default_branch;
          if (source_branch !== null) args.push('--branch', source_branch);
          args.push('--', repository_url, temporary);
          return git(root, args, options);
        });
        if (cloned.returncode) {
          const detail = cloned.stderr.trim();
          const target =
            default_branch !== null ? `${repository_url} (${default_branch})` : repository_url;
          throw new Error(
            detail
              ? `Cannot clone the Project repository ${target}: ${detail}`
              : `Cannot clone the Project repository ${target}; check remote access and branch`,
          );
        }
        const revision = repository?.published_revision;
        if (revision != null) await fetch_published_revision(temporary, revision, options);
        const switched = await git(
          temporary,
          revision == null ? ['switch', '-c', branch] : ['switch', '-C', branch, revision],
          options,
        );
        if (switched.returncode) throw new Error('Cannot create the Ticket branch');
        await validate_clone(temporary, branch, options);
        Object.assign(marker, {
          repository: null,
          branch,
          worktree: destination,
          preparing: true,
          checkout_kind: 'clone',
          ...(revision == null ? {} : { recovered_published_revision: revision }),
          temporary: temporary.slice(root.length + 1),
        });
        await write_json(marker_path, marker);
        recorded = true;
        await rename(temporary, destination);
      } finally {
        if (!recorded && (await entry_stat(temporary))) await rm(temporary, { recursive: true });
      }
      await mkdir(join(root, 'artifacts'), { recursive: true });
      marker.preparing = false;
      await write_json(marker_path, marker);
      return destination;
    }
    Object.assign(marker, {
      repository: null,
      branch: null,
      worktree: destination,
      preparing: true,
      initial_commit: null,
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
