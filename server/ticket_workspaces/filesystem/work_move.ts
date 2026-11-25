/** Check the source in place. This never changes or removes its work. */
import { join } from 'node:path';
import type { WorkspaceOptions } from '../contracts.ts';
import type { WorkMoveProof } from '../../work_moves/contracts.ts';
import { inspect_workspace_safety } from './safety.ts';
import { admit, entry_stat, git } from './shared.ts';

export async function inspect_work_move(
  root: string,
  ticket: boolean,
  options: WorkspaceOptions = {},
): Promise<WorkMoveProof> {
  root = admit(root, options);
  const { reason, advertised_refs } = await inspect_workspace_safety(
    root,
    undefined,
    new Set(),
    options,
  );
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
  // Recheck the captured HEAD against the same advertisement. The local branch
  // can advance after the safety check, but it need not share a remote ref name.
  const remote_revisions = [...new Set(advertised_refs.get(checkout)?.values() ?? [])];
  if (!remote_revisions.length) throw new Error('Cannot verify the published Ticket revision.');
  const unpublished = await git(
    checkout,
    ['rev-list', '-n', '1', published_revision, '--not', ...remote_revisions],
    options,
  );
  if (unpublished.returncode || unpublished.stdout.trim())
    throw new Error('Push the current Ticket branch before the move.');
  return { published_branch, published_revision };
}
