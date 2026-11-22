/** Read a stopped, checkpointed server folder. This module never copies or changes it. */
import { lstat } from 'node:fs/promises';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import { path_is_within } from '../../core/path_containment.ts';
import { is_private_ticket_name } from '../../files/logic/paths.ts';
import { manifests_equal, relative_manifest, sha256_file } from './checksums.ts';
import type { ChecksumManifest, ServerStatePaths } from './contracts.ts';

async function info(path: string) {
  try {
    return await lstat(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
}

/** Only actual served files belong to a move. Ticket checkouts stay on their machine. */
export function excluded_served_path(path: string): boolean {
  const parts = path.split('/');
  return (
    (parts[0] === 'tickets' && parts.length >= 3 && is_private_ticket_name(parts[2]!)) ||
    (parts[0] === 'conversations' && parts.length === 3 && /^\.[0-9a-f]{32}$/.test(parts[2]!))
  );
}

export async function server_state_manifest(paths: ServerStatePaths): Promise<ChecksumManifest> {
  const root = resolve(paths.server_root);
  const database = resolve(paths.database_path);
  const key = resolve(paths.credential_key_path);
  const secrets = join(root, 'secrets');
  for (const configured of [paths.apns_key_path, paths.update_token_path]) {
    if (configured == null) continue;
    const path = resolve(configured);
    if (!path_is_within(secrets, path) || path === secrets)
      throw new Error('Put configured push and update secrets within the server secrets folder');
  }
  if (dirname(database) !== root)
    throw new Error('The database must be directly within the server folder');
  if (!path_is_within(root, key) || key === root)
    throw new Error('The credential key must be within the server folder');
  if (!(await info(root))?.isDirectory())
    throw new Error('The server folder must be a real directory');
  const wal = await info(database + '-wal');
  if (wal && (!wal.isFile() || wal.size !== 0))
    throw new Error('Stop the server and checkpoint its database before the full checksum');
  const manifest: ChecksumManifest = Object.create(null);
  async function file(path: string, required = false): Promise<void> {
    const present = await info(path);
    if (!present && !required) return;
    if (!present?.isFile()) throw new Error(`Server state is not a regular file: ${path}`);
    manifest[relative(root, path).split(sep).join('/')] = await sha256_file(path);
  }
  await file(database, true);
  // Check each ancestor, so a configured key cannot escape through a directory link.
  let ancestor = dirname(key);
  while (ancestor !== root) {
    const present = await info(ancestor);
    if (present && !present.isDirectory())
      throw new Error(`Server state is not a real directory: ${ancestor}`);
    ancestor = dirname(ancestor);
  }
  await file(key);
  for (const name of ['files', 'skills', 'secrets'] as const) {
    const directory = join(root, name);
    if (!(await info(directory))) continue;
    const tree = await relative_manifest(directory, {
      strict: true,
      exclude:
        name === 'files'
          ? excluded_served_path
          : name === 'skills'
            ? (path) =>
                path === '.publication.lock' ||
                /^\..+\.tmp-\d+-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
                  basename(path),
                )
            : undefined,
    });
    for (const [path, digest] of Object.entries(tree)) manifest[`${name}/${path}`] = digest;
  }
  for (const configured of [paths.apns_key_path, paths.update_token_path])
    if (configured != null && !(await info(resolve(configured)))?.isFile())
      throw new Error(`Configured server secret is not a regular file: ${configured}`);
  return Object.fromEntries(
    Object.entries(manifest).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  );
}

export async function server_state_valid(
  paths: ServerStatePaths,
  recorded: unknown,
): Promise<boolean> {
  return manifests_equal(await server_state_manifest(paths), recorded);
}
