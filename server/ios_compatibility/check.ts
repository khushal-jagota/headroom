/** A human review must bind the accepted native-facing inventory and build floor. */
import { createHash } from 'node:crypto';
import { readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { strip } from '../core/whitespace.ts';
import { parse_attempt_json, is_object } from '../environments/updates/codec.ts';
import { python_json_equal } from '../environments/release/logic/manifest.ts';
import { MINIMUM_SUPPORTED_BUILD } from './policy.ts';

export const REVIEW_PATH = 'server/ios_compatibility/review.json';
const excluded_components = new Set(['node_modules', 'tests', 'testing']);
const excluded_paths = new Set([
  'server/test_host.ts',
  'server/vitest.config.ts',
  'server/executor/tools',
]);
const fixed_paths = [
  'server/package.json',
  'server/package-lock.json',
  'server/core/database/application_schema.json',
  'server/skills/cli_commands.json',
  'server/environments/release/migration_graph.json',
  'src/planner/conversation/backends/hermes_acp_extension.py',
  'src/planner/conversation/backends/hermes_model_catalog.py',
];

function compare_components(left: string, right: string): number {
  const a = Array.from(left, (character) => character.codePointAt(0)!);
  const b = Array.from(right, (character) => character.codePointAt(0)!);
  for (let index = 0; index < Math.min(a.length, b.length); index++)
    if (a[index] !== b[index]) return a[index]! - b[index]!;
  return a.length - b.length;
}
/** Python Path order compares components, then Unicode code points within each one. */
function compare_paths(left: string, right: string): number {
  const a = left.split('/'),
    b = right.split('/');
  for (let index = 0; index < Math.min(a.length, b.length); index++) {
    const order = compare_components(a[index]!, b[index]!);
    if (order) return order;
  }
  return a.length - b.length;
}

/** Relative POSIX names. Mandatory fixed inputs never disappear when files are absent. */
export async function contract_paths(root: string): Promise<readonly string[]> {
  const paths = [...fixed_paths];
  async function visit(directory: string): Promise<void> {
    for (const entry of await readdir(join(root, directory), { withFileTypes: true })) {
      if (excluded_components.has(entry.name)) continue;
      const path = directory + '/' + entry.name;
      if (excluded_paths.has(path)) continue;
      if (entry.isDirectory()) await visit(path);
      else if (entry.name.endsWith('.ts') && !entry.name.endsWith('.test.ts')) paths.push(path);
    }
  }
  await visit('server');
  return paths.sort(compare_paths);
}

/** Every comment, newline and other source byte contributes without normalization. */
export async function contract_digest(root: string): Promise<string> {
  const digest = createHash('sha256');
  for (const path of await contract_paths(root)) {
    digest.update(path + '\0', 'utf8');
    digest.update(await readFile(join(root, path)));
    digest.update('\0');
  }
  return digest.digest('hex');
}

/** Read only. Missing inputs or a missing human record cannot authorize publication. */
export async function check(root: string, review_path = join(root, REVIEW_PATH)): Promise<void> {
  const review = parse_attempt_json(
    new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readFile(review_path)),
  );
  if (!is_object(review)) throw new Error('iOS compatibility review must be an object');
  if (review.contract_sha256 !== (await contract_digest(root)))
    throw new Error(
      'Native-facing contracts changed. Review iOS compatibility and refresh the source-bound review.',
    );
  if (!python_json_equal(review.oldest_supported_build, MINIMUM_SUPPORTED_BUILD))
    throw new Error("Review must name the policy's oldest supported build.");
  if (review.decision !== 'compatible' && review.decision !== 'minimum_raised')
    throw new Error('Record compatible or minimum_raised after review.');
  if (typeof review.evidence !== 'string' || Array.from(strip(review.evidence)).length < 40)
    throw new Error(
      'Record concrete check evidence, or the break that required a minimum increase.',
    );
}
