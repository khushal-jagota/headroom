/** Check the source in place. This never changes or removes its work. */
import { join } from 'node:path';
import type { WorkspaceOptions } from '../contracts.ts';
import type { WorkMoveProof } from '../../work_moves/contracts.ts';
import { reason_to_keep } from './safety.ts';
import { entry_stat, git } from './shared.ts';

export async function inspect_work_move(
  root: string,
  ticket: boolean,
  options: WorkspaceOptions = {},
): Promise<WorkMoveProof> {
  const reason = await reason_to_keep(root, undefined, new Set(), options);
  if (reason !== null) throw new Error(reason);
  const checkout = join(root, 'worktree');
  if (!ticket || !(await entry_stat(checkout)))
    return { published_branch: null, published_revision: null };
  // A non-repository Ticket has an ordinary empty worktree folder.
  if (!(await entry_stat(join(checkout, '.git'))))
    return { published_branch: null, published_revision: null };
  const branch = await git(checkout, ['symbolic-ref', '--short', 'HEAD'], options);
  const revision = await git(checkout, ['rev-parse', 'HEAD'], options);
  if (branch.returncode || revision.returncode)
    throw new Error('Cannot identify the published Ticket branch.');
  const published_branch = branch.stdout.trim();
  const published_revision = revision.stdout.trim();
  const remote = await git(
    checkout,
    ['ls-remote', '--heads', 'origin', `refs/heads/${published_branch}`],
    options,
  );
  if (remote.returncode || remote.stdout.trim().split(/\s+/)[0] !== published_revision)
    throw new Error('Push the current Ticket branch before the move.');
  return { published_branch, published_revision };
}
