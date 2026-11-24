import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { afterEach, it } from 'vitest';
import BetterSqlite3 from 'better-sqlite3';
import {
  applySchemaSteps,
  createDatabase,
  createDatabaseFromSeed,
  createReadAccess,
  SEED_STATEMENT_QUERY,
} from './index.ts';
import { application_schema_seed } from './admission.ts';
import { SCHEMA_STEPS } from './schema_steps/contracts.ts';
import {
  LIVE_NAVIGATION_VIEW_ORDERED_TABLE,
  LIVE_SIGNAL_SOURCE_AGENTS_TRIGGERS,
  live_baseline_variant,
} from './schema_steps/live_baseline_variant.ts';
import { build_schema_artifacts } from './tools/generate_seed.ts';
import type { ApplicationSchemaSeed } from './contracts.ts';
import {
  BASELINE_SEED,
  TEST_SCHEMA_STEPS,
  dangling_fk_step,
  failing_step,
  probe_step,
  rebuild_step,
} from './schema_steps/testing/steps.ts';

const roots: string[] = [];
afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});
function baseline_database(seed: ApplicationSchemaSeed = BASELINE_SEED): string {
  const root = mkdtempSync(join(tmpdir(), 'panels-schema-steps-'));
  roots.push(root);
  const path = join(root, 'planner.db');
  createDatabaseFromSeed(path, seed, () => {}).close();
  return path;
}
function state(path: string) {
  const reader = createReadAccess(path);
  try {
    return reader.read((sql) => ({
      revision: sql
        .all<{ version_num: string }>('SELECT version_num FROM alembic_version')
        .map((row) => row.version_num),
      statements: sql.all<{ sql: string }>(SEED_STATEMENT_QUERY).map((row) => row.sql),
    }));
  } finally {
    reader.close();
  }
}

it('a failing step rolls back only itself; a drifted baseline runs no step', () => {
  // Partial DDL must never commit. Real SQLite on the baseline seed.
  for (const broken of [failing_step, dangling_fk_step]) {
    const path = baseline_database();
    assert.throws(
      () =>
        applySchemaSteps(path, [probe_step, broken], {
          head_revision: broken.revision,
          expected_statements: null,
          refuse_pending: false,
          baseline: BASELINE_SEED,
        }),
      broken === failing_step ? /scripted schema step failure/ : /foreign key violation/,
    );
    const after = state(path);
    assert.deepEqual(after.revision, [probe_step.revision]);
    assert(after.statements.some((sql) => sql.startsWith('CREATE TABLE schema_step_probe ')));
    assert(
      !after.statements.some((sql) => /schema_step_(failed|orphan)/.test(sql)),
      'the broken step left its DDL',
    );
  }
  // A baseline-revision database whose schema text differs from the baseline: no step runs.
  const root = mkdtempSync(join(tmpdir(), 'panels-schema-steps-'));
  roots.push(root);
  const drifted = join(root, 'drifted.db');
  const [first, ...rest] = BASELINE_SEED.statements;
  createDatabaseFromSeed(
    drifted,
    { ...BASELINE_SEED, statements: [first!.replace('(', '(  '), ...rest] },
    () => {},
  ).close();
  const before = state(drifted);
  assert.throws(
    () =>
      applySchemaSteps(drifted, TEST_SCHEMA_STEPS, {
        head_revision: rebuild_step.revision,
        expected_statements: null,
        refuse_pending: false,
        baseline: BASELINE_SEED,
      }),
    /schema text differs from the baseline, so no step ran/,
  );
  assert.deepEqual(state(drifted), before);
});

