import { expect, test, vi, afterEach } from 'vitest';
import { mkdirSync, existsSync, realpathSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fixture, fixture_path, create } from '../tests/ticket_fixture.ts';
import { path_is_within } from '../core/path_containment.ts';
import { filesRoot } from '../files/logic/paths.ts';
import { workspace_identity } from './filesystem.ts';
import { clean_for_database } from './cleanup.ts';
import { register_workspace } from './data.ts';
import { RETENTION_SECONDS } from './contracts.ts';
const injection = vi.hoisted(() => ({ fail_commit: false }));
vi.mock('../core/database/index.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../core/database/index.ts')>();
  return {
    ...actual,
    createDatabase: (path: string, options: Parameters<typeof actual.createDatabase>[1] = {}) =>
      actual.createDatabase(path, {
        ...options,
        commitSteps: [
          ...(options.commitSteps ?? []),
          () => {
            if (injection.fail_commit) throw new Error('synthetic commit failure');
          },
        ],
      }),
  };
});
afterEach(() => {
  injection.fail_commit = false;
});
function setup() {
  const db = fixture(),
    path = fixture_path(db),
    root = realpathSync(dirname(path)),
    ticket = create(db);
  // The existing fixture owns a mkdtemp root. Refuse any path outside that physical root.
  function admit(target: string): string {
    target = resolve(target);
    if (!path_is_within(root, target)) throw new Error('outside synthetic fixture');
    let existing = target;
    while (!existsSync(existing)) existing = dirname(existing);
    if (!path_is_within(root, realpathSync(existing))) throw new Error('synthetic symlink escape');
    return target;
  }
  const folder = admit(join(filesRoot(path, 'ticket'), ticket.id));
  mkdirSync(folder, { recursive: true });
  const identity = workspace_identity(admit(folder));
  db.write((sql) =>
    sql.run('UPDATE ticket_workspace_lifecycle SET ended_at=0 WHERE ticket_id=?', [ticket.id]),
  );
  return {
    db,
    path,
    ticket,
    folder,
    identity,
    options: {
      admit_path: admit,
      snapshot_ticket_scratch: () => [],
      prune_ticket_scratch: () => true,
    },
  };
}
test('durable child uncertainty fences deletion until positive retirement', async () => {
  const { db, path, ticket, folder, identity, options } = setup();
  db.write((sql) => {
    sql.run(
      "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) VALUES('synthetic-child','codex',?,'full',0)",
      [folder],
    );
    register_workspace(sql, ticket.id, 'synthetic-child', identity);
    sql.run(
      "INSERT INTO conversation_child_custody(conversation_id,generation,state) VALUES('synthetic-child','synthetic-generation','started')",
    );
  });
  const held = await clean_for_database(path, RETENTION_SECONDS, options);
  expect(held.skipped).toContainEqual({
    path: folder,
    reason: 'Local child retirement is not proven',
  });
  expect(existsSync(folder)).toBe(true);
  expect(
    db.read((sql) =>
      sql.get('SELECT 1 FROM ticket_workspace_retirements WHERE ticket_id=?', [ticket.id]),
    ),
  ).toBeUndefined();
  db.write((sql) =>
    sql.run(
      "UPDATE conversation_child_custody SET state='retired' WHERE conversation_id='synthetic-child'",
    ),
  );
  const removed = await clean_for_database(path, RETENTION_SECONDS, options);
  expect(removed.removed).toContain(folder);
  expect(existsSync(folder)).toBe(false);
});
test('a failed database commit restores the detached physical folder and rolls back its grant', async () => {
  const { db, path, ticket, folder, identity, options } = setup();
  injection.fail_commit = true;
  const result = await clean_for_database(path, RETENTION_SECONDS, options);
  expect(result.removed).toEqual([]);
  expect(result.skipped).toContainEqual({
    path: folder,
    reason: 'cleanup failed: Error: synthetic commit failure',
  });
  expect(existsSync(folder)).toBe(true);
  expect(
    db.read((sql) =>
      sql.get('SELECT 1 FROM ticket_workspace_retirements WHERE ticket_id=?', [ticket.id]),
    ),
  ).toBeUndefined();
  expect(workspace_identity(options.admit_path(folder))).toEqual(identity);
});
