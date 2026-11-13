/** Immutable file-backed archives. The checksum is checked before any extraction. */
import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { chmod, lstat, mkdir, open, readlink, stat } from 'node:fs/promises';
import { isAbsolute, join, posix, relative } from 'node:path';
import { pipeline } from 'node:stream/promises';
import { Pack, Parser, extract } from 'tar';
import type { ReadEntry } from 'tar';
import type { AppManifest } from './contracts.ts';
import { AppValidationError } from './contracts.ts';
import { ARCHIVE_GZIP_LEVEL, FILE_CHUNK_BYTES } from './configuration.ts';
import { app_tree_paths, validate_app_manifest } from './identity.ts';
import { resolved_path } from '../paths.ts';
export { APP_ARCHIVE_NAME, PUBLICATION_RECEIPT_NAME } from './configuration.ts';

type Member = Pick<ReadEntry, 'path' | 'type' | 'mode' | 'linkpath'>;
function refuse(message: string): never {
  throw new AppValidationError(message);
}
async function exists(path: string): Promise<boolean> {
  try {
    await stat(path);
    return true;
  } catch (error) {
    if (
      error instanceof Error &&
      'code' in error &&
      (error.code === 'ENOENT' || error.code === 'ENOTDIR')
    )
      return false;
    throw error;
  }
}
export async function hash_archive(path: string): Promise<string> {
  const digest = createHash('sha256');
  for await (const chunk of createReadStream(path, { highWaterMark: FILE_CHUNK_BYTES }))
    digest.update(chunk);
  return digest.digest('hex');
}
export async function create_app_archive(app: string, archive: string): Promise<string> {
  await validate_app_manifest(join(app, 'manifest.json'), { require_runtime: true });
  const root = await resolved_path(app),
    target = await resolved_path(archive);
  const suffix = relative(root, target);
  if (suffix === '' || (suffix !== '..' && !suffix.startsWith('../') && !isAbsolute(suffix)))
    refuse('archive must be outside the candidate app');
  const paths = await app_tree_paths(root);
  for (const path of paths) {
    const info = await lstat(path);
    if (info.isSymbolicLink() && isAbsolute(await readlink(path)))
      refuse('app archive requires relative symlinks');
    if (info.mode & 0o7777 & ~0o777) refuse('app archive contains special permission bits');
  }
  const pack = new Pack({
    cwd: root,
    gzip: { level: ARCHIVE_GZIP_LEVEL },
    strict: true,
    noDirRecurse: true,
  });
  // Pack.add does not treat an ordinary filename beginning with '@' as another archive.
  const writing = pipeline(pack, createWriteStream(archive, { flags: 'wx' }));
  for (const path of paths) pack.add(relative(root, path));
  pack.end();
  await writing;
  return hash_archive(archive);
}
async function archive_members(archive: string): Promise<Member[]> {
  const handle = await open(archive, 'r');
  try {
    const magic = Buffer.alloc(2);
    await handle.read(magic, 0, magic.length, 0);
    if (!magic.equals(Buffer.from([0x1f, 0x8b]))) refuse('app archive is not gzip data');
  } finally {
    await handle.close();
  }
  const members: Member[] = [];
  const capture = (entry: ReadEntry): void => {
    if (entry.meta) return;
    members.push({
      path: entry.path,
      type: entry.type,
      // ReadEntry masks the header bits. Admission checks the original mode first.
      mode: entry.extended?.mode ?? entry.globalExtended?.mode ?? entry.header.mode,
      linkpath: entry.linkpath,
    });
  };
  const parser = new Parser({
    strict: true,
    maxMetaEntrySize: Infinity,
    maxDecompressionRatio: Infinity,
  });
  parser.on('entry', (entry: ReadEntry) => {
    capture(entry);
    entry.resume();
  });
  // The library intentionally ignores unsupported types. Our admission must refuse them.
  parser.on('ignoredEntry', capture);
  await new Promise<void>((resolve, reject) => {
    const input = createReadStream(archive, { highWaterMark: FILE_CHUNK_BYTES });
    parser.once('error', (error: Error) => {
      input.destroy();
      reject(error);
    });
    parser.once('end', resolve);
    input.once('error', (error: Error) => parser.abort(error));
    input.pipe(parser);
  });
  return members;
}
function validate_members(members: readonly Member[]): void {
  const names = new Set<string>(),
    links = new Map<string, string>();
  for (const member of members) {
    if (
      posix.isAbsolute(member.path) ||
      member.path.split('/').includes('..') ||
      names.has(member.path)
    )
      refuse('app archive contains unsafe or duplicate paths');
    names.add(member.path);
    if (
      !['Directory', 'File', 'OldFile', 'ContiguousFile', 'SymbolicLink', 'Link'].includes(
        member.type,
      )
    )
      refuse('app archive contains unsupported entries');
    if (
      member.mode === undefined ||
      !Number.isSafeInteger(member.mode) ||
      member.mode < 0 ||
      member.mode > 0o777
    )
      refuse('app archive contains special permission bits');
    if (member.type === 'SymbolicLink')
      links.set(posix.normalize(member.path), member.linkpath ?? '');
  }
  // Resolve link chains as paths, before there is a destination directory to mutate.
  function checked(
    parts: readonly string[],
    base: string[] = [],
    active = new Set<string>(),
  ): string[] {
    const current = [...base];
    for (const part of parts) {
      if (!part || part === '.') continue;
      if (part === '..') {
        if (!current.length) refuse('app archive contains an outside link');
        current.pop();
        continue;
      }
      current.push(part);
      const name = current.join('/'),
        target = links.get(name);
      if (target === undefined) continue;
      if (posix.isAbsolute(target) || active.has(name))
        refuse('app archive contains an outside link');
      active.add(name);
      const resolved = checked(target.split('/'), current.slice(0, -1), active);
      active.delete(name);
      current.splice(0, current.length, ...resolved);
    }
    return current;
  }
  for (const member of members) {
    checked(member.path.split('/'));
    if (member.type === 'SymbolicLink' || member.type === 'Link') {
      if (!member.linkpath || posix.isAbsolute(member.linkpath))
        refuse('app archive contains an outside link');
      checked(
        member.linkpath.split('/'),
        member.type === 'SymbolicLink'
          ? posix
              .dirname(member.path)
              .split('/')
              .filter((part) => part !== '.')
          : [],
      );
    }
  }
}
export async function extract_app_archive(
  archive: string,
  destination: string,
  archive_sha256: string,
  expected_sha: string,
): Promise<AppManifest> {
  if ((await hash_archive(archive)) !== archive_sha256)
    refuse('app archive hash differs from publication receipt');
  if (await exists(destination)) refuse('archive destination must not exist');
  const members = await archive_members(archive);
  validate_members(members);
  await mkdir(destination, { recursive: true });
  await extract({
    file: archive,
    cwd: destination,
    strict: true,
    preservePaths: false,
    preserveOwner: false,
    chmod: true,
    processUmask: 0,
    umask: 0,
    maxDepth: Infinity,
    maxMetaEntrySize: Infinity,
    maxDecompressionRatio: Infinity,
  });
  // node-tar adds owner access to directory entries. Restore ordinary archive modes last.
  const ordinary = members.filter((member) => member.type !== 'SymbolicLink');
  for (const member of [...ordinary].sort(
    (a, b) => b.path.split('/').length - a.path.split('/').length,
  ))
    await chmod(join(destination, member.path), member.mode!);
  for (const member of ordinary)
    if (((await stat(join(destination, member.path))).mode & 0o7777) !== member.mode)
      refuse('app archive did not preserve permissions');
  return validate_app_manifest(join(destination, 'manifest.json'), {
    expected_sha,
    require_runtime: true,
    verify_artifact_digest: false,
  });
}
