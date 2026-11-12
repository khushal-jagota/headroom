/** Fresh Python comparison companion. This module starts no service or connection. */
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  contained_path,
  copy_verified,
  file_digest,
  file_version,
  install_verified,
  safe_component,
  verified_version,
} from '../files.ts';
import { executor_paths, validate_server_url } from '../configuration.ts';
import * as configuration from '../configuration.ts';

function result(action: () => unknown): unknown {
  try {
    return { value: action() };
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    return { error: { name: error.name, message: error.message } };
  }
}
export async function files_parity(input: {
  root: string;
  urls: string[];
  components: string[];
  relatives: string[];
  roots: string[];
}): Promise<unknown> {
  const root = join(input.root, 'ts');
  const source = join(root, 'source');
  const [sha, count] = file_digest(source);
  const copy = join(root, 'copy');
  copy_verified(source, copy, sha, count);
  const target = join(root, 'nested', 'target');
  install_verified(copy, target, sha, count);
  const version = file_version(target);
  const verified = verified_version(target, sha, count);
  let corrupt_refused = false;
  try {
    copy_verified(source, join(root, 'bad-copy'), '0'.repeat(64), count);
  } catch {
    corrupt_refused = true;
  }
  const old = readFileSync(target);
  try {
    install_verified(source, target, sha, count - 1);
  } catch {
    // A refused replacement must leave the published inode's bytes intact.
  }
  const paths = [];
  for (const configured of input.roots) {
    const resolved = await executor_paths({ ...process.env, PLAN_EXECUTOR_ROOT: configured });
    paths.push([
      resolved.root,
      resolved.workspace,
      resolved.spool,
      resolved.artifacts,
      resolved.message_files_anchor,
      resolved.skills,
      resolved.run,
      resolved.ipc_socket,
    ]);
  }
  return {
    settings: Object.fromEntries(
      Object.entries(configuration).filter(([name]) => /^[A-Z_]+$/.test(name)),
    ),
    digest: [sha, count],
    copied: readFileSync(copy).equals(readFileSync(source)),
    installed: file_digest(target),
    verified: version !== null && verified !== null && version.every((v, i) => v === verified[i]),
    corrupt_refused,
    corrupt_cleaned: !existsSync(join(root, 'bad-copy')),
    old_preserved: old.equals(readFileSync(target)),
    files: (await import('node:fs')).readdirSync(join(root, 'nested')).sort(),
    components: input.components.map((value) => result(() => safe_component(value))),
    relatives: input.relatives.map((value) =>
      result(() => contained_path(root, value).slice(root.length)),
    ),
    urls: input.urls.map((value) => result(() => validate_server_url(value))),
    paths,
  };
}