it('migrated database equals the generated seed; a one-character difference refuses and rolls back the last step', async () => {
  // Fresh and migrated databases must not drift apart. The generator builds the expected seed.
  const repository = resolve(import.meta.dirname, '../../..');
  const seed = JSON.parse(
    (await build_schema_artifacts(repository, TEST_SCHEMA_STEPS)).seed,
  ) as ApplicationSchemaSeed;
  assert.equal(seed.alembicRevision, rebuild_step.revision);

  const migrated = baseline_database();
  assert.deepEqual(
    applySchemaSteps(migrated, TEST_SCHEMA_STEPS, {
      head_revision: rebuild_step.revision,
      expected_statements: seed.statements,
      refuse_pending: false,
      baseline: BASELINE_SEED,
    }),
    {
      from_revision: BASELINE_SEED.alembicRevision,
      to_revision: rebuild_step.revision,
      applied: [probe_step.revision, rebuild_step.revision],
    },
  );
  assert.deepEqual(state(migrated), {
    revision: [rebuild_step.revision],
    statements: seed.statements,
  });
  // The rebuild kept the rows and dropped the CHECK.
  const reader = createReadAccess(migrated);
  try {
    assert.deepEqual(
      reader.read((sql) => sql.all('SELECT probe_id, kind FROM schema_step_probe')),
      [{ probe_id: 'probe', kind: 'a' }],
    );
  } finally {
    reader.close();
  }

  const statements = [...seed.statements];
  const last = statements.length - 1;
  statements[last] = statements[last]! + ' ';
  const refused = baseline_database();
  assert.throws(
    () =>
      applySchemaSteps(refused, TEST_SCHEMA_STEPS, {
        head_revision: rebuild_step.revision,
        expected_statements: statements,
        refuse_pending: false,
        baseline: BASELINE_SEED,
      }),
    /differs from this release's seed/,
  );
  const after = state(refused);
  assert.deepEqual(after.revision, [probe_step.revision]);
  assert(after.statements.some((sql) => sql.includes("CHECK (kind IN ('a', 'b'))")));
});

/** The release chain, exactly as the installer's child runs it. */
const release_steps = (path: string) =>
  applySchemaSteps(path, SCHEMA_STEPS, {
    head_revision: application_schema_seed.alembicRevision,
    expected_statements: application_schema_seed.statements,
    refuse_pending: false,
    baseline: BASELINE_SEED,
  });
const write = (path: string, statements: readonly string[]) => {
  const database = createDatabase(path);
  try {
    database.write((tx) => {
      for (const statement of statements) tx.run(statement);
    });
  } finally {
    database.close();
  }
};
/** One row per formerly checked column, keyed by a backend kind. */
const keyed_rows = (kind: string) => [
  `INSERT INTO backend_model_enablement (backend_key, model_id, enabled) VALUES ('${kind}', 'model', 1)`,
  `INSERT INTO backend_usage_snapshots (backend_key, observed_at) VALUES ('${kind}', 1)`,
  `INSERT INTO goals (id, project_id, title, why, kind, active, completed, page, revision, created_at, updated_at, employee_backend, employee_launch_model) VALUES ('goal_${kind}', 'project', 'Goal', 'Why', 'binary', 1, 0, '{}', 1, 0, 0, '${kind}', 'model')`,
  `INSERT INTO native_usage_files (provider, source_key, file_key, snapshot) VALUES ('${kind}', 'source', 'file', '{}')`,
  `INSERT INTO native_usage_sources (provider, source_key, snapshot) VALUES ('${kind}', 'source', '{}')`,
];
const REBUILT_AND_REFERENCING = [
  'backend_model_enablement',
  'backend_usage_snapshots',
  'backend_usage_windows',
  'goals',
  'measure_sources',
  'native_usage_files',
  'native_usage_sources',
];
function rows(path: string) {
  const reader = createReadAccess(path);
  try {
    return reader.read((sql) =>
      REBUILT_AND_REFERENCING.map((table) => sql.all(`SELECT * FROM ${table} ORDER BY 1, 2`)),
    );
  } finally {
    reader.close();
  }
}
function foreign_key_violations(path: string): unknown[] {
  const connection = new BetterSqlite3(path, { readonly: true, fileMustExist: true });
  try {
    return connection.prepare('PRAGMA foreign_key_check').all();
  } finally {
    connection.close();
  }
}
/** The statement the server ran at startup before step 1 took the table over. */
const RUNTIME_NAVIGATION_VIEW_ORDERED = LIVE_NAVIGATION_VIEW_ORDERED_TABLE.replace(
  'CREATE TABLE ',
  'CREATE TABLE IF NOT EXISTS ',
);
const ORDERED_ROW =
  "INSERT INTO navigation_view_ordered VALUES ('section', 'profile', 'en', 0, 'ticket', 't1', 1, x'01', x'02')";
