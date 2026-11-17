/** Python migrations reach the frozen baseline. TypeScript schema steps carry it to the head. */
import type { ReadSql } from '../../core/database/contracts.ts';
import { AppValidationError } from './contracts.ts';
import type { MigrationGraph } from './contracts.ts';
import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';

function validate_migration_topology(revisions: MigrationGraph['revisions']): void {
  const visiting = new Set<string>(),
    visited = new Set<string>();
  const visit = (revision: string): void => {
    if (visiting.has(revision))
      throw new AppValidationError('invalid generated migration graph: cycle');
    if (visited.has(revision)) return;
    visiting.add(revision);
    for (const parent of revisions[revision]!) {
      if (parent === revision)
        throw new AppValidationError('invalid generated migration graph: self-parent');
      if (!Object.hasOwn(revisions, parent))
        throw new AppValidationError('invalid generated migration graph: dangling parent');
      visit(parent);
    }
    visiting.delete(revision);
    visited.add(revision);
  };
  for (const revision of Object.keys(revisions)) visit(revision);
  const heads = new Set(Object.keys(revisions));
  for (const parents of Object.values(revisions))
    for (const parent of parents) heads.delete(parent);
  if (heads.size !== 1)
    throw new AppValidationError('invalid generated migration graph: expected exactly one head');
}

export const MIGRATION_GRAPH_PATH = 'server/environments/release/migration_graph.json';
export const BASELINE_MIGRATION_GRAPH_PATH =
  'server/core/database/python_baseline/migration_graph.json';
export const BASELINE_SEED_PATH = 'server/core/database/python_baseline/application_schema.json';
export const SEED_PATH = 'server/core/database/application_schema.json';

/** The digest of the raw Python migration bytes that a graph file binds. */
export async function python_migration_digest(root: string): Promise<string> {
  const directory = join(root, 'src/planner/core/migrations/versions');
  const digest = createHash('sha256');
  for (const name of (await readdir(directory))
    .filter((name) => name.endsWith('.py'))
    .sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b)))) {
    digest.update(name + '\0');
    digest.update(await readFile(join(directory, name)));
    digest.update('\0');
  }
  return digest.digest('hex');
}

/** Parse, digest and topology checks for one graph file. Missing files stay ENOENT. */
export async function read_migration_graph_file(
  root: string,
  relative_path: string,
): Promise<MigrationGraph> {
  let graph: unknown;
  try {
    graph = JSON.parse(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
        await readFile(join(root, relative_path)),
      ),
    );
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError)
      throw new AppValidationError('invalid generated migration graph');
    throw error;
  }
  if (
    graph === null ||
    typeof graph !== 'object' ||
    Array.isArray(graph) ||
    Object.keys(graph).sort().join(',') !== 'revisions,source_digest,version' ||
    !('version' in graph) ||
    graph.version !== 1 ||
    !('source_digest' in graph) ||
    typeof graph.source_digest !== 'string' ||
    !/^[0-9a-f]{64}$/.test(graph.source_digest) ||
    !('revisions' in graph) ||
    graph.revisions === null ||
    typeof graph.revisions !== 'object' ||
    Array.isArray(graph.revisions) ||
    !Object.values(graph.revisions).every(
      (parents) => Array.isArray(parents) && parents.every((parent) => typeof parent === 'string'),
    )
  )
    throw new AppValidationError('invalid generated migration graph');
  if ((await python_migration_digest(root)) !== graph.source_digest)
    throw new AppValidationError(
      'migration graph differs from the current Python migration source',
    );
  validate_migration_topology((graph as MigrationGraph).revisions);
  return graph as MigrationGraph;
}

/** Build input, bound to raw migration bytes without a runtime Python parser. */
export async function read_migration_graph(root: string): Promise<MigrationGraph> {
  return read_migration_graph_file(root, MIGRATION_GRAPH_PATH);
}

/** The revisions a TypeScript release accepts: its head, and the chain that leads to it. */
export interface NodeSchemaFrontier {
  readonly head: string;
  /** The baseline head and every TypeScript step revision before the head. */
  readonly step_ancestors: ReadonlySet<string>;
}

