/** Node runtime archives use the existing bounded TAR format and retained directory writes. */
import { createHash, randomUUID } from 'node:crypto';
import {
  closeSync,
  constants,
  createReadStream,
  fchmodSync,
  fstatSync,
  futimesSync,
  readFileSync,
  writeSync,
  read,
} from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { isMainThread } from 'node:worker_threads';
import { createGunzip } from 'node:zlib';
import { finished } from 'node:stream/promises';
import { ArchiveMembers } from '../../../environments/release/logic/archive_members.ts';
import type { ArchiveMember } from '../../../environments/release/logic/archive_members.ts';
import {
  ARCHIVE_MAX_EXPANDED_BYTES,
  ARCHIVE_STREAM_CHUNK_BYTES,
} from '../../../environments/release/configuration.ts';
import {
  MAX_MAC_RUNTIME_ARCHIVE_BYTES,
  MAX_MAC_RUNTIME_EXPANDED_BYTES,
  MAX_MAC_RUNTIME_MANIFEST_BYTES,
  MAC_RUNTIME_MANIFEST_NAME,
} from '../configuration.ts';
import { close_retained } from '../retained_cleanup.ts';
import {
  cleanup_directory_readers,
  owns_directory_readers,
  directory_open_flags,
  mkdir_relative,
  open_directory_descriptor,
  open_relative,
  read_directory_names,
  rename_relative,
  rmdir_relative,
  symlink_relative,
  sync_directory_descriptor,
  sync_file_descriptor,
  unlink_relative,
} from '../platform.ts';

