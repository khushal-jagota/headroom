import { mkdtemp, mkdir, readdir, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { WorkerScratch, snapshot_ticket_scratch, prune_ticket_scratch } from './worker_scratch.ts';
import { private_ticket_path, resolveFile } from '../files/logic/paths.ts';
import { fixture as ticket_fixture, fixture_path, create } from '../tests/ticket_fixture.ts';
import { ticket_detail } from '../tickets/views.ts';
import { linked } from '../files/catalog/read.ts';
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
  const db = ticket_fixture(),
    ticket = create(db),
    database = fixture_path(db);
  const root = dirname(database),
    owner = join(root, 'files/tickets', ticket.id);
  await mkdir(join(owner, 'worktree/src'), { recursive: true });
  await mkdir(join(owner, 'artifacts/worktree'), { recursive: true });
  await writeFile(join(owner, 'artifacts/worktree/public.txt'), 'public nested name');
  await writeFile(join(owner, 'worktree/file.txt'), 'private');
  await symlink('../worktree/src', join(owner, 'artifacts/s'));
  await symlink('s/..', join(owner, 'artifacts/x'));
  await writeFile(join(owner, 'worktree/secret.txt'), 'private');
  await writeFile(join(owner, '.panels-workspace.json'), '{}');
  await writeFile(join(owner, '.panels-workspace.json.' + 'a'.repeat(32) + '.tmp'), '{}');
  await writeFile(join(owner, 'report.html'), 'public');
  await symlink(join(owner, 'worktree'), join(owner, 'alias'));
  expect(private_ticket_path(owner, 'alias/missing')).toBe(true);
  expect(private_ticket_path(owner, 'future.html')).toBe(false);
  expect(private_ticket_path(owner, 'artifacts/x/file.txt')).toBe(true);
  expect(private_ticket_path(owner, 'artifacts/x/missing.txt')).toBe(true);
  expect(() => resolveFile(database, 'ticket', ticket.id, 'artifacts/x/file.txt')).toThrow();
  expect(() => resolveFile(database, 'ticket', ticket.id, 'alias/secret.txt')).toThrow();
  expect(() => resolveFile(database, 'ticket', ticket.id, '.panels-workspace.json')).toThrow();
  expect(resolveFile(database, 'ticket', ticket.id, 'report.html')).toBe(
    join(owner, 'report.html'),
  );
  const catalog = await inventory(owner, `/files/tickets/${ticket.id}/`);
  expect(catalog.directories['']!.map((row) => row.path).sort()).toEqual([
    'artifacts',
    'report.html',
  ]);
  const detail = db.read((sql) => ticket_detail(sql, ticket.id, 1, database));
  expect((detail.artifacts as { name: string }[]).map((row) => row.name).sort()).toEqual([
    'artifacts',
    'report.html',
  ]);
  expect(catalog.directories['artifacts']!.map((row) => row.path)).toEqual(['artifacts/worktree']);
  // Existing indexed rows predate the private policy and must be filtered at read time.
  const paths = [
    'worktree/file.txt',
    'artifacts/x/file.txt',
    'artifacts/x/missing.txt',
    'future.html',
  ];
  db.write((sql) =>
    paths.forEach((path, index) => {
      const target = `/files/tickets/${ticket.id}/${path}`;
      sql.run('INSERT INTO ticket_artifact_links VALUES (?,?,?,?,?,?,?,?,?)', [
        ticket.id,
        'brief',
        index,
        target,
        target,
        null,
        'artifact' + index,
        '',
        'html',
      ]);
    }),
  );
  const links = db.read((sql) =>
    linked(sql, ticket.id, 'http://planner.local', new Set(), 0, 20, join(root, 'files/tickets')),
  );
  expect(links.total).toBe(1);
  expect(links.entries.map((row) => row.path)).toEqual(['future.html']);
});