function single_head(graph: MigrationGraph): string {
  const [head] = migration_frontier(graph);
  return head!;
}

/**
 * The live graph is the frozen Python baseline plus one line of TypeScript steps.
 * Without a baseline file the chain is empty, and only the head is accepted.
 */
export async function read_node_schema_frontier(root: string): Promise<NodeSchemaFrontier> {
  const live = await read_migration_graph(root);
  const head = single_head(live);
  let baseline: MigrationGraph;
  try {
    baseline = await read_migration_graph_file(root, BASELINE_MIGRATION_GRAPH_PATH);
  } catch (error) {
    if (error instanceof Error && 'code' in error && error.code === 'ENOENT')
      return { head, step_ancestors: new Set() };
    throw error;
  }
  const differs = () =>
    new AppValidationError('migration graph differs from its Python baseline and schema steps');
  if (baseline.source_digest !== live.source_digest) throw differs();
  for (const [revision, parents] of Object.entries(baseline.revisions)) {
    const actual = live.revisions[revision];
    if (
      !Object.hasOwn(live.revisions, revision) ||
      actual!.length !== parents.length ||
      actual!.some((parent, index) => parent !== parents[index])
    )
      throw differs();
  }
  const baseline_head = single_head(baseline);
  const steps = new Set(
    Object.keys(live.revisions).filter((revision) => !Object.hasOwn(baseline.revisions, revision)),
  );
  // Walk back from the head. Every step has one parent, and the walk ends at the baseline head.
  const visited = new Set<string>(),
    ancestors = new Set<string>();
  let revision = head;
  while (revision !== baseline_head) {
    const parents = live.revisions[revision]!;
    if (!steps.has(revision) || parents.length !== 1) throw differs();
    visited.add(revision);
    revision = parents[0]!;
    ancestors.add(revision);
  }
  if (visited.size !== steps.size) throw differs();
  return { head, step_ancestors: ancestors };
}

/** The release build and the Mac payload: the seed revision is the graph's single head. */
export async function require_generated_schema_frontier(root: string): Promise<void> {
  const { head } = await read_node_schema_frontier(root);
  let seed: unknown;
  try {
    seed = JSON.parse(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
        await readFile(join(root, SEED_PATH)),
      ),
    );
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError)
      throw new AppValidationError('migration graph differs from the generated schema frontier');
    throw error;
  }
  if (
    seed === null ||
    typeof seed !== 'object' ||
    !('alembicRevision' in seed) ||
    seed.alembicRevision !== head
  )
    throw new AppValidationError('migration graph differs from the generated schema frontier');
}

export function migration_frontier(graph: MigrationGraph): ReadonlySet<string> {
  const heads = new Set(Object.keys(graph.revisions));
  for (const parents of Object.values(graph.revisions))
    for (const parent of parents) heads.delete(parent);
  return heads;
}
function database_revisions(sql: ReadSql): Set<string> {
  return new Set(
    sql
      .all<{ version_num: string }>('SELECT version_num FROM alembic_version')
      .map(({ version_num }) => String(version_num)),
  );
}
/** Preserve Python's existing v1 backup decision. It does not admit a v2 migration. */
export function schema_change_pending(sql: ReadSql, graph: MigrationGraph): boolean {
  const revisions = database_revisions(sql);
  if (
    !revisions.size ||
    [...revisions].some((revision) => !Object.hasOwn(graph.revisions, revision))
  )
    throw new AppValidationError("This release does not know the database's schema revision.");
  const heads = migration_frontier(graph);
  return revisions.size !== heads.size || [...revisions].some((revision) => !heads.has(revision));
}
/** Admit a TypeScript release's database, and say whether its steps still need to run. */
export function node_schema_steps_pending(sql: ReadSql, frontier: NodeSchemaFrontier): boolean {
  const revisions = [...database_revisions(sql)];
  const revision = revisions[0];
  if (
    revisions.length !== 1 ||
    (revision !== frontier.head && !frontier.step_ancestors.has(revision!))
  )
    throw new AppValidationError(
      'This release requires the database to be at exactly its schema revision. ' +
        'Install the Python release that carries the same migrations first.',
    );
  return revision !== frontier.head;
}