function ordered_rows(path: string) {
  const reader = createReadAccess(path);
  try {
    return reader.read((sql) => sql.all('SELECT * FROM navigation_view_ordered'));
  } finally {
    reader.close();
  }
}
const digest = (path: string) =>
  [path, path + '-wal'].map((file) =>
    createHash('sha256')
      .update(existsSync(file) ? readFileSync(file) : '')
      .digest('hex'),
  );

it('step 1 brings a baseline database, with either trigger text and with or without the navigation table, to the exact seed with its rows', () => {
  // A wrong rebuild loses rows, breaks a reference or leaves the seed and the stepped schema
  // apart, and every update would roll back. Every used database holds the navigation table
  // the server created at run time. Real SQLite, the release chain and seed.
  const live_variant = live_baseline_variant(BASELINE_SEED.statements);
  assert(live_variant !== null);
  for (const [statements, navigation_table] of [
    [BASELINE_SEED.statements, false],
    [BASELINE_SEED.statements, true],
    [live_variant, false],
    [live_variant, true],
  ] as const) {
    const path = baseline_database({ ...BASELINE_SEED, statements });
    if (navigation_table) write(path, [RUNTIME_NAVIGATION_VIEW_ORDERED, ORDERED_ROW]);
    write(path, [
      "INSERT INTO projects (id, name, created_at, updated_at) VALUES ('project', 'Project', 0, 0)",
      "INSERT INTO projects (id, name, created_at, updated_at) VALUES ('project_panels', 'Panels', 0, 0), ('project_wren', 'Wren', 0, 0), ('project_rewire', 'Rewire', 0, 0), ('project_vylo', 'Vylo', 0, 0), ('project_personal', 'Personal', 0, 0)",
      ...keyed_rows('codex'),
      "INSERT INTO backend_usage_windows (backend_key, window_kind, model_id, used_percent, resets_at) VALUES ('codex', 'five_hour', '', 40, 2)",
      "INSERT INTO measure_sources (id, name, project_id, account, responsible_kind, responsible_goal_id, responsibility_revision, source_revision, created_at, updated_at) VALUES ('source', 'Source', 'project', 'account', 'goal', 'goal_codex', 1, 1, 0, 0)",
    ]);
    // The baseline refuses a new backend key in every one of these columns.
    for (const statement of keyed_rows('acme'))
      assert.throws(() => write(path, [statement]), /CHECK constraint failed/);
    const before = rows(path);
    assert(before.every((table) => table.length === 1));
    const ordered_before = navigation_table ? ordered_rows(path) : [];
    assert.equal(ordered_before.length, navigation_table ? 1 : 0);

    assert.deepEqual(release_steps(path), {
      from_revision: BASELINE_SEED.alembicRevision,
      to_revision: 'ts_0010_answer_receipt_index',
      applied: [
        'ts_0001_open_backend_keys',
        'ts_0002_project_repository',
        'ts_0003_ticket_worker_setups',
        'ts_0004_drop_ideas',
        'ts_0005_client_versions',
        'ts_0006_server_link',
        'ts_0007_machine_backend_state',
        'ts_0008_conversation_machine',
        'ts_0009_work_moves',
        'ts_0010_answer_receipt_index',
      ],
    });
    assert.deepEqual(state(path), {
      revision: ['ts_0010_answer_receipt_index'],
      statements: application_schema_seed.statements,
    });
    const project_reader = createReadAccess(path);
    try {
      assert.deepEqual(
        project_reader.read((sql) =>
          sql.all(
            "SELECT id, repository_url, default_branch FROM projects WHERE id IN ('project_panels', 'project_wren', 'project_rewire', 'project_vylo', 'project_personal') ORDER BY id",
          ),
        ),
        [
          {
            id: 'project_panels',
            repository_url: 'https://github.com/khushal-jagota/panels.git',
            default_branch: 'staging',
          },
          { id: 'project_personal', repository_url: null, default_branch: null },
          {
            id: 'project_rewire',
            repository_url: 'https://github.com/khushal-jagota/rewire.git',
            default_branch: 'main',
          },
          {
            id: 'project_vylo',
            repository_url: 'https://github.com/khushal-jagota/vylo-convex.git',
            default_branch: 'main',
          },
          {
            id: 'project_wren',
            repository_url: 'https://github.com/khushal-jagota/Wren.git',
            default_branch: 'main',
          },
        ],
      );
    } finally {
      project_reader.close();
    }
    assert.deepEqual(rows(path), before);
    // Step 1 keeps an existing navigation table and its rows, and creates an empty one otherwise.
    assert.deepEqual(ordered_rows(path), ordered_before);
    assert.deepEqual(foreign_key_violations(path), []);
    // Every formerly checked column now stores a new backend key.
    write(path, keyed_rows('acme'));
  }
});

