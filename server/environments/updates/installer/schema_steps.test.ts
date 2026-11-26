import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import BetterSqlite3 from 'better-sqlite3';
import {
  closeSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { it } from 'vitest';
import {
  applySchemaSteps,
  createDatabase,
  createDatabaseFromSeed,
  createReadAccess,
  SEED_STATEMENT_QUERY,
} from '../../../core/database/index.ts';
import { application_schema_seed } from '../../../core/database/admission.ts';
import {
  BASELINE_SEED,
  TEST_SCHEMA_STEPS,
  rebuild_step,
} from '../../../core/database/schema_steps/testing/steps.ts';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { register_test_root } from '../../../core/test_paths.ts';
import { create_database_backup, stage_database_copy } from '../../backup/capture.ts';
import { restore_database_snapshot } from '../../backup/restore.ts';
import { create_installer_effects } from './commands.ts';
import { install } from './install.ts';
import { InstallationRecord } from './record.ts';
import type { InstallerEffects, InstallerRequest } from './contracts.ts';

const FROM = 'a'.repeat(40),
  TO = 'b'.repeat(40);

/** The safety tests' layout: an installed app, a staged app, and an attempt waiting to install. */
function installation(prefix: string, backup: string | null) {
  const root = mkdtempSync(join(tmpdir(), prefix));
  const data = join(root, 'data');
  mkdirSync(data);
  mkdirSync(join(root, 'app'));
  mkdirSync(join(root, '.app-next-' + TO));
  writeFileSync(join(root, 'app/manifest.json'), JSON.stringify({ app_sha: FROM }));
  writeFileSync(join(root, '.app-next-' + TO, 'manifest.json'), JSON.stringify({ app_sha: TO }));
  const request: InstallerRequest = {
    current_root: root,
    database: join(data, 'planner.db'),
    data_directory: data,
    backup_directory: join(root, 'backups'),
    attempt_id: 'schema-steps',
    node_executable: process.execPath,
    service_unit: 'never-start.service',
    health_url: 'http://127.0.0.1:0',
    health_timeout_seconds: 1,
  };
  const attempt = (snapshot: string | null, snapshot_required = false) =>
    writeFileSync(
      join(data, 'update-attempt.json'),
      JSON.stringify({
        attempt_id: 'schema-steps',
        state: 'installing',
        from_sha: FROM,
        to_sha: TO,
        backup: snapshot,
        snapshot_required,
        steps: {},
      }),
    );
  attempt(backup);
  return { root, request, attempt };
}
function installed_sha(root: string): unknown {
  return JSON.parse(readFileSync(join(root, 'app/manifest.json'), 'utf8')).app_sha;
}
function record(root: string) {
  return JSON.parse(readFileSync(join(root, 'data/update-attempt.json'), 'utf8'));
}

it('steps run after the swap and before start', async () => {
  // The wrong order would step a database under a running app. Scripted effects only.
  for (const backup of [null, '/scripted/snapshot']) {
    const { root, request } = installation('panels-schema-step-order-', backup);
    const events: unknown[] = [];
    try {
      const code = await install(
        request,
        {
          async service(action) {
            events.push(action);
          },
          async capture() {
            events.push('capture');
            return { kind: 'returned', returncode: 0 };
          },
          async snapshot() {
            assert.fail('this attempt requires no snapshot');
          },
          async schema_steps(descriptor, backup_taken) {
            events.push(['schema_steps', installed_sha(root), typeof descriptor, backup_taken]);
          },
          async verify_snapshot() {
            throw new Error('this attempt staged no copy');
          },
          async restore() {
            assert.fail('a healthy install restores nothing');
          },
          async healthy(sha) {
            events.push(['healthy', sha]);
            return true;
          },
        },
        { stderr() {} },
        new AbortController().signal,
      );
      assert.equal(code, 0);
      assert.deepEqual(events, [
        'stop',
        'capture',
        ['schema_steps', TO, 'number', backup !== null],
        'start',
        ['healthy', TO],
      ]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  }
});

it('a staged copy is caught up in the window and fully checked only after health', async () => {
  // The late check must not delay the start or turn a healthy update into a failure.
  const { root, request } = installation('panels-schema-step-staged-', null);
  const staged = join(request.backup_directory, '.backup-staged-scripted');
  const raw = JSON.parse(readFileSync(join(root, 'data/update-attempt.json'), 'utf8'));
  writeFileSync(
    join(root, 'data/update-attempt.json'),
    JSON.stringify({ ...raw, snapshot_required: true, staged_snapshot: staged }),
  );
  const events: unknown[] = [];
  try {
    const code = await install(
      request,
      {
        async service(action) {
          events.push(action);
        },
        async capture() {
          return { kind: 'returned', returncode: 0 };
        },
        async snapshot(deployed_revision, _descriptor, staged_copy) {
          events.push(['snapshot', deployed_revision, staged_copy]);
          new InstallationRecord(request).snapshot_taken('/scripted/snapshot');
        },
        async verify_snapshot(snapshot) {
          events.push(['verify_snapshot', snapshot, record(root).state]);
          throw new Error('SQLite integrity check failed');
        },
        async schema_steps() {
          events.push('schema_steps');
        },
        async restore() {
          assert.fail('a healthy install restores nothing');
        },
        async healthy(sha) {
          events.push(['healthy', sha]);
          return true;
        },
      },
      { stderr() {} },
      new AbortController().signal,
    );
    assert.equal(code, 0);
    assert.deepEqual(events, [
      'stop',
      ['snapshot', FROM, staged],
      'schema_steps',
      'start',
      ['healthy', TO],
      ['verify_snapshot', '/scripted/snapshot', 'done'],
    ]);
    assert.equal(record(root).state, 'done');
    assert.equal(typeof record(root).steps.snapshot_check_failed, 'string');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it('a failing step restores the backup before the previous app starts', async () => {
  // A step failure must reach roll_back with no start of either app before the restore.
  const { root, request } = installation('panels-schema-step-failure-', '/scripted/snapshot');
  const events: unknown[] = [];
  try {
    const code = await install(
      request,
      {
        async service(action) {
          events.push(action);
        },
        async capture() {
          return { kind: 'returned', returncode: 0 };
        },
        async snapshot() {
          assert.fail('this attempt requires no snapshot');
        },
        async schema_steps() {
          events.push('schema_steps');
          throw new Error('scripted schema step failure');
        },
        async verify_snapshot() {
          throw new Error('this attempt staged no copy');
        },
        async restore(snapshot) {
          events.push(['restore', snapshot, installed_sha(root)]);
        },
        async healthy(sha) {
          events.push(['healthy', sha]);
          return true;
        },
      },
      { stderr() {} },
      new AbortController().signal,
    );
    assert.equal(code, 1);
    assert.deepEqual(events, [
      'stop',
      'schema_steps',
      'stop',
      // Still the new app: a kill before the renames leaves it for its startup recovery.
      ['restore', '/scripted/snapshot', TO],
      'start',
      ['healthy', FROM],
    ]);
    assert.match(record(root).detail, /scripted schema step failure/);
    assert.match(record(root).detail, /went back to the previous version and its data/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it('a failed snapshot leaves the app unchanged and starts it again', async () => {
  // The service is already stopped when the snapshot runs. A failure there must not
  // leave Panels down, swap the app, or restore anything.
  const { root, request, attempt } = installation('panels-snapshot-failure-', null);
  attempt(null, true);
  const events: unknown[] = [];
  try {
    const code = await install(
      request,
      {
        async service(action) {
          events.push(action);
        },
        async capture() {
          return { kind: 'returned', returncode: 0 };
        },
        async snapshot() {
          throw new Error('scripted snapshot failure');
        },
        async schema_steps() {
          assert.fail('no step runs without its snapshot');
        },
        async verify_snapshot() {
          throw new Error('this attempt staged no copy');
        },
        async restore() {
          assert.fail('nothing changed, so nothing is restored');
        },
        async healthy(sha) {
          events.push(['healthy', sha]);
          return true;
        },
      },
      { stderr() {} },
      new AbortController().signal,
    );
    assert.equal(code, 1);
    assert.deepEqual(events, ['stop', 'start', ['healthy', FROM]]);
    assert.equal(installed_sha(root), FROM);
    assert.match(record(root).detail, /scripted snapshot failure/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it('step applied, health fails, old app runs on the restored database', async () => {
  // Rollback must restore the data before the old app starts, or the old app meets a
  // database it refuses. The installer takes the snapshot itself, after the stop and
  // before the swap, and the rollback restores that one. Real backup, steps and restore.
  const { root, request, attempt } = installation('panels-schema-step-rollback-', null);
  try {
    createDatabaseFromSeed(request.database, BASELINE_SEED, () => {}).close();
    attempt(null, true);
    const events: unknown[] = [];
    const effects: InstallerEffects = {
      async service(action) {
        events.push(action);
      },
      async capture() {
        return { kind: 'returned', returncode: 0 };
      },
      async snapshot(deployed_revision, descriptor) {
        assert.equal(typeof descriptor, 'number');
        events.push(['snapshot', installed_sha(root)]);
        new InstallationRecord(request).snapshot_taken(
          await create_database_backup(
            request.database,
            request.backup_directory,
            deployed_revision,
          ),
        );
      },
      async schema_steps(_descriptor, backup_taken) {
        events.push('schema_steps');
        applySchemaSteps(request.database, TEST_SCHEMA_STEPS, {
          head_revision: rebuild_step.revision,
          expected_statements: null,
          refuse_pending: !backup_taken,
          baseline: BASELINE_SEED,
        });
      },
      async verify_snapshot() {
        throw new Error('this attempt staged no copy');
      },
      async restore(snapshot, descriptor) {
        assert.equal(typeof descriptor, 'number');
        events.push('restore');
        await restore_database_snapshot(snapshot, request.database, { live_stopped: true });
      },
      async healthy(sha) {
        events.push(['healthy', sha]);
        if (sha === TO) {
          // The new app writes before its health check fails.
          const database = createDatabase(request.database);
          try {
            database.write((tx) =>
              tx.run(
                "INSERT INTO projects (id, name, created_at, updated_at) VALUES ('after_step', 'After the step', 0, 0)",
              ),
            );
          } finally {
            database.close();
          }
          return false;
        }
        // The previous app's revision, its schema, and no row from after the step.
        const reader = createReadAccess(request.database);
        try {
          return reader.read(
            (sql) =>
              sql.get<{ version_num: string }>('SELECT version_num FROM alembic_version')
                ?.version_num === BASELINE_SEED.alembicRevision &&
              JSON.stringify(
                sql.all<{ sql: string }>(SEED_STATEMENT_QUERY).map((row) => row.sql),
              ) === JSON.stringify(BASELINE_SEED.statements) &&
              sql.get("SELECT 1 FROM projects WHERE id = 'after_step'") === undefined,
          );
        } finally {
          reader.close();
        }
      },
    };
    const code = await install(request, effects, { stderr() {} }, new AbortController().signal);
    assert.equal(code, 1);
    assert.deepEqual(events, [
      'stop',
      ['snapshot', FROM],
      'schema_steps',
      'start',
      ['healthy', TO],
      'stop',
      'restore',
      'start',
      ['healthy', FROM],
    ]);
    assert.equal(installed_sha(root), FROM);
    assert.equal(record(root).state, 'failed');
    assert.match(record(root).detail, /went back to the previous version and its data/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

it('node-v2 launches the child with the lock fd; no-backup plus pending steps refuses before writing', async () => {
  // A step must never run without a backup. Use the packaged schema entry on SQLite.
  const { root, request } = installation('panels-schema-step-child-', null);
  const owner = new ChildProcesses(),
    controller = new AbortController();
  const original = owner.spawn.bind(owner),
    launched: { argv: string[]; descriptor: unknown }[] = [];
  owner.spawn = ((command: string, args: readonly string[], options) => {
    if (args.some((arg) => arg.endsWith('schema_step_main.ts')))
      launched.push({
        argv: [command, ...args],
        descriptor: Array.isArray(options.stdio) ? options.stdio[3] : undefined,
      });
    return original(command, args, options);
  }) as ChildProcesses['spawn'];
  const lock = openSync(join(root, 'lifetime.lock'), 'w');
  let unregister = () => {};
  try {
    const main = join(root, 'app/server/environments/updates/schema_step_main.ts'),
      real_main = fileURLToPath(new URL('../schema_step_main.ts', import.meta.url));
    mkdirSync(dirname(main), { recursive: true });
    symlinkSync(real_main, main);
    // The installer's test path guard follows the link. Admit only the entry's own folder,
    // a source folder: nothing opens or writes a database there, and finally unregisters it.
    unregister = register_test_root(dirname(real_main));
    createDatabaseFromSeed(request.database, application_schema_seed, () => {}).close();
    const effects = create_installer_effects(request, {
      child_processes: owner,
      signal: controller.signal,
      environment: { ...process.env },
    });

    await effects.schema_steps(lock, false);
    assert.deepEqual(launched, [
      { argv: [process.execPath, main, request.database, 'no-backup'], descriptor: lock },
    ]);

    // The real snapshot_main.ts child, after the stop: a wrong argument or record write
    // would fail every update that has schema steps. Its snapshot must restore.
    symlinkSync(
      fileURLToPath(new URL('../snapshot_main.ts', import.meta.url)),
      join(root, 'app/server/environments/updates/snapshot_main.ts'),
    );
    await effects.snapshot(FROM, lock);
    const taken = record(root);
    assert.equal(typeof taken.steps.backup, 'string');
    assert.equal(dirname(taken.backup), request.backup_directory);
    await restore_database_snapshot(taken.backup, join(root, 'restored.sqlite'), {
      live_stopped: true,
    });

    // The staged route through the same children: rows written after the staged check reach
    // the snapshot, which equals the stopped file byte for byte and passes the late check.
    const staged = await stage_database_copy(request.database, request.backup_directory);
    const writer = new BetterSqlite3(request.database);
    writer.exec('CREATE TABLE after_staging (id INTEGER PRIMARY KEY, body BLOB)');
    writer
      .prepare(
        'WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 50) ' +
          'INSERT INTO after_staging (body) SELECT randomblob(3000) FROM n',
      )
      .run();
    writer.prepare('INSERT INTO after_staging (body) VALUES (randomblob(200000))').run();
    // An open idle connection keeps those frames in the WAL until the capture checkpoints.
    assert.ok(statSync(request.database + '-wal').size > 0);
    await effects.snapshot(FROM, lock, staged);
    writer.close();
    const caught_up = record(root).backup as string;
    const bytes = (path: string) => createHash('sha256').update(readFileSync(path)).digest('hex');
    assert.equal(bytes(join(caught_up, 'database.sqlite')), bytes(request.database));
    assert.equal(existsSync(staged), false);
    const release = join(root, '.release-' + FROM, 'server/environments/updates');
    mkdirSync(release, { recursive: true });
    symlinkSync(
      fileURLToPath(new URL('../snapshot_verify_main.ts', import.meta.url)),
      join(release, 'snapshot_verify_main.ts'),
    );
    await effects.verify_snapshot(caught_up);
    // Retention, deferred from the window, keeps both verified snapshots.
    assert.equal(
      readdirSync(request.backup_directory).filter((name) => name.startsWith('snapshot-')).length,
      2,
    );

    // Pending steps without a backup refuse before the first write.
    const stepped = join(root, 'baseline.sqlite');
    createDatabaseFromSeed(stepped, BASELINE_SEED, () => {}).close();
    const digest = () => createHash('sha256').update(readFileSync(stepped)).digest('hex');
    const before = digest();
    assert.throws(
      () =>
        applySchemaSteps(stepped, TEST_SCHEMA_STEPS, {
          head_revision: rebuild_step.revision,
          expected_statements: null,
          refuse_pending: true,
          baseline: BASELINE_SEED,
        }),
      /no backup was taken, so none ran/,
    );
    assert.equal(digest(), before);
  } finally {
    unregister();
    closeSync(lock);
    owner.seal();
    controller.abort();
    await owner.settled();
    rmSync(root, { recursive: true, force: true });
  }
});
