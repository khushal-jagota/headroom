/** Existing advisory lease bytes and names, shared with the future supervisor. */
import { chmodSync, closeSync, constants, mkdirSync, openSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { getSystemErrorMessage } from 'node:util';
import { flock } from '../core/locks/flock.ts';
import { ServerLifecycleAlreadyOwnedError, ServerLifecycleError } from './contracts.ts';
import type { LifecycleLease } from './contracts.ts';

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
  const error = flock(descriptor, 'try-exclusive');
  if (error !== 0) {
    closeSync(descriptor);
    if ([11, 13, 35].includes(error))
      throw new ServerLifecycleAlreadyOwnedError(`Port ${port} already has a Panels supervisor.`);
    throw new ServerLifecycleError(
      `Could not acquire Panels lifecycle lease: ${getSystemErrorMessage(-Math.abs(error))}`,
    );
  }
  let closed = false;
  return {
    close() {
      if (closed) return;
      closed = true;
      try {
        flock(descriptor, 'unlock');
      } finally {
        closeSync(descriptor);
      }
    },
  };
}