it('the answer receipt step indexes the receipt lookup', () => {
  // Without the index, every answer with an operation ID scans the conversation's whole event log.
  // Real SQLite, the release chain from the baseline, and the store's exact lookup.
  const path = baseline_database();
  release_steps(path);
  const connection = new BetterSqlite3(path, { readonly: true, fileMustExist: true });
  try {
    assert.deepEqual(
      connection
        .prepare("SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?")
        .all('idx_conversation_events_answer_operation_id'),
      [{ tbl_name: 'conversation_events' }],
    );
    const plan = connection
      .prepare(
        'EXPLAIN QUERY PLAN SELECT * FROM conversation_events ' +
          "WHERE conversation_id=? AND json_extract(payload,'$.operation_id')=? " +
          "AND kind IN ('permission_answered','user_input_answered') ORDER BY sequence DESC LIMIT 1",
      )
      .all('conversation', 'operation') as { detail: string }[];
    assert(
      plan.some((row) =>
        row.detail.includes('USING INDEX idx_conversation_events_answer_operation_id'),
      ),
      JSON.stringify(plan),
    );
  } finally {
    connection.close();
  }
});

it('step 1 refuses before writing on any baseline difference but the recorded live ones', () => {
  // The variant acceptance must not widen into accepting drift. Real SQLite.
  const live_variant = live_baseline_variant(BASELINE_SEED.statements)!;
  const [insert_trigger] = Object.values(LIVE_SIGNAL_SOURCE_AGENTS_TRIGGERS);
  const partial = BASELINE_SEED.statements.map((statement) =>
    statement.startsWith('CREATE TRIGGER signal_source_agents_insert ')
      ? insert_trigger!
      : statement,
  );
  const [first, ...rest] = live_variant;
  for (const statements of [
    partial,
    [first!.replace('(', '(  '), ...rest],
    // The navigation table is accepted only with its exact stored text.
    [...live_variant, LIVE_NAVIGATION_VIEW_ORDERED_TABLE.replace(',UNIQUE', ', UNIQUE')],
  ]) {
    const path = baseline_database({ ...BASELINE_SEED, statements });
    const before = digest(path);
    assert.throws(
      () => release_steps(path),
      /schema text differs from the baseline, so no step ran/,
    );
    assert.deepEqual(digest(path), before);
  }
});
