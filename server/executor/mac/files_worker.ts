/** This owned thread serializes file operations and retains directory capabilities. */
import { readdirSync } from 'node:fs';
import { parentPort } from 'node:worker_threads';
import * as files from './file_worker_operations.ts';
import * as transfers from './transfer_file_operations.ts';
import * as records from './runtime/record_operations.ts';
import * as archives from './updates/archive_operations.ts';
import { close_retained } from './retained_cleanup.ts';
import { close_native_descriptor, sync_directory_descriptor, unlink_relative } from './platform.ts';
import type {
  FileOperationResults,
  FileWorkerError,
  FileWorkerRequest,
  FileWorkerResponse,
} from './file_worker_contracts.ts';
const port = parentPort!;
const directories = new Map<number, number>();
let sequence = 0;
function descriptor(handle: number): number {
  const value = directories.get(handle);
  if (value === undefined) throw new Error('Executor directory handle is closed or unknown.');
  return value;
}
async function execute(
  message: Exclude<FileWorkerRequest, { operation: 'stop' }>,
): Promise<FileOperationResults[keyof FileOperationResults]> {
  switch (message.operation) {
    case 'read_runtime_revision':
      return archives.read_runtime_revision(...message.arguments);
    case 'unlink_at':
      return unlink_relative(descriptor(message.arguments[0]), message.arguments[1], true);
    case 'extract_runtime':
      return archives.extract_runtime(...message.arguments);
    case 'read_runtime_record':
      return records.read_runtime_record(...message.arguments);
    case 'atomic_runtime_record':
      return records.atomic_runtime_record(...message.arguments);
    case 'remove_runtime_record':
      return records.remove_runtime_record(...message.arguments);
    case 'open_transfer':
      return transfers.open_transfer(...message.arguments);
    case 'read_transfer':
      return transfers.read_transfer(...message.arguments);
    case 'write_transfer':
      return transfers.write_transfer(...message.arguments);
    case 'finish_transfer':
      return transfers.finish_transfer(...message.arguments);
    case 'close_transfer':
      return transfers.close_transfer(...message.arguments);
    case 'capture_file': {
      const [source, handle, checked_ns] = message.arguments;
      return files.capture_file(source, descriptor(handle), checked_ns);
    }
    case 'sweep_directory': {
      const [handle, path, retained] = message.arguments;
      const names = readdirSync(path);
      if (!names.length) return;
      const keep = new Set(retained);
      for (const name of names) if (!keep.has(name)) unlink_relative(descriptor(handle), name);
      sync_directory_descriptor(descriptor(handle));
      return;
    }
    case 'file_digest':
      return files.file_digest(...message.arguments);
    case 'verified_version':
      return files.verified_version(...message.arguments);
    case 'copy_verified':
      return files.copy_verified(...message.arguments);
    case 'install_verified':
      return files.install_verified(...message.arguments);
    case 'fsync_directory':
      return files.fsync_directory(...message.arguments);
    case 'open_directory': {
      const fd = files.directory_descriptor(...message.arguments);
      const handle = ++sequence;
      directories.set(handle, fd);
      return handle;
    }
    case 'close_directory': {
      const [handle] = message.arguments;
      const fd = descriptor(handle);
      close_native_descriptor(fd);
      directories.delete(handle);
      return;
    }
    case 'copy_verified_at': {
      const [source, handle, name, sha256, byte_count] = message.arguments;
      return files.copy_verified_at(source, descriptor(handle), name, sha256, byte_count);
    }
    case 'install_verified_at': {
      const [source, handle, name, sha256, byte_count] = message.arguments;
      return files.install_verified_at(source, descriptor(handle), name, sha256, byte_count);
    }
  }
}
function error_body(error: unknown): FileWorkerError {
  const value = (
    error instanceof Error ? error : new Error(String(error))
  ) as NodeJS.ErrnoException;
  return {
    name: value.name,
    message: value.message,
    ...('code' in value ? { code: value.code } : {}),
    ...('errno' in value ? { errno: value.errno } : {}),
    ...('syscall' in value ? { syscall: value.syscall } : {}),
    ...(value instanceof AggregateError ? { errors: value.errors.map(error_body) } : {}),
    ...(value.cause instanceof Error ? { cause: error_body(value.cause) } : {}),
  };
}
function close_handles(): unknown {
  const failures: unknown[] = [
    transfers.close_transfers(),
    close_retained(directories, close_native_descriptor),
  ].filter((failure) => failure !== undefined);
  for (const cleanup of [records.cleanup_record_handles, archives.cleanup_archive_handles]) {
    try {
      cleanup();
    } catch (error) {
      failures.push(error);
    }
  }
  return failures.length > 1
    ? new AggregateError(failures, 'Executor file cleanup failed.')
    : failures[0];
}
// Fatal JavaScript errors still release libc descriptors before the thread exits.
process.once('uncaughtExceptionMonitor', close_handles);
process.once('exit', close_handles);
async function dispatch(message: FileWorkerRequest): Promise<void> {
  if (message.operation === 'stop') {
    const failure = close_handles();
    if (failure !== undefined) {
      // Keep the worker and every failed capability alive. The host retains the
      // failure, and another stop retries without a renewed deadline.
      port.postMessage({ cleanup_failed: error_body(failure) } satisfies FileWorkerResponse);
      return;
    }
    port.close();
    return;
  }
  let response: FileWorkerResponse;
  try {
    response = { id: message.id, value: await execute(message) };
  } catch (error) {
    if (
      records.owns_record_handles() ||
      archives.owns_archive_handles() ||
      message.operation === 'close_transfer' ||
      message.operation === 'close_directory' ||
      (error instanceof AggregateError && message.operation === 'open_transfer')
    ) {
      port.postMessage({ cleanup_failed: error_body(error) } satisfies FileWorkerResponse);
      return;
    }
    response = { id: message.id, error: error_body(error) };
  }
  port.postMessage(response);
}
let pending = Promise.resolve();
port.on('message', (message: FileWorkerRequest) => {
  // Stop joins the same ordered chain as admitted async archive work.
  pending = pending.then(() => dispatch(message));
});
