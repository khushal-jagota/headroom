import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { it, vi } from 'vitest';
import {
  createDatabaseFromSeed,
  createReadAccess,
  SEED_STATEMENT_QUERY,
} from '../../core/database/index.ts';
import { require_schema_revision } from '../../core/database/admission.ts';
import { BASELINE_SEED } from '../../core/database/schema_steps/testing/steps.ts';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { UpdateBarrier } from '../update_barrier.ts';
import { install } from './installer/install.ts';
import { restore_database_snapshot } from '../backup/restore.ts';
import { InstallationRecord } from './installer/record.ts';
import { recover_interrupted_install } from './interrupted_install.ts';
import {
  KILLED_ATTEMPT,
  KILLED_FROM as FROM,
  KILLED_TO as TO,
  killed_install_steps,
} from './installer/testing/killed_install_main.ts';

const ENTRY = fileURLToPath(new URL('./installer/testing/killed_install_main.ts', import.meta.url));

/**
 * The real installer in its own process, SIGKILLed at `point`: no abort handler, catch or
 * finally runs. Leaves an installed app, a staged app, a baseline database, a frozen barrier
 * and an attempt waiting to install that requires a snapshot, as the server hands them over.
 * The installer takes that snapshot itself after the stop, as in production.
 */
async function killed_install(point: string) {
  const root = mkdtempSync(join(tmpdir(), 'panels-killed-install-'));
  const data = join(root, 'data'),
    database = join(data, 'planner.db');
  mkdirSync(data);
  mkdirSync(join(root, 'app'));
  mkdirSync(join(root, '.app-next-' + TO));
  writeFileSync(join(root, 'app/manifest.json'), JSON.stringify({ app_sha: FROM }));
  writeFileSync(join(root, '.app-next-' + TO, 'manifest.json'), JSON.stringify({ app_sha: TO }));
  createDatabaseFromSeed(database, BASELINE_SEED, () => {}).close();
  writeFileSync(
    join(data, 'update-attempt.json'),
    JSON.stringify({
      attempt_id: KILLED_ATTEMPT,
      state: 'installing',
      from_sha: FROM,
      to_sha: TO,
      backup: null,
      snapshot_required: true,
      steps: {},
    }),
  );
  new UpdateBarrier(join(data, 'update-barrier.json')).freeze(KILLED_ATTEMPT);
  const owner = new ChildProcesses();
  try {
    const child = owner.spawn(process.execPath, [ENTRY, root, point], {
      stdio: ['ignore', 'inherit', 'inherit'],
      env: { ...process.env },
    });
    await vi.waitFor(() => assert(existsSync(join(root, 'stopped'))), {
      timeout: 10_000,
      interval: 20,
    });
    child.kill('SIGKILL');
  } finally {
    owner.seal();
    await owner.settled();
  }
  const steps = killed_install_steps(point);
  const head = steps.at(-1)!.revision;
  return {
    root,
    database,
    head,
    recover: (app_sha: string) =>
      recover_interrupted_install({
        database,
        app_root: join(root, 'app'),
        app_sha,
      }),
  };
}
const installed_sha = (root: string): unknown =>
  JSON.parse(readFileSync(join(root, 'app/manifest.json'), 'utf8')).app_sha;
const record = (root: string) =>
  JSON.parse(readFileSync(join(root, 'data/update-attempt.json'), 'utf8'));
function statements(database: string): string[] {
  const reader = createReadAccess(database);
  try {
    return reader.read((sql) =>
      sql.all<{ sql: string }>(SEED_STATEMENT_QUERY).map((row) => row.sql),
    );
  } finally {
    reader.close();
  }
}

