/** Immutable snapshots and bounded byte transfers, relative to retained directory inodes. */
import { constants, fstatSync, readSync, writeSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { basename, dirname } from 'node:path';
import { isMainThread } from 'node:worker_threads';
import {
  file_error as invalid,
  error_code,
  file_version,
  version,
  same_version,
} from './file_metadata.ts';
import type { FileVersion } from './file_metadata.ts';
import { FILE_CHUNK_BYTES } from './configuration.ts';
import { FILE_MODIFIED_WINDOW_NS } from './configuration.ts';
import type { CapturedBlob } from './file_worker_contracts.ts';
import {
  close_native_descriptor,
  open_directory_descriptor,
  open_relative,
  rename_relative,
  require_relative_component,
  sync_directory_descriptor,
  sync_file_descriptor,
  unlink_relative,
} from './platform.ts';

if (isMainThread)
  throw new Error('Executor file operations require the owned FilesService worker.');
/** The caller owns the returned descriptor. Reject '..' before path normalization. */
export const directory_descriptor = open_directory_descriptor;
export function regular_descriptor(path: string): number {
  const parent = directory_descriptor(dirname(path));
  let descriptor: number;
  try {
    descriptor = open_relative(
      parent,
      basename(path),
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
  } catch (error) {
    close_native_descriptor(parent);
    throw error;
  }
  try {
    close_native_descriptor(parent);
  } catch (error) {
    close_native_descriptor(descriptor);
    throw error;
  }
  try {
    if (!fstatSync(descriptor).isFile()) throw invalid('artifact is not regular file');
    return descriptor;
  } catch (error) {
    close_native_descriptor(descriptor);
    throw error;
  }
}
export function fsync_file(descriptor: number): void {
  sync_file_descriptor(descriptor);
}
export function fsync_directory(path: string): void {
  const descriptor = directory_descriptor(path);
  try {
    sync_directory_descriptor(descriptor);
  } finally {
    close_native_descriptor(descriptor);
  }
}
export function copy_hashed(
  incoming: number,
  outgoing: number,
  options: { byte_limit?: number; skip_unreadable_source?: boolean } = {},
): readonly [string, number] {
  const digest = createHash('sha256');
  const buffer = Buffer.allocUnsafe(FILE_CHUNK_BYTES);
  let count = 0;
  for (;;) {
    let read: number;
    try {
      read = readSync(incoming, buffer, 0, buffer.length, null);
    } catch (error) {
      if (
        options.skip_unreadable_source &&
        ['ENOENT', 'ENOTDIR', 'EACCES', 'EPERM'].includes(error_code(error) ?? '')
      )
        return ['', -1];
      throw error;
    }
    if (read === 0) return [digest.digest('hex'), count];
    count += read;
    if (options.byte_limit !== undefined && count > options.byte_limit)
      throw invalid('file exceeds manifest length');
    digest.update(buffer.subarray(0, read));
    let offset = 0;
    while (offset < read) {
      const written = writeSync(outgoing, buffer, offset, read - offset, null);
      if (!written)
        throw Object.assign(new Error('file write made no progress'), { name: 'OSError' });
      offset += written;
    }
  }
}
/** Copy into the held destination parent. The source is opened without ancestor symlinks. */
export function copy_verified_at(
  source: string,
  parent: number,
  name: string,
  sha256: string,
  byte_count: number,
): void {
  const incoming = regular_descriptor(source);
  try {
    const outgoing = open_relative(
      parent,
      name,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
      0o600,
    );
    try {
      try {
        const [digest, count] = copy_hashed(incoming, outgoing, { byte_limit: byte_count });
        if (count !== byte_count || digest !== sha256) throw invalid('incomplete or corrupt file');
        fsync_file(outgoing);
      } catch (error) {
        unlink_relative(parent, name);
        throw error;
      }
    } finally {
      close_native_descriptor(outgoing);
    }
  } finally {
    close_native_descriptor(incoming);
  }
}
export function copy_verified(
  source: string,
  destination: string,
  sha256: string,
  byte_count: number,
): void {
  const parent = directory_descriptor(dirname(destination));
  try {
    copy_verified_at(source, parent, basename(destination), sha256, byte_count);
  } finally {
    close_native_descriptor(parent);
  }
}
export function install_verified_at(
  source: string,
  parent: number,
  name: string,
  sha256: string,
  byte_count: number,
): void {
  require_relative_component(name);
  const temporary = '.tmp-' + randomUUID().replaceAll('-', '');
  try {
    copy_verified_at(source, parent, temporary, sha256, byte_count);
    rename_relative(parent, temporary, parent, name);
    sync_directory_descriptor(parent);
  } finally {
    unlink_relative(parent, temporary, true);
  }
}
export function install_verified(
  source: string,
  target: string,
  sha256: string,
  byte_count: number,
): void {
  require_relative_component(basename(target));
  const parent = directory_descriptor(dirname(target), { create: true });
  try {
    install_verified_at(source, parent, basename(target), sha256, byte_count);
  } finally {
    close_native_descriptor(parent);
  }
}
function hash_descriptor(incoming: number): readonly [string, number] {
  const digest = createHash('sha256');
  const buffer = Buffer.allocUnsafe(FILE_CHUNK_BYTES);
  let count = 0;
  for (;;) {
    const read = readSync(incoming, buffer, 0, buffer.length, null);
    if (read === 0) return [digest.digest('hex'), count];
    digest.update(buffer.subarray(0, read));
    count += read;
  }
}
export function verified_version(
  path: string,
  sha256: string,
  byte_count: number,
): FileVersion | null {
  let incoming: number;
  try {
    incoming = regular_descriptor(path);
  } catch (error) {
    if (error_code(error) === 'ENOENT') return null;
    throw error;
  }
  let before: FileVersion, after: FileVersion, digest: string, count: number;
  try {
    before = version(fstatSync(incoming, { bigint: true }));
    [digest, count] = hash_descriptor(incoming);
    after = version(fstatSync(incoming, { bigint: true }));
  } finally {
    close_native_descriptor(incoming);
  }
  if (!same_version(before, after) || digest !== sha256 || count !== byte_count) return null;
  return same_version(file_version(path), after) ? after : null;
}
export function file_digest(path: string): readonly [string, number] {
  const incoming = regular_descriptor(path);
  try {
    return hash_descriptor(incoming);
  } finally {
    close_native_descriptor(incoming);
  }
}

/** Capture once into the spool. Source failures skip; destination failures remain fatal. */
export function capture_file(
  source: string,
  blobs: number,
  checked_ns: bigint,
): CapturedBlob | null {
  let incoming: number;
  try {
    incoming = regular_descriptor(source);
  } catch (error) {
    if (
      ['ENOENT', 'ENOTDIR', 'EACCES', 'EPERM'].includes(error_code(error) ?? '') ||
      (error as Error).name === 'ValueError'
    )
      return null;
    throw error;
  }
  const temporary = '.tmp-' + randomUUID().replaceAll('-', '');
  let created = false;
  try {
    const outgoing = open_relative(
      blobs,
      temporary,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
      0o600,
    );
    created = true;
    let captured: CapturedBlob;
    try {
      const before = fstatSync(incoming, { bigint: true });
      const [sha256, count] = copy_hashed(incoming, outgoing, { skip_unreadable_source: true });
      const after = fstatSync(incoming, { bigint: true });
      if (
        before.size !== after.size ||
        before.mtimeNs !== after.mtimeNs ||
        before.ctimeNs !== after.ctimeNs ||
        BigInt(count) !== after.size
      )
        return null;
      fsync_file(outgoing);
      captured = {
        sha256,
        byte_count: BigInt(count),
        modified_ns:
          checked_ns - after.mtimeNs <= BigInt(FILE_MODIFIED_WINDOW_NS) ? -1n : after.mtimeNs,
      };
    } finally {
      close_native_descriptor(outgoing);
    }
    let exists = false;
    try {
      const existing = open_relative(
        blobs,
        captured.sha256,
        constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
      );
      try {
        if (!fstatSync(existing).isFile()) throw invalid('artifact is not regular file');
        exists = true;
      } finally {
        close_native_descriptor(existing);
      }
    } catch (error) {
      if (error_code(error) !== 'ENOENT') throw error;
    }
    if (!exists) rename_relative(blobs, temporary, blobs, captured.sha256);
    sync_directory_descriptor(blobs);
    return captured;
  } finally {
    try {
      if (created) unlink_relative(blobs, temporary, true);
    } finally {
      close_native_descriptor(incoming);
    }
  }
}
