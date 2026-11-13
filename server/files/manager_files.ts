/** Shared Manager inventory for file routes and the Manager workspace. */
import { readdir, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { ownerRoot } from './managed_files.ts';
import { foldDirectory } from './logic/listing.ts';
import { symlink } from './logic/paths.ts';
import { JsonNumber, type NumericJSON } from '../core/http/numeric_json.ts';
import type { ArtifactEntry } from './contracts.ts';
export const ARTIFACTS_DIRECTORY = 'artifacts';
export async function list_files(
  db: string,
  manager: string,
): Promise<{ path: string; modified_at: number }[]> {
  const root = ownerRoot(db, 'manager', manager, false);
  if (!root) return [];
  const result: { path: string; modified_at: number }[] = [];
  async function visit(directory: string, prefix: string): Promise<void> {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const disk = join(directory, entry.name),
        path = prefix + entry.name;
      if (symlink(disk)) continue;
      if (entry.isFile()) result.push({ path, modified_at: (await stat(disk)).mtimeMs / 1000 });
      else if (entry.isDirectory()) await visit(disk, path + '/');
    }
  }
  await visit(root, '');
  return result;
}
function artifact_json(entry: ArtifactEntry): NumericJSON<ArtifactEntry> {
  return {
    name: entry.name,
    opens: entry.opens,
    modified_at: new JsonNumber(
      Object.is(entry.modified_at, -0) ? '-0.0' : entry.modified_at.toExponential(),
    ),
    children: entry.children.map(artifact_json),
  };
}
export async function list_artifact_entries(db: string, manager: string) {
  const root = ownerRoot(db, 'manager', manager, false);
  return root
    ? (await foldDirectory(join(root, ARTIFACTS_DIRECTORY), ARTIFACTS_DIRECTORY + '/')).map(
        artifact_json,
      )
    : [];
}