it('a kill before the steps commit: the next start puts back the previous app and its data', async () => {
  // The new app cannot start on this database and the old app is not in place, so no
  // server would ever start. Real installer, real SIGKILL, real steps and restore.
  for (const point of ['first_step_open', 'second_step_open']) {
    const killed = await killed_install(point);
    try {
      assert.throws(() => require_schema_revision(killed.database, killed.head));
      assert.equal(installed_sha(killed.root), TO);
      // The installer's own snapshot, recorded before the swap, is what recovery restores.
      assert.equal(dirname(record(killed.root).backup), join(killed.root, 'backups'));

      assert.equal(await killed.recover(TO), 'rolled_back');
      assert.equal(installed_sha(killed.root), FROM);
      assert(existsSync(join(killed.root, '.app-next-' + TO)));
      assert(!existsSync(join(killed.root, '.app-previous-' + FROM)));
      // The old app's admission accepts it, and no committed step survives the restore.
      require_schema_revision(killed.database, BASELINE_SEED.alembicRevision);
      assert.deepEqual(statements(killed.database), BASELINE_SEED.statements);
      assert.equal(record(killed.root).state, 'failed');
      assert.match(record(killed.root).detail, /the previous version and its data/);
      assert(!existsSync(join(killed.root, 'data/update-barrier.json')));
      // The previous app's own start leaves it alone.
      assert.equal(await killed.recover(FROM), 'none');
    } finally {
      rmSync(killed.root, { recursive: true, force: true });
    }
  }
}, 30_000);

it('a kill after migration but before readiness restores the previous app and data', async () => {
  const killed = await killed_install('steps_committed');
  try {
    assert.equal(record(killed.root).steps.service_started, undefined);
    assert.equal(await killed.recover(TO), 'rolled_back');
    assert.equal(installed_sha(killed.root), FROM);
    require_schema_revision(killed.database, BASELINE_SEED.alembicRevision);
    assert.equal(record(killed.root).state, 'failed');
    assert(!existsSync(join(killed.root, 'data/update-barrier.json')));
  } finally {
    rmSync(killed.root, { recursive: true, force: true });
  }
}, 30_000);

it('a kill before the swap changes nothing, during or after the snapshot too', async () => {
  // The previous app stays in place on its untouched database, and recovery leaves it.
  for (const point of ['before_swap', 'snapshot_unrecorded', 'snapshot_recorded']) {
    const killed = await killed_install(point);
    const digest = () => createHash('sha256').update(readFileSync(killed.database)).digest('hex');
    try {
      assert.equal(record(killed.root).steps.app_swapped, undefined);
      assert.equal(
        typeof record(killed.root).backup,
        point === 'snapshot_recorded' ? 'string' : 'object',
      );
      const before = digest();
      for (const sha of [FROM, TO]) assert.equal(await killed.recover(sha), 'none');
      assert.equal(installed_sha(killed.root), FROM);
      assert(existsSync(join(killed.root, '.app-next-' + TO)));
      assert.equal(digest(), before);
      assert.equal(record(killed.root).state, 'failed');
      assert(!existsSync(join(killed.root, 'data/update-barrier.json')));
      require_schema_revision(killed.database, BASELINE_SEED.alembicRevision);
    } finally {
      rmSync(killed.root, { recursive: true, force: true });
    }
  }
}, 30_000);

it('the independent coordinator recovers a stopped installation after restart even when the candidate cannot boot', async () => {
  const killed = await killed_install('steps_committed');
  const data = join(killed.root, 'data');
  const request = {
    current_root: killed.root,
    data_directory: data,
    database: killed.database,
    attempt_id: KILLED_ATTEMPT,
    backup_directory: join(killed.root, 'backups'),
    node_executable: process.execPath,
    service_unit: 'test.service',
    health_url: 'http://127.0.0.1:0',
    health_timeout_seconds: 1,
  };
  try {
    new InstallationRecord(request).record('coordinator_registered');
    assert.equal(await killed.recover(TO), 'none');
    const events: string[] = [];
    const code = await install(
      request,
      {
        async service(action) {
          events.push(action);
        },
        async restore(snapshot) {
          events.push('restore');
          await restore_database_snapshot(snapshot, killed.database, { live_stopped: true });
        },
        async healthy(sha) {
          events.push('verify-previous');
          return sha === FROM && installed_sha(killed.root) === FROM;
        },
        async capture() {
          return assert.fail('unexpected capture');
        },
        async snapshot() {
          assert.fail('unexpected snapshot');
        },
        async schema_steps() {
          assert.fail('unexpected migration');
        },
      },
      { stderr: () => {} },
      new AbortController().signal,
    );
    assert.equal(code, 1);
    assert.deepEqual(events, ['stop', 'restore', 'start', 'verify-previous']);
    assert.equal(record(killed.root).state, 'failed');
    assert(!existsSync(join(data, 'update-barrier.json')));
    require_schema_revision(killed.database, BASELINE_SEED.alembicRevision);
  } finally {
    rmSync(killed.root, { recursive: true, force: true });
  }
}, 30000);
