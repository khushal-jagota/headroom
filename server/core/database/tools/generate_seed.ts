/**
 * Build-time generator. The live seed and migration graph are the frozen Python
 * baseline with the TypeScript schema steps applied. `--check` refuses any drift.
 */
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  applySchemaSteps,
  createDatabaseFromSeed,
  createReadAccess,
  SEED_STATEMENT_QUERY,
} from '../index.ts';
import type { ApplicationSchemaSeed } from '../contracts.ts';
import { SCHEMA_STEPS, validate_schema_step_chain } from '../schema_steps/contracts.ts';
import type { SchemaStep } from '../schema_steps/contracts.ts';
import {
  BASELINE_MIGRATION_GRAPH_PATH,
  BASELINE_SEED_PATH,
  MIGRATION_GRAPH_PATH,
  SEED_PATH,
  migration_frontier,
  read_migration_graph_file,
} from '../../../environments/release/migrations.ts';

export interface SchemaArtifacts {
  /** The exact bytes of `application_schema.json`. */
  readonly seed: string;
  /** The exact bytes of `migration_graph.json`. */
  readonly graph: string;
}

function read_baseline_seed(root: string): ApplicationSchemaSeed {
  const seed: unknown = JSON.parse(readFileSync(join(root, BASELINE_SEED_PATH), 'utf8'));
  if (
    seed === null ||
    typeof seed !== 'object' ||
    !('statements' in seed) ||
    !Array.isArray(seed.statements) ||
    !seed.statements.every((statement) => typeof statement === 'string') ||
    !('alembicRevision' in seed) ||
    typeof seed.alembicRevision !== 'string'
  )
    throw new Error('The Python baseline seed is not a valid seed');
  return seed as ApplicationSchemaSeed;
}

function read_statements(path: string): string[] {
  const reader = createReadAccess(path);
  try {
    return reader.read((sql) =>
      sql.all<{ sql: string }>(SEED_STATEMENT_QUERY).map((row) => String(row.sql)),
    );
  } finally {
    reader.close();
  }
}

function create_from_seed(path: string, seed: ApplicationSchemaSeed): void {
  createDatabaseFromSeed(path, seed, () => {}).close();
}

/** Python's `json.dumps(sort_keys=True, indent=2)`, which the graph's readers already accept. */
function sorted_graph_json(value: unknown, indent = ''): string {
  const inner = indent + '  ';
  if (Array.isArray(value))
    return value.length === 0
      ? '[]'
      : '[\n' +
          value.map((item) => inner + sorted_graph_json(item, inner)).join(',\n') +
          '\n' +
          indent +
          ']';
  if (value !== null && typeof value === 'object') {
    const keys = Object.keys(value).sort();
    return keys.length === 0
      ? '{}'
      : '{\n' +
          keys
            .map(
              (key) =>
                inner +
                JSON.stringify(key) +
                ': ' +
                sorted_graph_json((value as Record<string, unknown>)[key], inner),
            )
            .join(',\n') +
          '\n' +
          indent +
          '}';
  }
  return JSON.stringify(value);
}

export async function build_schema_artifacts(
  root: string,
  steps: readonly SchemaStep[],
): Promise<SchemaArtifacts> {
  const baseline_graph = await read_migration_graph_file(root, BASELINE_MIGRATION_GRAPH_PATH);
  const baseline_seed = read_baseline_seed(root);
  const [baseline_head] = migration_frontier(baseline_graph);
  if (baseline_seed.alembicRevision !== baseline_head)
    throw new Error('The Python baseline seed and graph name different revisions');
  for (const step of steps)
    if (Object.hasOwn(baseline_graph.revisions, step.revision))
      throw new Error(`Schema step ${step.revision} is already a Python baseline revision`);
  validate_schema_step_chain(steps, baseline_head);
  const head = steps.at(-1)?.revision ?? baseline_head;
  const directory = mkdtempSync(join(tmpdir(), 'panels-schema-seed-'));
  try {
    const stepped = join(directory, 'stepped.sqlite');
    create_from_seed(stepped, baseline_seed);
    applySchemaSteps(stepped, steps, {
      head_revision: head,
      expected_statements: null,
      refuse_pending: false,
      baseline: null,
    });
    const seed: ApplicationSchemaSeed = {
      statements: read_statements(stepped),
      alembicRevision: head,
    };
    // A fresh install from the new seed must read back exactly what the steps made.
    const fresh = join(directory, 'fresh.sqlite');
    create_from_seed(fresh, seed);
    const round_trip = read_statements(fresh);
    if (
      round_trip.length !== seed.statements.length ||
      round_trip.some((statement, index) => statement !== seed.statements[index])
    )
      throw new Error('A database made from the generated seed differs from the stepped database');
    const revisions: Record<string, readonly string[]> = { ...baseline_graph.revisions };
    for (const step of steps) revisions[step.revision] = [step.previous_revision];
    return {
      seed: JSON.stringify(seed, null, 2) + '\n',
      graph:
        sorted_graph_json({
          version: baseline_graph.version,
          source_digest: baseline_graph.source_digest,
          revisions,
        }) + '\n',
    };
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

export async function check_schema_artifacts(
  root: string,
  steps: readonly SchemaStep[],
): Promise<SchemaArtifacts> {
  const artifacts = await build_schema_artifacts(root, steps);
  if (readFileSync(join(root, SEED_PATH), 'utf8') !== artifacts.seed)
    throw new Error(
      `${SEED_PATH} differs from the Python baseline plus the schema steps. Run node server/core/database/tools/generate_seed.ts`,
    );
  if (readFileSync(join(root, MIGRATION_GRAPH_PATH), 'utf8') !== artifacts.graph)
    throw new Error(
      `${MIGRATION_GRAPH_PATH} differs from the Python baseline plus the schema steps. Run node server/core/database/tools/generate_seed.ts`,
    );
  return artifacts;
}

export async function write_schema_artifacts(
  root: string,
  steps: readonly SchemaStep[],
): Promise<SchemaArtifacts> {
  const artifacts = await build_schema_artifacts(root, steps);
  writeFileSync(join(root, SEED_PATH), artifacts.seed);
  writeFileSync(join(root, MIGRATION_GRAPH_PATH), artifacts.graph);
  return artifacts;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const args = process.argv.slice(2);
  if (args.length > 1 || (args.length === 1 && args[0] !== '--check')) {
    process.stderr.write('usage: node server/core/database/tools/generate_seed.ts [--check]\n');
    process.exitCode = 2;
  } else {
    const root = fileURLToPath(new URL('../../../../', import.meta.url));
    const artifacts = await (
      args[0] === '--check' ? check_schema_artifacts : write_schema_artifacts
    )(root, SCHEMA_STEPS);
    const seed = JSON.parse(artifacts.seed) as ApplicationSchemaSeed;
    process.stdout.write(
      JSON.stringify({
        alembicRevision: seed.alembicRevision,
        statements: seed.statements.length,
        steps: SCHEMA_STEPS.length,
      }) + '\n',
    );
  }
}
