import { afterEach, beforeEach, expect, test } from 'vitest';
import {
  mkdtempSync,
  mkdirSync,
  realpathSync,
  existsSync,
  writeFileSync,
  readFileSync,
  symlinkSync,
  rmSync,
  renameSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { path_is_within } from '../core/path_containment.ts';
import {
  cleanup_local_workspaces,
  prepare_workspace_retirement,
  read_workspace_identity,
  reason_to_keep,
  workspace_identity,
  prepare_workspace,
} from './filesystem.ts';
import { git } from './filesystem/shared.ts';
import { RETENTION_SECONDS } from './contracts.ts';
import type { WorkspaceOptions } from './contracts.ts';

let root: string;
const ticket = 't_01234567';
// Every product entry and Git call is admitted against this test's fresh physical root.
function admit(path: string): string {
  path = resolve(path);
  if (!path_is_within(root, path)) throw new Error('outside synthetic root');
  let existing = path;
  while (!existsSync(existing)) existing = dirname(existing);
  if (!path_is_within(root, realpathSync(existing))) throw new Error('synthetic symlink escape');
  return path;
}
const options: WorkspaceOptions = {
  admit_path: admit,
  snapshot_ticket_scratch: () => [],
  prune_ticket_scratch: () => true,
};
beforeEach(() => {
  root = realpathSync(mkdtempSync(join(tmpdir(), 'panels-workspaces-proof-')));
});
afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});
function folder(name = ticket): string {
  const path = admit(join(root, 'files', 'tickets', name));
  mkdirSync(path, { recursive: true });
  return path;
}
async function command(path: string, args: string[]): Promise<string> {
  const result = await git(admit(path), args, options);
  expect(result.stderr, args.join(' ')).toBeDefined();
  expect(result.returncode, result.stderr).toBe(0);
  return result.stdout.trim();
}

test('the synthetic guard rejects outside paths and symlink escapes before product dispatch', () => {
  expect(() => admit('/home/vps/Deployments/Panels')).toThrow('outside synthetic root');
  expect(() => admit('/home/vps/Deployments/Panels/current/data/files/tickets/' + ticket)).toThrow(
    'outside synthetic root',
  );
  symlinkSync(dirname(root), join(root, 'escape'), 'dir');
  expect(() => admit(join(root, 'escape', 'missing'))).toThrow('synthetic symlink escape');
});

test('identity rejects replacement during retirement and preparation resumes the same artifact-only folder', async () => {
  const path = folder(),
    identity = workspace_identity(admit(path));
  expect(await prepare_workspace(path, null, ticket, options)).toBe(join(path, 'worktree'));
  expect(await prepare_workspace(path, null, ticket, options)).toBe(join(path, 'worktree'));
  renameSync(path, join(root, 'older'));
  mkdirSync(path);
  const replacement = workspace_identity(admit(path));
  expect(replacement.workspace_id).not.toBe(identity.workspace_id);
  expect(
    await prepare_workspace_retirement(
      path,
      { ticket_id: ticket, ended_at: 0, deleted: false },
      { ...options, identity },
    ),
  ).toEqual({ reason: 'physical workspace identity changed' });
  expect(read_workspace_identity(admit(path))).toEqual(replacement);
});

test('inventory precedes detach and scratch deletion uses only the recorded snapshot', async () => {
  const path = folder();
  writeFileSync(join(path, 'artifact.txt'), 'saved');
  workspace_identity(admit(path));
  const states = new Map([[ticket, { ticket_id: ticket, ended_at: 0, deleted: false }]]);
  let before = false,
    snapshot = false,
    pruned: readonly string[] = [];
  const result = await cleanup_local_workspaces(admit(dirname(path)), states, RETENTION_SECONDS, {
    ...options,
    before_remove: () => {
      const manifest = JSON.parse(
        readFileSync(admit(join(root, 'files', 'ticket-workspaces-cleanup.json')), 'utf8'),
      );
      expect(manifest.folders[0].preservation_reason).toBeNull();
      expect(existsSync(path)).toBe(true);
      before = true;
      return null;
    },
    snapshot_ticket_scratch: () => {
      expect(before).toBe(true);
      snapshot = true;
      return [`${ticket}-123abc`];
    },
    prune_ticket_scratch: (id, names) => {
      expect(snapshot).toBe(true);
      expect(id).toBe(ticket);
      pruned = names;
      return true;
    },
  });
  expect(result).toEqual({ removed: [path], skipped: [] });
  expect(pruned).toEqual([`${ticket}-123abc`]);
  expect(existsSync(path)).toBe(false);
});

test('remote advertisements, rather than stale tracking refs, prove the last remote copy', async () => {
  const remote = admit(join(root, 'remote.git'));
  mkdirSync(remote);
  await command(remote, ['init', '--bare']);
  const path = folder(),
    checkout = admit(join(path, 'repository'));
  mkdirSync(checkout);
  await command(checkout, ['init']);
  await command(checkout, ['config', 'user.email', 'proof@example.test']);
  await command(checkout, ['config', 'user.name', 'Synthetic proof']);
  writeFileSync(join(checkout, 'tracked.txt'), 'published');
  await command(checkout, ['add', '.']);
  await command(checkout, ['commit', '-m', 'published']);
  await command(checkout, ['remote', 'add', 'origin', remote]);
  await command(checkout, ['push', 'origin', 'HEAD:refs/heads/published']);
  expect(await reason_to_keep(path, undefined, undefined, options)).toBeNull();
  await command(remote, ['update-ref', '-d', 'refs/heads/published']);
  expect(await reason_to_keep(path, undefined, undefined, options)).toBe(
    `no remote copy can be verified for ${checkout}`,
  );
  // The folder and its commit survive the failed proof.
  expect(readFileSync(join(checkout, 'tracked.txt'), 'utf8')).toBe('published');
});
