import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  chmodSync,
  closeSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  rmSync,
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
import { create_database_backup } from '../../backup/capture.ts';
import { restore_database_snapshot } from '../../backup/restore.ts';
import { create_installer_effects } from './commands.ts';
import { install } from './install.ts';
import { structural_app } from './testing/structural_app.ts';
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
    attempt_id: 'schema-steps',
    node_executable: process.execPath,
    service_unit: 'never-start.service',
    health_url: 'http://127.0.0.1:0',
    health_timeout_seconds: 1,
    admitted_legacy_null_sha: null,
  };
  const attempt = (snapshot: string | null) =>
    writeFileSync(
      join(data, 'update-attempt.json'),
      JSON.stringify({
        attempt_id: 'schema-steps',
        state: 'installing',
        from_sha: FROM,
        to_sha: TO,
        backup: snapshot,
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
          async schema_steps(descriptor, backup_taken) {
            events.push(['schema_steps', installed_sha(root), typeof descriptor, backup_taken]);
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
        async schema_steps() {
          events.push('schema_steps');
          throw new Error('scripted schema step failure');
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

it('step applied, health fails, old app runs on the restored database', async () => {
  // Rollback must restore the data before the old app starts, or the old app meets a
  // database it refuses. Real backup, real steps, real restore.
  const { root, request, attempt } = installation('panels-schema-step-rollback-', null);
  try {
    createDatabaseFromSeed(request.database, BASELINE_SEED, () => {}).close();
    attempt(await create_database_backup(request.database, join(root, 'backups'), FROM));
    const events: unknown[] = [];
    const effects: InstallerEffects = {
      async service(action) {
        events.push(action);
      },
      async capture() {
        return { kind: 'returned', returncode: 0 };
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

it('only node-v2 launches the child, with the lock fd; no-backup plus pending steps refuses before writing', async () => {
  // A step must never run on a Python v1 app or without a backup. The real classifier
  // and the real schema_step_main.ts in a structural app.
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
    rmSync(join(root, 'app'), { recursive: true });
    structural_app(join(root, 'app'), FROM);
    const main = join(root, 'app/server/environments/updates/schema_step_main.ts'),
      real_main = fileURLToPath(new URL('../schema_step_main.ts', import.meta.url));
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

    // The same app as Python version 1 upgrades itself, so no step child starts.
    const contract = JSON.stringify({ version: 1 });
    const manifest = JSON.parse(readFileSync(join(root, 'app/manifest.json'), 'utf8'));
    writeFileSync(
      join(root, 'app/manifest.json'),
      JSON.stringify({ ...manifest, runtime_contract: { version: 1 } }),
    );
    writeFileSync(join(root, 'app/runtime/contract.json'), contract);
    mkdirSync(join(root, 'app/.venv/bin'), { recursive: true });
    writeFileSync(join(root, 'app/.venv/bin/python'), '#!/bin/sh\nexit 99\n');
    chmodSync(join(root, 'app/.venv/bin/python'), 0o755);
    await effects.schema_steps(lock, true);
    assert.equal(launched.length, 1);

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
