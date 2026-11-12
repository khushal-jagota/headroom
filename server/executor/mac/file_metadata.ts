/** Cheap local validation and metadata observations. No byte copies or full flushes. */
import { lstatSync } from 'node:fs';
import type { BigIntStats } from 'node:fs';
import { isAbsolute, join, relative } from 'node:path';
import { resolve_path_sync } from '../../core/path_resolution.ts';

/** Local inode metadata, not an executor wire type. */
export type FileVersion = readonly [bigint, bigint, bigint, bigint, bigint];
export function file_error(message: string): Error {
  return Object.assign(new Error(message), { name: 'ValueError' });
}
export function error_code(error: unknown): string | undefined {
  return (error as NodeJS.ErrnoException).code;
}
export function safe_component(value: string): string {
  if (!value || /[^A-Za-z0-9_-]/.test(value)) throw file_error('unsafe executor identifier');
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
/** This preflight never replaces descriptor-relative byte I/O. */
export function contained_path(root: string, relative_path: string): string {
  if (
    !relative_path ||
    relative_path.includes('\\') ||
    /%(25|2e|2f|5c)/i.test(relative_path) ||
    relative_path.split('/').some((part) => ['', '.', '..'].includes(part))
  )
    throw file_error('unsafe relative artifact path');
  if (is_symlink(root)) throw file_error('artifact root is symlink');
  let current = root;
  for (const part of relative_path.split('/')) {
    current = join(current, part);
    if (is_symlink(current)) throw file_error('artifact path contains symlink');
  }
  if (current.includes('\0')) throw file_error('lstat: embedded null character in path');
  const rel = relative(resolve_path_sync(root), resolve_path_sync(current));
  if (rel === '..' || rel.startsWith('../') || isAbsolute(rel))
    throw file_error('artifact path escapes root');
  return current;
}
export function version(status: BigIntStats): FileVersion {
  return [status.dev, status.ino, status.size, status.mtimeNs, status.ctimeNs];
}
export function same_version(one: FileVersion | null, other: FileVersion): boolean {
  return one !== null && one.every((value, index) => value === other[index]);
}
/** Python observes metadata through lstat here. Byte I/O does not trust this path. */
export function file_version(path: string): FileVersion | null {
  try {
    const status = lstatSync(path, { bigint: true });
    return status.isFile() ? version(status) : null;
  } catch (error) {
    if (error_code(error) === 'ENOENT') return null;
    throw error;
  }
}
