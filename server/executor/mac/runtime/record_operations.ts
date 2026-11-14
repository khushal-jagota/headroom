/** Runtime record byte work stays in the retained FilesService thread. */
import { randomUUID } from 'node:crypto';
import { closeSync, constants, fstatSync, readFileSync, writeSync } from 'node:fs';
import { basename, dirname } from 'node:path';
import { isMainThread } from 'node:worker_threads';
import { close_retained } from '../retained_cleanup.ts';
import {
  open_directory_descriptor,
  open_relative,
  rename_relative,
  sync_directory_descriptor,
  sync_file_descriptor,
  unlink_relative,
} from '../platform.ts';

if (isMainThread) throw new Error('Runtime record operations require the owned files worker.');
// A failed native close retains its capability for the worker's next cleanup attempt.
const handles = new Map<number, number>();
function retain(fd: number): number {
  handles.set(fd, fd);
  return fd;
}
function close(fd: number): void {
  closeSync(fd);
  handles.delete(fd);
}
export function owns_record_handles(): boolean {
  return handles.size !== 0;
}
export function cleanup_record_handles(): void {
  const failure = close_retained(handles, closeSync);
  if (failure !== undefined) throw failure;
}
/** Preserve the primary failure alongside every close failure. */
function owned<T>(fd: number, work: () => T): T {
  let result: T;
  try {
    result = work();
  } catch (error) {
    try {
      close(fd);
    } catch (cleanup) {
      throw new AggregateError([error, cleanup], 'Runtime record work and cleanup failed.');
    }
    throw error;
  }
  close(fd);
  return result;
}
export function read_runtime_record(path: string): string | null {
  try {
    const parent = retain(open_directory_descriptor(dirname(path)));
    return owned(parent, () => {
      const fd = retain(
        open_relative(
          parent,
          basename(path),
          constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
        ),
      );
      return owned(fd, () => {
        if (!fstatSync(fd).isFile()) throw new Error('Runtime record is not a regular file.');
        return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(readFileSync(fd));
      });
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
}
export function atomic_runtime_record(path: string, text: string): void {
  const parent = retain(open_directory_descriptor(dirname(path), { create: true }));
  owned(parent, () => {
    const temporary = '.' + basename(path) + '.' + randomUUID().replaceAll('-', '');
    let failure: unknown;
    try {
      const fd = retain(
        open_relative(
          parent,
          temporary,
          constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
          0o666,
        ),
      );
      owned(fd, () => {
        const bytes = Buffer.from(text);
        let offset = 0;
        while (offset < bytes.length) {
          const count = writeSync(fd, bytes, offset, bytes.length - offset);
          if (count === 0) throw new Error('Runtime record write made no progress.');
          offset += count;
        }
        sync_file_descriptor(fd);
      });
      rename_relative(parent, temporary, parent, basename(path));
      sync_directory_descriptor(parent);
    } catch (error) {
      failure = error;
    }
    try {
      unlink_relative(parent, temporary, true);
    } catch (cleanup) {
      if (failure !== undefined)
        throw new AggregateError(
          [failure, cleanup],
          'Runtime record work and temporary cleanup failed.',
        );
      throw cleanup;
    }
    if (failure !== undefined) throw failure;
  });
}
export function remove_runtime_record(path: string): void {
  let parent: number;
  try {
    parent = retain(open_directory_descriptor(dirname(path)));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return;
    throw error;
  }
  owned(parent, () => {
    unlink_relative(parent, basename(path), true);
    sync_directory_descriptor(parent);
  });
}
