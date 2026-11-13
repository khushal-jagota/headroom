/** Existing advisory lease bytes and names, shared with the future supervisor. */
import { chmodSync, closeSync, constants, mkdirSync, openSync } from 'node:fs';
import { dirname, join } from 'node:path';
import koffi from 'koffi';
import { ServerLifecycleAlreadyOwnedError, ServerLifecycleError } from './contracts.ts';
import type { LifecycleLease } from './contracts.ts';
const libc = koffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
const flock = libc.func('int flock(int fd, int operation)');
const strerror = libc.func('const char *strerror(int error)');

export function resolve_server_lifecycle_lease_path(port: number): string {
  const directory = join('/tmp', `panels-${process.getuid!()}`);
  return join(directory, `server-${port}.lock`);
}
export function acquire_server_lifecycle_lease(path: string, port: number): LifecycleLease {
  if (dirname(path) === dirname(resolve_server_lifecycle_lease_path(port))) {
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    chmodSync(dirname(path), 0o700);
  }
  // Staging's parent is prepared by materialization. Do not silently create a missing run root.
  const descriptor = openSync(path, constants.O_RDWR | constants.O_CREAT, 0o600);
  if (flock(descriptor, 2 | 4) !== 0) {
    const error = koffi.errno();
    closeSync(descriptor);
    if ([11, 13, 35].includes(error))
      throw new ServerLifecycleAlreadyOwnedError(`Port ${port} already has a Panels supervisor.`);
    throw new ServerLifecycleError(
      `Could not acquire Panels lifecycle lease: [Errno ${error}] ${strerror(error)}`,
    );
  }
  let closed = false;
  return {
    close() {
      if (closed) return;
      closed = true;
      try {
        flock(descriptor, 8);
      } finally {
        closeSync(descriptor);
      }
    },
  };
}
