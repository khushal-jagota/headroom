import {
  closeSync,
  constants,
  mkdirSync,
  openSync,
  lstatSync,
  chmodSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { createHash } from 'node:crypto';
import { resolve_path_sync } from '../path_resolution.ts';
import koffi from 'koffi';
export { observe_existing_flock } from './observe.ts';
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
  return lock_file(path, constants.O_RDWR | constants.O_CREAT | constants.O_APPEND, 0o644);
}
function retry_flock(fd: number, operation: number): number {
  let result: number;
  do {
    result = flock(fd, operation);
  } while (result !== 0 && koffi.errno() === 4);
  return result;
}
function lock_file(path: string, flags: number, mode: number): LockHandle | null {
  const fd = openSync(path, flags, mode);
  if (retry_flock(fd, 2 | 4) !== 0) {
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
        retry_flock(fd, 8);
      } finally {
        closeSync(fd);
      }
    },
  };
}
export function canonical_database_path(path: string): string {
  return resolve_path_sync(path, { missing_only: true });
}
/** Pure naming: status inspection must never create the authority it observes. */
export function runtime_authority_path(canonical: string): string {
  return join(
    '/tmp',
    `panels-${process.getuid!()}`,
    `instance-${createHash('sha256').update(canonical).digest('hex')}.lock`,
  );
}
export function acquireRuntimeOwnership(dbPath: string): LockHandle {
  const canonical = canonical_database_path(dbPath);
  const stable_path = runtime_authority_path(canonical),
    directory = dirname(stable_path);
  mkdirSync(directory, { recursive: true, mode: 0o700 });
  const parent = lstatSync(directory);
  if (!parent.isDirectory() || parent.isSymbolicLink() || parent.uid !== process.getuid!())
    throw new Error('runtime authority directory is not owned by this account');
  chmodSync(directory, 0o700);
  const stable = lock_file(
    stable_path,
    constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW,
    0o600,
  );
  if (!stable) throw new ConversationRuntimeAlreadyOwnedError(canonical);
  try {
    if (canonical_database_path(dbPath) !== canonical)
      throw new Error('database identity changed during runtime ownership acquisition');
    // Python permits a missing DB in an existing parent, but never creates that parent.
    const legacy = lock_file(
      `${canonical}.conversation-owner.lock`,
      constants.O_RDWR | constants.O_CREAT | constants.O_APPEND,
      0o644,
    );
    if (!legacy) throw new ConversationRuntimeAlreadyOwnedError(canonical);
    let closed = false;
    return {
      close() {
        if (closed) return;
        closed = true;
        try {
          legacy.close();
        } finally {
          stable.close();
        }
      },
    };
  } catch (error) {
    stable.close();
    throw error;
  }
}
