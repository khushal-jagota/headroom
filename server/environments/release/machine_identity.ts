/** Machine bytes have an identity independent of the release that carries them. */
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, readFile, readlink } from 'node:fs/promises';
import { relative, sep } from 'node:path';
import { app_tree_paths } from './identity.ts';

/** Only these provenance fields are excluded. Runtime contract and installer files remain bound. */
const provenance_fields: Readonly<Record<string, readonly string[]>> = {
  'mac-runtime.json': ['application_revision', 'build_id'],
  'manifest.json': ['app_sha', 'source_digest', 'artifact_digest'],
  'runtime/installer-files.json': ['source_sha'],
};

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value !== null && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => (left < right ? -1 : left > right ? 1 : 0))
        .map(([key, item]) => [key, canonical(item)]),
    );
  return value;
}

async function digest_machine_tree(root: string, entries?: Map<string, string>): Promise<string> {
  const digest = createHash('sha256');
  for (const path of await app_tree_paths(root)) {
    const name = relative(root, path).split(sep).join('/'),
      info = await lstat(path);
    const entry = entries === undefined ? undefined : createHash('sha256');
    const update = (bytes: string | Buffer) => {
      digest.update(bytes);
      entry?.update(bytes);
    };
    if (info.isSymbolicLink()) {
      update(JSON.stringify([name, 'symlink', await readlink(path)]) + '\n');
    } else if (info.isDirectory()) {
      update(JSON.stringify([name, 'directory', info.mode & 0o111]) + '\n');
    } else if (info.isFile()) {
      const excluded = provenance_fields[name];
      if (excluded !== undefined) {
        const metadata = JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>;
        for (const key of excluded) delete metadata[key];
        update(JSON.stringify([name, 'metadata', info.mode & 0o111, canonical(metadata)]) + '\n');
      } else {
        update(JSON.stringify([name, 'file', info.mode & 0o111, info.size]) + '\n');
        for await (const bytes of createReadStream(path)) update(bytes);
        update('\n');
      }
    } else throw new Error(`Unsupported machine payload entry: ${name}`);
    if (entry !== undefined) entries!.set(name, entry.digest('hex'));
  }
  return digest.digest('hex');
}

export function digest_machine_build(root: string): Promise<string> {
  return digest_machine_tree(root);
}

/** Failure diagnostics bind the same bytes as the identity and expose no payload contents. */
export async function machine_build_differences(
  first: string,
  repeated: string,
): Promise<string[]> {
  const left = new Map<string, string>(),
    right = new Map<string, string>();
  await digest_machine_tree(first, left);
  await digest_machine_tree(repeated, right);
  return [...new Set([...left.keys(), ...right.keys()])]
    .sort()
    .filter((name) => left.get(name) !== right.get(name))
    .map(
      (name) =>
        `${name}: first=${left.get(name) ?? 'missing'} repeated=${right.get(name) ?? 'missing'}`,
    );
}