if (isMainThread) throw new Error('Runtime archive work requires the owned files worker.');
const handles = new Map<number, number>();
const retain = (fd: number) => {
  handles.set(fd, fd);
  return fd;
};
function close(fd: number): void {
  closeSync(fd);
  handles.delete(fd);
}
export function owns_archive_handles(): boolean {
  return handles.size !== 0 || owns_directory_readers();
}
export function cleanup_archive_handles(): void {
  const failures: unknown[] = [];
  try {
    cleanup_directory_readers();
  } catch (error) {
    failures.push(error);
  }
  const failure = close_retained(handles, closeSync);
  if (failure !== undefined) failures.push(failure);
  if (failures.length === 1) throw failures[0];
  if (failures.length) throw new AggregateError(failures, 'Runtime archive cleanup failed.');
}
function owned<T>(fd: number, work: (fd: number) => T): T {
  let value: T;
  try {
    value = work(fd);
  } catch (error) {
    try {
      close(fd);
    } catch (cleanup) {
      throw new AggregateError([error, cleanup], 'Runtime archive cleanup failed.');
    }
    throw error;
  }
  close(fd);
  return value;
}
/** The retained root is borrowed; every newly opened parent stays owned through the operation. */
function at<T>(
  root: number,
  path: string,
  create: boolean,
  work: (parent: number, leaf: string) => T,
): T {
  const parts = path.split('/'),
    leaf = parts.pop()!;
  // Each recursion borrows its explicit parent descriptor.
  const descend = (parent: number, index: number): T => {
    if (index === parts.length) return work(parent, leaf);
    const part = parts[index]!;
    if (create) {
      try {
        mkdir_relative(parent, part, 0o777);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
      }
    }
    const child = retain(open_relative(parent, part, directory_open_flags));
    return owned(child, () => descend(child, index + 1));
  };
  return descend(root, 0);
}
function regular(parent: number, leaf: string): number {
  const fd = retain(
    open_relative(parent, leaf, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK),
  );
  if (!fstatSync(fd).isFile()) {
    close(fd);
    throw new Error('Runtime archive payload must be a regular file.');
  }
  return fd;
}
function revision(root: number, expected: string): void {
  const manifest = owned(regular(root, MAC_RUNTIME_MANIFEST_NAME), (fd) => {
    if (fstatSync(fd).size > MAX_MAC_RUNTIME_MANIFEST_BYTES)
      throw new Error('Runtime manifest exceeds its size limit.');
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(readFileSync(fd))) as Record<
      string,
      unknown
    >;
  });
  if (
    manifest.schema_version !== 1 ||
    manifest.platform !== 'darwin' ||
    manifest.architecture !== 'arm64' ||
    manifest.application_revision !== expected
  )
    throw new Error('Runtime manifest does not match the approved release.');
  at(root, 'node/bin/node', false, (parent, leaf) =>
    owned(regular(parent, leaf), (fd) => {
      if (fstatSync(fd).size === 0 || (fstatSync(fd).mode & 0o111) === 0)
        throw new Error('Runtime Node executable is not executable.');
    }),
  );
  at(root, 'server/executor/mac/entrypoint.ts', false, (parent, leaf) =>
    owned(regular(parent, leaf), (fd) => {
      if (fstatSync(fd).size === 0) throw new Error('Runtime entrypoint is empty.');
    }),
  );
}
export function read_runtime_revision(runtime: string, expected: string): string {
  const root = retain(open_directory_descriptor(runtime));
  owned(root, () => revision(root, expected));
  return expected;
}
/** Destroy waits for pending reads but never closes the descriptor borrowed from this operation. */
function source_stream(fd: number, bytes: number) {
  return createReadStream('', {
    fd,
    autoClose: false,
    start: 0,
    end: bytes,
    highWaterMark: ARCHIVE_STREAM_CHUNK_BYTES,
    fs: { read, close: (_fd, callback) => callback(null) },
  });
}
async function digest(fd: number, bytes: number): Promise<string> {
  const hash = createHash('sha256');
  for await (const chunk of source_stream(fd, bytes)) hash.update(chunk);
  return hash.digest('hex');
}
async function inflated(
  fd: number,
  sha: string,
  bytes: number,
  consume: (bytes: Buffer) => void,
): Promise<void> {
  const source = source_stream(fd, bytes);
  const gunzip = createGunzip({ chunkSize: ARCHIVE_STREAM_CHUNK_BYTES });
  const hash = createHash('sha256');
  let expanded = 0,
    failure: unknown;
  source.on('data', (bytes) => {
    hash.update(bytes);
  });
  source.on('error', (error) => gunzip.destroy(error));
  source.pipe(gunzip);
  try {
    for await (const bytes of gunzip) {
      expanded += bytes.length;
      if (expanded > ARCHIVE_MAX_EXPANDED_BYTES)
        throw new Error('Runtime archive exceeds its expanded size limit.');
      consume(bytes);
    }
    if (hash.digest('hex') !== sha)
      throw new Error('Runtime archive digest differs from approval.');
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    source.destroy();
    gunzip.destroy();
    const settled = await Promise.allSettled([
      finished(source, { cleanup: true }),
      finished(gunzip, { cleanup: true }),
    ]);
    const errors = settled.flatMap((result) =>
      result.status === 'rejected' &&
      result.reason !== failure &&
      result.reason?.code !== 'ERR_STREAM_PREMATURE_CLOSE'
        ? [result.reason]
        : [],
    );
    if (errors.length)
      throw new AggregateError(
        failure === undefined ? errors : [failure, ...errors],
        'Runtime archive stream cleanup failed.',
      );
  }
}
function remove_tree(parent: number, leaf: string): void {
  const root = retain(open_relative(parent, leaf, directory_open_flags));
  owned(root, () => {
    for (const name of read_directory_names(root)) {
      try {
        remove_tree(root, name);
      } catch (error) {
        if (!['ENOTDIR', 'ELOOP'].includes((error as NodeJS.ErrnoException).code ?? ''))
          throw error;
        unlink_relative(root, name);
      }
    }
  });
  rmdir_relative(parent, leaf);
}
function write_all(fd: number, bytes: Buffer): void {
  let offset = 0;
  while (offset < bytes.length) {
    const count = writeSync(fd, bytes, offset, bytes.length - offset);
    if (count === 0) throw new Error('Runtime archive write made no progress.');
    offset += count;
  }
}
function file_mode(mode: number): number {
  let filtered = mode & 0o755;
  if (!(filtered & 0o100)) filtered &= ~0o111;
  return filtered | 0o600;
}
async function extract(
  fd: number,
  sha: string,
  root: number,
  members: readonly ArchiveMember[],
  bytes: number,
): Promise<void> {
  for (const member of members)
    if (member.type === '5')
      at(root, member.path, true, (parent, leaf) => {
        try {
          mkdir_relative(parent, leaf, 0o777);
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
        }
      });
  const files = members.filter((member) => ['0', '\0', '7'].includes(member.type));
  let index = 0,
    position = 0,
    current: number | undefined;
  const complete = () => {
    const member = files[index]!;
    fchmodSync(current!, file_mode(member.mode));
    futimesSync(current!, member.mtime, member.mtime);
    sync_file_descriptor(current!);
    close(current!);
    current = undefined;
    index++;
  };
  try {
    await inflated(fd, sha, bytes, (chunk) => {
      const end = position + chunk.length;
      while (index < files.length) {
        const member = files[index]!;
        if (member.offset > end || (member.offset === end && member.size !== 0)) break;
        if (current === undefined)
          current = at(root, member.path, true, (parent, leaf) =>
            retain(
              open_relative(
                parent,
                leaf,
                constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
                0o600,
              ),
            ),
          );
        const start = Math.max(position, member.offset),
          until = Math.min(end, member.offset + member.size);
        if (until > start) write_all(current, chunk.subarray(start - position, until - position));
        if (member.offset + member.size > end) break;
        complete();
      }
      position = end;
    });
    if (index !== files.length) throw new Error('Runtime archive is truncated.');
  } finally {
    if (current !== undefined) close(current);
  }
  for (const member of members)
    if (member.type === '2')
      at(root, member.path, true, (parent, leaf) =>
        symlink_relative(parent, leaf, member.linkpath),
      );
  for (const member of [...members].reverse())
    if (member.type === '5')
      at(root, member.path, false, (parent, leaf) =>
        owned(retain(open_relative(parent, leaf, directory_open_flags)), (directory) => {
          futimesSync(directory, member.mtime, member.mtime);
        }),
      );
}
function flush_tree(root: number): void {
  for (const name of read_directory_names(root)) {
    let child: number;
    try {
      child = retain(open_relative(root, name, directory_open_flags));
    } catch (error) {
      if (['ENOTDIR', 'ELOOP'].includes((error as NodeJS.ErrnoException).code ?? '')) continue;
      throw error;
    }
    owned(child, () => flush_tree(child));
  }
  sync_directory_descriptor(root);
}
export async function extract_runtime(
  archive: string,
  releases: string,
  sha: string,
  bytes: number,
  expected: string,
): Promise<string> {
  if (
    sha.length !== 64 ||
    !/^[0-9a-f]{64}$/.test(sha) ||
    expected.length !== 40 ||
    !/^[0-9a-f]{40}$/.test(expected) ||
    !Number.isSafeInteger(bytes) ||
    bytes < 1 ||
    bytes > MAX_MAC_RUNTIME_ARCHIVE_BYTES
  )
    throw new TypeError('Invalid runtime archive approval.');
  const parent = retain(open_directory_descriptor(dirname(archive)));
  const input = owned(parent, () => regular(parent, basename(archive)));
  let release: number | undefined,
    stage: number | undefined,
    temporary: string | undefined,
    failure: unknown;
  const destination = expected + '-' + sha;
  try {
    if (fstatSync(input).size !== bytes)
      throw new Error('Runtime archive size differs from approval.');
    try {
      release = retain(open_directory_descriptor(releases));
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    let existing: number | undefined;
    if (release !== undefined)
      try {
        existing = retain(open_relative(release, destination, directory_open_flags));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      }
    if (existing !== undefined) {
      owned(existing, () => revision(existing!, expected));
      if ((await digest(input, bytes)) !== sha)
        throw new Error('Runtime archive digest differs from approval.');
      return join(releases, destination);
    }
    const parser = new ArchiveMembers('extraction');
    let total = 0,
      seen = 0;
    await inflated(input, sha, bytes, (chunk) => {
      parser.feed(chunk);
      while (seen < parser.members.length) {
        total += parser.members[seen++]!.size;
        if (total > MAX_MAC_RUNTIME_EXPANDED_BYTES)
          throw new Error('Runtime archive exceeds its expanded size limit.');
      }
    });
    parser.finish();
    for (const member of parser.members) {
      if (!member.path || member.path.includes('\\') || member.type === '1')
        throw new Error('Runtime archive contains an unsafe member.');
    }
    release ??= retain(open_directory_descriptor(releases, { create: true }));
    temporary = '.install-' + randomUUID();
    mkdir_relative(release, temporary);
    stage = retain(open_relative(release, temporary, directory_open_flags));
    await extract(input, sha, stage, parser.members, bytes);
    revision(stage, expected);
    flush_tree(stage);
    rename_relative(release, temporary, release, destination);
    temporary = undefined;
    sync_directory_descriptor(release);
    return join(releases, destination);
  } catch (error) {
    failure = error;
    throw error;
  } finally {
    const failures: unknown[] = [];
    if (stage !== undefined)
      try {
        close(stage);
      } catch (error) {
        failures.push(error);
      }
    if (temporary !== undefined && release !== undefined)
      try {
        remove_tree(release, temporary);
      } catch (error) {
        failures.push(error);
      }
    if (release !== undefined)
      try {
        close(release);
      } catch (error) {
        failures.push(error);
      }
    try {
      close(input);
    } catch (error) {
      failures.push(error);
    }
    if (failures.length)
      throw new AggregateError(
        failure === undefined ? failures : [failure, ...failures],
        'Runtime archive cleanup failed.',
      );
  }
}
