/** Linux procfs capabilities: async byte work, retained parents and bounded publication. */
import { constants, lstatSync, renameSync } from 'node:fs';
import { link, lstat, mkdir, open, rename, unlink } from 'node:fs/promises';
import type { FileHandle } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join } from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { ValueError, integer_value } from './protocol.ts';
import { FILE_CHUNK_BYTES } from './configuration.ts';
import { version, same_version } from './mac/file_metadata.ts';
import type { FileVersion } from './mac/file_metadata.ts';
import type {
  DirectoryHandle,
  HeldFileLocation,
  FileOperationOptions,
  WireValue,
} from './contracts.ts';
export type { FileVersion } from './mac/file_metadata.ts';

/** Proven cleanup failures, distinct from an operation's ordinary refusal. */
export class FileCleanupError extends AggregateError {
  readonly cleanup_errors: readonly unknown[];
  constructor(errors: readonly unknown[], cleanup_errors: readonly unknown[]) {
    super(errors, 'Executor file cleanup failed');
    this.name = 'FileCleanupError';
    this.cleanup_errors = [...cleanup_errors];
  }
}

function check_aborted(signal?: AbortSignal): void {
  if (signal?.aborted)
    throw is_shutdown_business_cancellation(signal.reason)
      ? signal.reason
      : Object.assign(new DOMException('Executor file operation aborted.', 'AbortError'), {
          cause: signal.reason,
        });
}
function has_code(error: unknown, code: string): boolean {
  return error instanceof Error && 'code' in error && error.code === code;
}
/** Cleanup cannot hide either the operation failure or another cleanup failure. */
async function with_cleanup<T>(
  work: () => Promise<T>,
  cleanup: () => void | Promise<void>,
): Promise<T> {
  let value!: T;
  let failed = false;
  let failure: unknown;
  try {
    value = await work();
  } catch (error) {
    failed = true;
    failure = error;
  }
  try {
    await cleanup();
  } catch (error) {
    const previous = failed && failure instanceof FileCleanupError ? failure.cleanup_errors : [];
    const current = error instanceof FileCleanupError ? error.cleanup_errors : [error];
    throw new FileCleanupError(failed ? [failure, error] : [error], [...previous, ...current]);
  }
  if (failed) throw failure;
  return value;
}
function require_linux(): void {
  if (process.platform !== 'linux')
    throw new ValueError('Executor file intake requires Linux procfs descriptors');
}
function component(leaf: string): string {
  if (!leaf || leaf === '.' || leaf === '..' || /[/\\\0]/.test(leaf))
    throw new ValueError('unsafe directory component');
  return leaf;
}
function at(parent: DirectoryHandle, leaf: string): string {
  require_linux();
  if (parent.fd < 0) throw new ValueError('directory descriptor is closed');
  return `/proc/self/fd/${parent.fd}/${component(leaf)}`;
}
/** Refuse before intake. This checks the actual capability namespace, without a fallback. */
export async function assert_file_support(): Promise<void> {
  require_linux();
  const root = await open('/', constants.O_RDONLY | constants.O_DIRECTORY);
  await with_cleanup(
    async () => {
      let view: FileHandle;
      try {
        view = await open(`/proc/self/fd/${root.fd}/.`, constants.O_RDONLY | constants.O_DIRECTORY);
      } catch (error) {
        throw Object.assign(
          new ValueError('Executor file intake requires Linux procfs descriptors'),
          { cause: error },
        );
      }
      await with_cleanup(
        async () => {
          const original = await root.stat({ bigint: true });
          const observed = await view.stat({ bigint: true });
          if (
            original.dev !== observed.dev ||
            original.ino !== observed.ino ||
            !observed.isDirectory()
          )
            throw new ValueError('procfs does not retain directory descriptors');
        },
        () => view.close(),
      );
    },
    () => root.close(),
  );
}
export function safe_component(value: string): string {
  if (!value || /[^A-Za-z0-9_-]/.test(value)) throw new ValueError('unsafe executor identifier');
  return value;
}
async function is_symlink(path: string): Promise<boolean> {
  try {
    return (await lstat(path)).isSymbolicLink();
  } catch (error) {
    if (has_code(error, 'ENOENT') || has_code(error, 'ENOTDIR')) return false;
    if (has_code(error, 'ELOOP')) throw new ValueError('artifact path contains symlink');
    throw error;
  }
}
/** Source-compatible preflight. Descriptor walks, rather than this path, authorize I/O. */
export async function contained_path(root: string, relative_path: string): Promise<string> {
  if (
    !relative_path ||
    isAbsolute(relative_path) ||
    relative_path.includes('\\') ||
    /%(25|2e|2f|5c)/i.test(relative_path) ||
    relative_path.split('/').some((part) => ['', '.', '..'].includes(part))
  )
    throw new ValueError('unsafe relative artifact path');
  if (relative_path.includes('\0') || root.includes('\0'))
    throw new ValueError('lstat: embedded null character in path');
  if (await is_symlink(root)) throw new ValueError('artifact root is symlink');
  let current = root;
  for (const part of relative_path.split('/')) {
    current = join(current, part);
    if (await is_symlink(current)) throw new ValueError('artifact path contains symlink');
  }
  return current;
}
/** Walk raw components from /; never normalize a parent component before refusal. */
export async function directory_descriptor(
  path: string,
  options: { create?: boolean } = {},
): Promise<DirectoryHandle> {
  const absolute = isAbsolute(path) ? path : `${process.cwd()}/${path}`;
  const parts = absolute.split('/').filter((part) => part !== '');
  for (const part of parts) component(part);
  await assert_file_support();
  let held = await open('/', constants.O_RDONLY | constants.O_DIRECTORY);
  try {
    for (const part of parts) {
      if (options.create) {
        try {
          await mkdir(at(held, part), { mode: 0o700 });
          await held.sync();
        } catch (error) {
          if (!has_code(error, 'EEXIST')) throw error;
        }
      }
      const child = await open(
        at(held, part),
        constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
      );
      try {
        await held.close();
      } catch (error) {
        const failure =
          error instanceof FileCleanupError ? error : new FileCleanupError([error], [error]);
        await with_cleanup(
          async () => {
            throw failure;
          },
          () => child.close(),
        );
      }
      held = child;
    }
    return held;
  } catch (error) {
    return await with_cleanup(
      async () => {
        throw error;
      },
      () => held.close(),
    );
  }
}
async function with_location<T>(
  source: string | HeldFileLocation,
  work: (location: HeldFileLocation) => Promise<T>,
): Promise<T> {
  if (typeof source !== 'string') return await work(source);
  const parent_path = dirname(source);
  const parent = await directory_descriptor(parent_path === '.' ? process.cwd() : parent_path);
  return await with_cleanup(
    () => work({ parent, leaf: basename(source), path: source }),
    () => parent.close(),
  );
}
/** The returned file handle belongs to the caller. A path's parent can close after open. */
export async function regular_descriptor(source: string | HeldFileLocation): Promise<FileHandle> {
  let incoming: FileHandle | undefined;
  try {
    return await with_location(source, async (location) => {
      const handle = await open(
        at(location.parent, location.leaf),
        constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
      );
      try {
        if (!(await handle.stat()).isFile()) throw new ValueError('artifact is not regular file');
        incoming = handle;
        return handle;
      } catch (error) {
        return await with_cleanup(
          async () => {
            throw error;
          },
          () => handle.close(),
        );
      }
    });
  } catch (error) {
    // A parent close can fail after the regular file open succeeded.
    if (incoming)
      return await with_cleanup(
        async () => {
          throw error;
        },
        () => incoming!.close(),
      );
    throw error;
  }
}
export function create_file_at(target: HeldFileLocation): Promise<FileHandle> {
  return open(
    at(target.parent, target.leaf),
    constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
    0o600,
  );
}
/** Positional reads leave the descriptor ready for another operation starting at zero. */
export async function descriptor_hash(
  incoming: FileHandle,
  options: FileOperationOptions = {},
): Promise<readonly [string, bigint]> {
  const hash = createHash('sha256');
  const buffer = Buffer.allocUnsafe(FILE_CHUNK_BYTES);
  let count = 0n;
  for (;;) {
    check_aborted(options.signal);
    const { bytesRead } = await incoming.read(buffer, 0, buffer.length, count);
    check_aborted(options.signal);
    if (bytesRead === 0) return [hash.digest('hex'), count];
    hash.update(buffer.subarray(0, bytesRead));
    count += BigInt(bytesRead);
  }
}
/** Outgoing is a newly created inode at offset zero. Writes consume each block before reuse. */
export async function copy_hashed(
  incoming: FileHandle,
  outgoing: FileHandle,
  options: FileOperationOptions & { byte_limit?: bigint } = {},
): Promise<readonly [string, bigint]> {
  const hash = createHash('sha256');
  const buffer = Buffer.allocUnsafe(FILE_CHUNK_BYTES);
  let count = 0n;
  for (;;) {
    check_aborted(options.signal);
    const { bytesRead } = await incoming.read(buffer, 0, buffer.length, count);
    check_aborted(options.signal);
    if (bytesRead === 0) return [hash.digest('hex'), count];
    count += BigInt(bytesRead);
    if (options.byte_limit !== undefined && count > options.byte_limit)
      throw new ValueError('file exceeds manifest length');
    hash.update(buffer.subarray(0, bytesRead));
    let offset = 0;
    while (offset < bytesRead) {
      check_aborted(options.signal);
      const { bytesWritten } = await outgoing.write(buffer, offset, bytesRead - offset, null);
      if (!bytesWritten)
        throw Object.assign(new Error('file write made no progress'), { code: 'EIO' });
      offset += bytesWritten;
    }
  }
}
/** Both parent capabilities are borrowed until all byte work and cleanup settle. */
export async function copy_verified(
  source: HeldFileLocation,
  target: HeldFileLocation,
  manifest: WireValue<'FileManifest'>,
  options: FileOperationOptions = {},
): Promise<void> {
  let created = false;
  try {
    const incoming = await regular_descriptor(source);
    await with_cleanup(
      async () => {
        check_aborted(options.signal);
        const outgoing = await create_file_at(target);
        created = true;
        await with_cleanup(
          async () => {
            const length = integer_value(manifest.byte_count);
            const [digest, count] = await copy_hashed(incoming, outgoing, {
              ...options,
              byte_limit: length,
            });
            if (count !== length || digest !== manifest.sha256)
              throw new ValueError('incomplete or corrupt file');
            await outgoing.sync();
          },
          () => outgoing.close(),
        );
      },
      () => incoming.close(),
    );
  } catch (error) {
    if (!created) throw error;
    await with_cleanup(
      async () => {
        throw error;
      },
      () => unlink_at(target.parent, target.leaf),
    );
  }
}
export async function install_verified(
  source: HeldFileLocation,
  target: HeldFileLocation,
  manifest: WireValue<'FileManifest'>,
  options: FileOperationOptions = {},
): Promise<void> {
  const temporary: HeldFileLocation = {
    parent: target.parent,
    leaf: '.tmp-' + randomUUID().replaceAll('-', ''),
    path: target.path,
  };
  let copied = false;
  await with_cleanup(
    async () => {
      await copy_verified(source, temporary, manifest, options);
      copied = true;
      check_aborted(options.signal);
      await rename_at(target.parent, temporary.leaf, target.parent, target.leaf);
      await fsync_directory(target.parent);
    },
    async () => {
      if (copied) await unlink_at(target.parent, temporary.leaf, { missing_ok: true });
    },
  );
}
export async function file_digest(
  source: string | HeldFileLocation,
  options: FileOperationOptions = {},
): Promise<readonly [string, bigint]> {
  const incoming = await regular_descriptor(source);
  return await with_cleanup(
    () => descriptor_hash(incoming, options),
    () => incoming.close(),
  );
}
export async function verified_version(
  source: string | HeldFileLocation,
  manifest: WireValue<'FileManifest'>,
  options: FileOperationOptions = {},
): Promise<FileVersion | null> {
  try {
    return await with_location(source, async (location) => {
      const incoming = await regular_descriptor(location);
      return await with_cleanup(
        async () => {
          const before = version(await incoming.stat({ bigint: true }));
          const [digest, count] = await descriptor_hash(incoming, options);
          const after = version(await incoming.stat({ bigint: true }));
          if (
            !same_version(before, after) ||
            digest !== manifest.sha256 ||
            count !== integer_value(manifest.byte_count)
          )
            return null;
          return same_version(file_version_at(location.parent, location.leaf), after)
            ? after
            : null;
        },
        () => incoming.close(),
      );
    });
  } catch (error) {
    if (has_code(error, 'ENOENT')) return null;
    throw error;
  }
}
/** Synchronous metadata only. Byte work stays outside writers. */
export function file_version_at(parent: DirectoryHandle, leaf: string): FileVersion | null {
  try {
    const status = lstatSync(at(parent, leaf), { bigint: true });
    return status.isFile() ? version(status) : null;
  } catch (error) {
    if (has_code(error, 'ENOENT')) return null;
    throw error;
  }
}
export async function rename_at(
  from: DirectoryHandle,
  source_leaf: string,
  to: DirectoryHandle,
  target_leaf: string,
): Promise<void> {
  await rename(at(from, source_leaf), at(to, target_leaf));
}
/** Publication only: caller retains parents and pins overwritten inodes before its writer. */
export function rename_at_sync(
  from: DirectoryHandle,
  source_leaf: string,
  to: DirectoryHandle,
  target_leaf: string,
): void {
  renameSync(at(from, source_leaf), at(to, target_leaf));
}
/** link() refuses an occupied hold name. No target bytes are copied or opened. */
export async function link_at(
  parent: DirectoryHandle,
  source_leaf: string,
  hold_leaf: string,
): Promise<void> {
  await link(at(parent, source_leaf), at(parent, hold_leaf));
}
export async function unlink_at(
  parent: DirectoryHandle,
  leaf: string,
  options: { missing_ok?: boolean } = {},
): Promise<void> {
  try {
    await unlink(at(parent, leaf));
  } catch (error) {
    if (!options.missing_ok || !has_code(error, 'ENOENT')) throw error;
  }
}
export function fsync_directory(parent: DirectoryHandle): Promise<void> {
  return parent.sync();
}
