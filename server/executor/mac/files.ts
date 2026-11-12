/** Immutable snapshots and bounded byte transfers, relative to retained directory inodes. */
import { closeSync, constants, fstatSync, lstatSync, openSync, readSync, writeSync } from 'node:fs';
import type { BigIntStats } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { basename, dirname, isAbsolute, join, relative } from 'node:path';
import { resolve_path_sync } from '../../core/path_resolution.ts';
import { FILE_CHUNK_BYTES } from './configuration.ts';
import {
  directory_open_flags,
  mkdir_relative,
  open_relative,
  rename_relative,
  require_relative_component,
  sync_directory_descriptor,
  sync_file_descriptor,
  unlink_relative,
} from './platform.ts';

/** This is local metadata, not an executor wire type. */
export type FileVersion = readonly [bigint, bigint, bigint, bigint, bigint];
function invalid(message: string): Error {
  return Object.assign(new Error(message), { name: 'ValueError' });
}
function error_code(error: unknown): string | undefined {
  return (error as NodeJS.ErrnoException).code;
}
export function safe_component(value: string): string {
  if (!value || /[^A-Za-z0-9_-]/.test(value)) throw invalid('unsafe executor identifier');
  return value;
}
function is_symlink(path: string): boolean {
  // pathlib's is_symlink returns false for an embedded NUL. resolve then refuses it.
  if (path.includes('\0')) return false;
  try {
    return lstatSync(path).isSymbolicLink();
  } catch (error) {
    if (['ENOENT', 'ENOTDIR'].includes(error_code(error) ?? '')) return false;
    throw error;
  }
}
/** A lexical/preflight refusal only. All later byte I/O still uses retained descriptors. */
export function contained_path(root: string, relative_path: string): string {
  if (
    !relative_path ||
    relative_path.includes('\\') ||
    /%(25|2e|2f|5c)/i.test(relative_path) ||
    relative_path.split('/').some((part) => ['', '.', '..'].includes(part))
  )
    throw invalid('unsafe relative artifact path');
  if (is_symlink(root)) throw invalid('artifact root is symlink');
  let current = root;
  for (const part of relative_path.split('/')) {
    current = join(current, part);
    if (is_symlink(current)) throw invalid('artifact path contains symlink');
  }
  if (current.includes('\0')) throw invalid('lstat: embedded null character in path');
  const rel = relative(resolve_path_sync(root), resolve_path_sync(current));
  if (rel === '..' || rel.startsWith('../') || isAbsolute(rel))
    throw invalid('artifact path escapes root');
  return current;
}
/** The caller owns the returned descriptor. Reject '..' before path normalization. */
export function directory_descriptor(path: string, options: { create?: boolean } = {}): number {
  if (path.includes('\0')) throw invalid('embedded null byte');
  const raw_parts = path.split('/');
  if (raw_parts.includes('..')) throw invalid('unsafe directory component');
  const parts = (isAbsolute(path) ? raw_parts : [...process.cwd().split('/'), ...raw_parts]).filter(
    (part) => part !== '' && part !== '.',
  );
  let descriptor = openSync('/', directory_open_flags);
  try {
    for (const part of parts) {
      if (options.create) {
        try {
          mkdir_relative(descriptor, part);
          sync_file_descriptor(descriptor);
        } catch (error) {
          if (error_code(error) !== 'EEXIST') throw error;
        }
      }
      const child = open_relative(descriptor, part, directory_open_flags);
      const previous = descriptor;
      descriptor = child;
      closeSync(previous);
    }
    return descriptor;
  } catch (error) {
    closeSync(descriptor);
    throw error;
  }
}
export function regular_descriptor(path: string): number {
  const parent = directory_descriptor(dirname(path));
  let descriptor: number;
  try {
    descriptor = open_relative(parent, basename(path), constants.O_RDONLY | constants.O_NOFOLLOW);
  } catch (error) {
    closeSync(parent);
    throw error;
  }
  try {
    closeSync(parent);
  } catch (error) {
    closeSync(descriptor);
    throw error;
  }
  try {
    if (!fstatSync(descriptor).isFile()) throw invalid('artifact is not regular file');
    return descriptor;
  } catch (error) {
    closeSync(descriptor);
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
    closeSync(descriptor);
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
      closeSync(outgoing);
    }
  } finally {
    closeSync(incoming);
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
    closeSync(parent);
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
    closeSync(parent);
  }
}
function version(status: BigIntStats): FileVersion {
  return [status.dev, status.ino, status.size, status.mtimeNs, status.ctimeNs];
}
function same_version(one: FileVersion | null, other: FileVersion): boolean {
  return one !== null && one.every((value, index) => value === other[index]);
}
/** Python uses metadata-only lstat here. Byte I/O never uses this path-based observation. */
export function file_version(path: string): FileVersion | null {
  try {
    const status = lstatSync(path, { bigint: true });
    return status.isFile() ? version(status) : null;
  } catch (error) {
    if (error_code(error) === 'ENOENT') return null;
    throw error;
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
    closeSync(incoming);
  }
  if (!same_version(before, after) || digest !== sha256 || count !== byte_count) return null;
  return same_version(file_version(path), after) ? after : null;
}
export function file_digest(path: string): readonly [string, number] {
  const incoming = regular_descriptor(path);
  try {
    return hash_descriptor(incoming);
  } finally {
    closeSync(incoming);
  }
}
