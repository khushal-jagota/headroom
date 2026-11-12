import { closeSync, mkdirSync, openSync, realpathSync, lstatSync, readlinkSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import koffi from 'koffi';
const libc = koffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
const flock = libc.func('int flock(int fd, int operation)');
export interface LockHandle {
  close(): void;
}
export class ConversationRuntimeAlreadyOwnedError extends Error {
  constructor(path: string) {
    super(`ConversationRuntimeAlreadyOwned: ${path}`);
    this.name = 'ConversationRuntimeAlreadyOwnedError';
  }
}
/** Descriptors from Node's openSync are close-on-exec. Never unlink held lock files. */
export function acquireLock(path: string): LockHandle | null {
  mkdirSync(dirname(path), { recursive: true });
  const fd = openSync(path, 'a+', 0o644);
  if (flock(fd, 2 | 4) !== 0) {
    const errno = koffi.errno();
    closeSync(fd);
    if (errno === 11 || errno === 35) return null;
    throw new Error(`flock failed: errno ${errno}`);
  }
  let closed = false;
  return {
    close() {
      if (closed) return;
      closed = true;
      try {
        flock(fd, 8);
      } finally {
        closeSync(fd);
      }
    },
  };
}
function canonical_database_path(path: string): string {
  const absolute = resolve(path);
  try {
    return realpathSync(absolute);
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
  }
  // Python resolves a missing DB non-strictly. Follow an existing dangling link
  // before appending missing tails, so the future runtime uses the same lock.
  try {
    if (lstatSync(absolute).isSymbolicLink())
      return canonical_database_path(resolve(dirname(absolute), readlinkSync(absolute)));
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
  }
  return join(canonical_database_path(dirname(absolute)), basename(absolute));
}
export function acquireRuntimeOwnership(dbPath: string): LockHandle {
  const canonical = canonical_database_path(dbPath);
  const handle = acquireLock(`${canonical}.conversation-owner.lock`);
  if (!handle) throw new ConversationRuntimeAlreadyOwnedError(canonical);
  return handle;
}
