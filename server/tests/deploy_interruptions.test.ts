/** These risks are not reached by the core's no-participant recovery tests. */
import { afterEach, expect, test } from 'vitest';
import { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setup } from './conversation_core_fixture.ts';
import { create, read } from './ticket_fixture.ts';
import {
  reconcile,
  bind_delivery,
  settle_delivery,
  select_recoveries,
  admit_recovery,
} from '../conversation/deploy_interruptions/data.ts';
import { interruption_identity } from '../conversation/deploy_interruptions/identity.ts';
import {
  manifest_from_dict,
  read_manifests,
  write_manifest,
  manifest_root,
} from '../conversation/deploy_interruptions/files.ts';
import type { CaptureManifest } from '../conversation/deploy_interruptions/contracts.ts';
const directories: string[] = [];
afterEach(() => {
  for (const directory of directories.splice(0))
    rmSync(directory, { recursive: true, force: true });
});
function manifest(overrides: Partial<CaptureManifest> = {}): CaptureManifest {
  return {
    version: 1,
    deployment_id: 'deploy',
    capture_id: 'capture',
    captured_at: 99,
    state: 'captured',
    detail: null,
    turns: [
      {
        conversation_id: 'core',
        turn_number: 1,
        occurrence_id: null,
        runtime_epoch: 'test-runtime',
        original_status: 'running',
        display_name: 'Work',
        owner_kind: 'unknown',
        owner_id: null,
        claim_revision: null,
        claim: null,
      },
    ],
    ...overrides,
  };
}
test('startup imports legacy epoch evidence before rotation and never reimports the turn', async () => {
  const f = setup();
  try {
    await f.start();
    f.database.write((tx) =>
      tx.run(
        "INSERT INTO conversation_lifecycle_facts VALUES ('core','turn','1',1,'turn','running',NULL)",
      ),
    );
    const capture = manifest();
    f.database.write((tx) => reconcile(tx, [capture], 100));
    expect(
      f.database.read((sql) =>
        sql.get('SELECT exact_match,recovery_state FROM deploy_interruptions'),
      ),
    ).toEqual({ exact_match: 1, recovery_state: 'awaiting_action' });
    expect(
      f.database.read(
        (sql) => sql.get<{ epoch: string }>('SELECT epoch FROM conversation_runtime_epoch')!.epoch,
      ),
    ).not.toBe('test-runtime');
    f.database.write((tx) => reconcile(tx, [capture], 101));
    expect(
      f.database.read((sql) => sql.all('SELECT interruption_id FROM deploy_interruptions')),
    ).toHaveLength(1);
    const references = f.database.read(select_recoveries).get('core')!;
    expect(references).toEqual([
      { interruption_id: interruption_identity(capture, capture.turns[0]), expected_stage: null },
    ]);
    const notice = f.database.write((tx) =>
      bind_delivery(
        tx,
        'core',
        2,
        references,
        new Map([[references[0].interruption_id, 'recovery']]),
      ),
    );
    expect(notice).toContain('A deployment ended this turn.');
    f.database.write((tx) =>
      settle_delivery(
        tx,
        'core',
        [
          {
            kind: 'attempt',
            identity: 'prompt:2',
            turn_number: 2,
            operation: 'prompt',
            status: 'suppressed',
            provider_identity: null,
          },
        ],
        { now: 102 },
      ),
    );
    expect(
      f.database.read((sql) => sql.all('SELECT * FROM deploy_interruption_deliveries')),
    ).toEqual([]);
    expect(f.database.read(select_recoveries).size).toBe(1);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
test('recovery restores only the captured claim and refused delivery errors that restored revision', async () => {
  const f = setup();
  try {
    await f.start();
    const ticket = create(f.database);
    f.database.write((tx) => {
      tx.run(
        "UPDATE tickets SET conversation_id='core',worker_step_claim='out',worker_step_claim_revision=7 WHERE id=?",
        [ticket.id],
      );
      tx.run(
        "INSERT INTO conversation_lifecycle_facts VALUES ('core','turn','1',1,'turn','running',NULL)",
      );
    });
    const capture = manifest();
    const owned: CaptureManifest = {
      ...capture,
      turns: [
        {
          ...capture.turns[0],
          owner_kind: 'ticket',
          owner_id: ticket.id,
          claim_revision: 7,
          claim: 'out',
        },
      ],
    };
    f.database.write((tx) => reconcile(tx, [owned], 100));
    expect(read(f.database, ticket.id).worker_step_claim_revision).toBe(8);
    const references = f.database.read(select_recoveries).get('core')!;
    expect(references).toHaveLength(1);
    f.database.write((tx) => {
      expect(admit_recovery(tx, 'core', references, 101)).toBeNull();
      expect(readClaim(tx)).toBe('errored');
      bind_delivery(tx, 'core', 2, references);
      expect(admit_recovery(tx, 'core', references, 101)).toBeNull();
    });
    expect(read(f.database, ticket.id).worker_step_claim_revision).toBe(9);
    f.database.write((tx) =>
      settle_delivery(
        tx,
        'core',
        [
          {
            kind: 'attempt',
            identity: 'prompt:2',
            turn_number: 2,
            operation: 'prompt',
            status: 'refused',
            provider_identity: null,
          },
        ],
        { now: 102 },
      ),
    );
    expect(read(f.database, ticket.id).worker_step_claim).toBe('errored');
    expect(read(f.database, ticket.id).worker_step_claim_revision).toBe(10);
    expect(
      f.database.read((sql) => sql.get('SELECT recovery_state FROM deploy_interruptions')),
    ).toEqual({ recovery_state: 'unable_to_resume' });
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
  function readClaim(sql: import('../core/database/contracts.ts').ReadSql) {
    return sql.get<{ worker_step_claim: string }>('SELECT worker_step_claim FROM tickets')!
      .worker_step_claim;
  }
});
test('lost wire intent becomes unknown at restart and cannot automatically resume', async () => {
  const f = setup();
  try {
    await f.start();
    f.database.write((tx) =>
      tx.run(
        "INSERT INTO conversation_lifecycle_facts VALUES ('core','turn','1',1,'turn','running',NULL)",
      ),
    );
    f.database.write((tx) => reconcile(tx, [manifest()], 100));
    f.database.write((tx) => bind_delivery(tx, 'core', 2));
    f.database.write((tx) => reconcile(tx, [], 101));
    expect(
      f.database.read((sql) => sql.get('SELECT status FROM deploy_interruption_deliveries')),
    ).toEqual({ status: 'unknown' });
    expect(f.database.read(select_recoveries).size).toBe(0);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
test('invalid and symlink capture evidence stays unavailable rather than zero interrupted turns', () => {
  const directory = mkdtempSync(join(tmpdir(), 'capture-evidence-'));
  directories.push(directory);
  const db = join(directory, 'test.sqlite');
  write_manifest(db, manifest());
  const folder = join(manifest_root(db), 'deploy');
  writeFileSync(join(folder, 'bad.json'), '{');
  const external = join(directory, 'external.json');
  writeFileSync(external, JSON.stringify(manifest({ capture_id: 'linked' })));
  symlinkSync(external, join(folder, 'linked.json'));
  mkdirSync(join(folder, 'directory.json'));
  expect(read_manifests(db).map((row) => [row.capture_id, row.state])).toEqual([
    ['bad', 'unavailable'],
    ['capture', 'captured'],
    ['directory', 'unavailable'],
    ['linked', 'unavailable'],
  ]);
  expect(() =>
    manifest_from_dict({ ...manifest(), turns: [{ ...manifest().turns[0], turn_number: true }] }),
  ).toThrow('invalid capture integer');
  expect(() => manifest_from_dict({ ...manifest(), state: 'unavailable' })).toThrow(
    'invalid captured turns',
  );
});

test('restart reports only the task IDs captured before this process starts work', async () => {
  const f = setup();
  await f.start();
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  f.database.write((tx) =>
    tx.run("INSERT INTO conversation_background_tasks VALUES ('core','old','Old task')"),
  );
  const core = new (await import('../conversation/system.ts')).SqliteProcessConversationSystem(
    f.core.options,
  );
  try {
    f.database.write((tx) =>
      tx.run("INSERT INTO conversation_background_tasks VALUES ('core','new','New task')"),
    );
    await core.tell_agents_of_background_tasks_lost_at_restart();
    await Promise.all([...core.conversations.get('core')!.settlement_tasks]);
    expect(
      f.database.read((sql) =>
        sql.all('SELECT task_id FROM conversation_background_tasks ORDER BY task_id'),
      ),
    ).toEqual([{ task_id: 'new' }]);
    const first = f.store.read_events_after('core', 0).filter((event) => event.kind === 'prompt');
    expect(JSON.stringify(first)).toContain('Old task');
    expect(JSON.stringify(first)).not.toContain('New task');
    await core.tell_agents_of_background_tasks_lost_at_restart();
    expect(
      f.store.read_events_after('core', 0).filter((event) => event.kind === 'prompt'),
    ).toHaveLength(1);
  } finally {
    await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});
