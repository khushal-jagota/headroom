import { mkdtemp, mkdir, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { WorkerScratch, snapshot_ticket_scratch, prune_ticket_scratch } from './worker_scratch.ts';
import { private_ticket_path, resolveFile } from '../files/logic/paths.ts';
import { inventory } from '../files/catalog/inventory.ts';
const roots: string[] = [];
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'worker-scratch-'));
  roots.push(root);
  return root;
}
afterEach(async () => {
  await Promise.all(roots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});
test('retirement snapshot removes only captured runs and refuses symlink or foreign names', async () => {
  const home = await fixture();
  await mkdir(join(home, '.pw'), { mode: 0o700 });
  const old = join(home, '.pw/t_test-000001');
  await mkdir(old);
  const names = await snapshot_ticket_scratch('t_test', home);
  await mkdir(join(home, '.pw/t_test-000002'));
  expect(await prune_ticket_scratch('t_test', ['../outside'], home)).toBe(false);
  expect(await prune_ticket_scratch('t_test', names, home)).toBe(true);
  expect(await readdir(join(home, '.pw'))).toEqual(['t_test-000002']);
  await symlink(home, old);
  expect(await prune_ticket_scratch('t_test', names, home)).toBe(false);
});
test('Worker run owns private disk scratch, prunes old run, sets all temporary variables and closes', async () => {
  const home = await fixture();
  const old = await WorkerScratch.create({ PLAN_TICKET_ID: 't_test' }, { home });
  const fresh = await WorkerScratch.create({ PLAN_TICKET_ID: 't_test' }, { home });
  expect(fresh.directory).not.toBe(old.directory);
  expect(fresh.environment.TMPDIR).toBe(fresh.directory);
  expect(fresh.environment.TMP).toBe(fresh.directory);
  expect(fresh.environment.TEMP).toBe(fresh.directory);
  expect(await readdir(join(home, '.pw'))).toHaveLength(1);
  await fresh.close();
  expect(await readdir(join(home, '.pw'))).toEqual([]);
  await symlink(home, join(home, '.pw/t_test-abcdef'));
  await expect(WorkerScratch.create({ PLAN_TICKET_ID: '../escape' }, { home })).rejects.toThrow();
});
test('public Ticket files and catalogs hide private checkouts, markers and aliases but keep artifacts', async () => {
  const root = await fixture(),
    owner = join(root, 'files/tickets/t_test');
  await mkdir(join(owner, 'worktree'), { recursive: true });
  await writeFile(join(owner, 'worktree/secret.txt'), 'private');
  await writeFile(join(owner, '.panels-workspace.json'), '{}');
  await writeFile(join(owner, '.panels-workspace.json.' + 'a'.repeat(32) + '.tmp'), '{}');
  await writeFile(join(owner, 'report.html'), 'public');
  await symlink(join(owner, 'worktree'), join(owner, 'alias'));
  expect(private_ticket_path(owner, 'alias/missing')).toBe(true);
  expect(private_ticket_path(owner, 'future.html')).toBe(false);
  expect(() =>
    resolveFile(join(root, 'db.sqlite'), 'ticket', 't_test', 'alias/secret.txt'),
  ).toThrow();
  expect(() =>
    resolveFile(join(root, 'db.sqlite'), 'ticket', 't_test', '.panels-workspace.json'),
  ).toThrow();
  expect(resolveFile(join(root, 'db.sqlite'), 'ticket', 't_test', 'report.html')).toBe(
    join(owner, 'report.html'),
  );
  const catalog = await inventory(owner, '/files/tickets/t_test/');
  expect(catalog.directories['']!.map((row) => row.path)).toEqual(['report.html']);
});
