/**
 * Schema in the main database changes only through the schema steps. Runtime DDL there makes a
 * used database differ from the baseline and the seed, so the next schema step refuses it.
 * The scan reads every literal that starts a CREATE, ALTER or DROP statement in production code
 * under server/. An accepted limit: DDL assembled from a value the scan cannot read is not seen.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import ts from 'typescript';
import { expect, it } from 'vitest';

interface DdlFinding {
  readonly path: string;
  readonly line: number;
  readonly text: string;
}
/** Each path that may hold DDL, as a file or a directory prefix ending in '/'. */
const DDL_EXCEPTIONS: readonly { readonly path: string; readonly reason: string }[] = [
  {
    path: 'server/core/database/schema_steps/',
    reason: 'The schema steps, and the stored live text their pre-check accepts.',
  },
  {
    path: 'server/environments/vps_metrics/',
    reason: 'The VPS metrics file, a separate database.',
  },
  { path: 'server/executor/mac/storage/', reason: "The Mac executor's own separate database." },
  {
    path: 'server/environments/release/admission_probe.ts',
    reason: 'The admission probe, an in-memory database proving SQLite loads.',
  },
  {
    path: 'server/conversation/views/data/rebuild.ts',
    reason: "Private rebuild tables in the connection's temp schema, never in main.",
  },
];

/** A literal that starts a statement, such as `CREATE ${kind}INDEX`, or holds one anywhere. */
const DDL_START = /^\s*(?:CREATE|ALTER|DROP)\s/;
const DDL_ANYWHERE =
  /\b(?:CREATE|ALTER|DROP)\s+(?:(?:TEMP|TEMPORARY|UNIQUE|VIRTUAL)\s+)?(?:TABLE|INDEX|TRIGGER|VIEW)\b/i;

function production_source(path: string): boolean {
  return (
    path.endsWith('.ts') &&
    !path.endsWith('.test.ts') &&
    !path.endsWith('.d.ts') &&
    !path.includes('/node_modules/') &&
    !path.includes('/testing/') &&
    !path.includes('/tests/') &&
    path !== 'server/test_host.ts'
  );
}
function ddl_findings(path: string, text: string): DdlFinding[] {
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true);
  const findings: DdlFinding[] = [];
  const visit = (node: ts.Node): void => {
    if (
      (ts.isStringLiteralLike(node) || ts.isTemplateLiteralToken(node)) &&
      (DDL_START.test(node.text) || DDL_ANYWHERE.test(node.text))
    )
      findings.push({
        path,
        line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1,
        text: node.text.trim().slice(0, 80),
      });
    ts.forEachChild(node, visit);
  };
  visit(source);
  return findings;
}
function server_files(root: string): string[] {
  const paths: string[] = [];
  const walk = (directory: string): void => {
    for (const entry of readdirSync(join(root, directory), { withFileTypes: true })) {
      if (entry.name === 'node_modules') continue;
      const path = `${directory}/${entry.name}`;
      if (entry.isDirectory()) walk(path);
      else if (production_source(path)) paths.push(path);
    }
  };
  walk('server');
  return paths.sort();
}
const covered = (finding: DdlFinding, exception: { path: string }) =>
  exception.path.endsWith('/')
    ? finding.path.startsWith(exception.path)
    : finding.path === exception.path;

it('no production code outside the schema steps and separate databases issues DDL', () => {
  // A runtime CREATE TABLE in the main database blocked schema step 1 on every used database.
  const root = resolve(import.meta.dirname, '../..');
  const findings = server_files(root).flatMap((path) =>
    ddl_findings(path, readFileSync(join(root, path), 'utf8')),
  );
  const uncovered = findings.filter(
    (finding) => !DDL_EXCEPTIONS.some((exception) => covered(finding, exception)),
  );
  expect(uncovered).toEqual([]);
  // An exception that covers nothing is stale, so the list cannot outlive its reason.
  expect(
    DDL_EXCEPTIONS.filter((exception) => !findings.some((finding) => covered(finding, exception))),
  ).toEqual([]);
});

it('the scan reads strings and templates, not comments or prose', () => {
  const text = [
    '// CREATE TABLE in a comment',
    "tx.run('CREATE TABLE IF NOT EXISTS navigation_view_ordered (id TEXT)');",
    'tx.run(`\n  ALTER TABLE goals RENAME TO old_goals`);',
    'tx.run(`DROP INDEX ${name}`);',
    'const probe = `${setup}; db.exec("CREATE TABLE proof(value INTEGER)")`;',
    "const words = ['CREATE', 'ALTER', 'DROP'];",
    "const note = 'Create a ticket, then drop it.';",
    "db.exec('create index x on t(a)');",
  ].join('\n');
  expect(ddl_findings('server/example.ts', text).map((finding) => finding.line)).toEqual([
    2, 3, 5, 6, 9,
  ]);
});
