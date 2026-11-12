/** This owned thread serializes file operations and retains directory capabilities. */
import { closeSync } from 'node:fs';
import { parentPort } from 'node:worker_threads';
import * as files from './file_worker_operations.ts';
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
function execute(
  message: Exclude<FileWorkerRequest, { operation: 'stop' }>,
): FileOperationResults[keyof FileOperationResults] {
  switch (message.operation) {
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
      directories.delete(handle);
      closeSync(fd);
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
  };
}
function close_handles(): unknown {
  let failure: unknown;
  for (const [handle, fd] of directories) {
    directories.delete(handle);
    try {
      closeSync(fd);
    } catch (error) {
      failure ??= error;
    }
  }
  return failure;
}
// Fatal JavaScript errors still release libc descriptors before the thread exits.
process.once('uncaughtExceptionMonitor', close_handles);
process.once('exit', close_handles);
port.on('message', (message: FileWorkerRequest) => {
  if (message.operation === 'stop') {
    const failure = close_handles();
    port.close();
    if (failure !== undefined) throw failure;
    return;
  }
  let response: FileWorkerResponse;
  try {
    response = { id: message.id, value: execute(message) };
  } catch (error) {
    response = { id: message.id, error: error_body(error) };
  }
  port.postMessage(response);
});
