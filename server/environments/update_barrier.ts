/** Durable final-save admission, outside the database and its rollback snapshots. */
import { randomUUID } from 'node:crypto';
import { addAbortListener } from 'node:events';
import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join } from 'node:path';
import { constants } from 'node:os';
import { getSystemErrorName } from 'node:util';
import { flock } from '../core/locks/flock.ts';
import { exclusive_existing_lock, missing } from './updates/attempts.ts';

function lock_error(errno: number): Error {
  return Object.assign(new Error('flock failed'), {
    errno: -errno,
    code: getSystemErrorName(-errno),
    syscall: 'flock',
  });
}
function sync_directory(path: string): void {
  const descriptor = openSync(path, 'r');
  try {
    fsyncSync(descriptor);
  } finally {
    closeSync(descriptor);
  }
}

export class UpdateBarrier {
  readonly path: string;
  frozen: boolean;
  /**
   * The server move that holds this barrier closed, or null. Update recovery never reopens a
   * barrier that a move holds. Only the move that set it clears it.
   */
  move_hold: string | null = null;
  /** The running home exchanged two compatible, authenticated machine leases during this freeze. */
  prepared_home_attempt: string | null = null;
  private current_generation = 0;
  private durable_generation = 0;
  private active = 0;
  private waiters = new Set<() => void>();
  constructor(path: string) {
    this.path = path;
    this.frozen = existsSync(path);
  }
  get generation(): number {
    return this.current_generation;
  }
  get drained(): boolean {
    return this.active === 0;
  }
  close(): number {
    this.prepared_home_attempt = null;
    this.frozen = true;
    return ++this.current_generation;
  }
  reopen(): number {
    this.prepared_home_attempt = null;
    this.frozen = false;
    return ++this.current_generation;
  }
  reopen_if_current(generation: number): number | null {
    return generation === this.current_generation ? this.reopen() : null;
  }
  persist(attempt_id: string, generation: number): void {
    const parent = dirname(this.path);
    mkdirSync(parent, { recursive: true });
    const temporary = join(parent, `.update-barrier-${randomUUID()}`);
    try {
      const descriptor = openSync(temporary, 'wx', 0o600);
      try {
        writeFileSync(descriptor, attempt_id + '\n');
        fsyncSync(descriptor);
      } finally {
        closeSync(descriptor);
      }
      exclusive_existing_lock(this.path + '.lock', () => {
        if (generation !== this.current_generation) return;
        renameSync(temporary, this.path);
        this.durable_generation = generation;
      });
      sync_directory(parent);
    } finally {
      try {
        unlinkSync(temporary);
      } catch (error) {
        if (!missing(error)) throw error;
      }
    }
  }
  freeze(attempt_id: string): void {
    this.persist(attempt_id, this.close());
  }
  remove(generation: number): void {
    const parent = dirname(this.path);
    mkdirSync(parent, { recursive: true });
    exclusive_existing_lock(this.path + '.lock', () => {
      if (generation !== this.current_generation) return;
      try {
        unlinkSync(this.path);
      } catch (error) {
        if (!missing(error)) throw error;
      }
      this.durable_generation = generation;
    });
    sync_directory(parent);
  }
  release(): void {
    this.remove(this.reopen());
  }
  refresh(): void {
    if (this.durable_generation === this.current_generation) this.frozen = existsSync(this.path);
  }
  installer_guard<T>(_attempt_id: string, action: (active: boolean) => T): T {
    const descriptor = openSync(join(dirname(this.path), 'update-installer.lock'), 'a+');
    let owned = false;
    try {
      const errno = flock(descriptor, 'try-exclusive');
      if (errno === constants.errno.EAGAIN || errno === constants.errno.EWOULDBLOCK)
        return action(true);
      if (errno !== 0) throw lock_error(errno);
      owned = true;
      return action(false);
    } finally {
      if (owned) flock(descriptor, 'unlock');
      closeSync(descriptor);
    }
  }
  installer_active(attempt_id: string): boolean {
    return this.installer_guard(attempt_id, (active) => active);
  }
  operation(): { admitted: boolean; release(): void };
  operation<T>(action: (admitted: boolean) => T): T;
  operation<T>(action?: (admitted: boolean) => T): T | { admitted: boolean; release(): void } {
    const admitted = !this.frozen;
    if (admitted) this.active++;
    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      if (admitted && --this.active === 0) {
        for (const resolve of this.waiters) resolve();
        this.waiters.clear();
      }
    };
    if (!action) return { admitted, release };
    let result: T;
    try {
      result = action(admitted);
    } catch (error) {
      release();
      throw error;
    }
    if (result instanceof Promise) return result.finally(release) as T;
    release();
    return result;
  }
  drain(signal?: AbortSignal): Promise<void> {
    if (this.drained || signal?.aborted) return Promise.resolve();
    const { promise, resolve } = Promise.withResolvers<void>();
    const finished = () => {
      this.waiters.delete(finished);
      resolve();
    };
    this.waiters.add(finished);
    const subscription = signal === undefined ? undefined : addAbortListener(signal, finished);
    return promise.finally(() => subscription?.[Symbol.dispose]());
  }
}

/** A terminal installer releases only its own marker, while retaining its lifetime lock. */
export function release_installer_barrier(data_directory: string, attempt_id: string): void {
  const path = join(data_directory, 'update-barrier.json');
  exclusive_existing_lock(path + '.lock', () => {
    let owner: string;
    try {
      owner = readFileSync(path, 'utf8').trim();
    } catch (error) {
      if (missing(error)) return;
      throw error;
    }
    if (owner !== attempt_id) return;
    unlinkSync(path);
    sync_directory(data_directory);
  });
}

/** Hold the installer's kernel lock, or report that a live installer holds it. */
export async function with_installer_lock<T>(
  data_directory: string,
  attempt_id: string | null,
  operation: (descriptor: number) => Promise<T>,
): Promise<{ acquired: false } | { acquired: true; result: T }> {
  const descriptor = openSync(join(data_directory, 'update-installer.lock'), 'a+');
  let owned = false;
  try {
    const errno = flock(descriptor, 'try-exclusive');
    if (errno === constants.errno.EAGAIN || errno === constants.errno.EWOULDBLOCK)
      return { acquired: false };
    if (errno !== 0) throw lock_error(errno);
    owned = true;
    if (attempt_id !== null) writeFileSync(descriptor, attempt_id + '\n');
    return { acquired: true, result: await operation(descriptor) };
  } finally {
    if (owned) flock(descriptor, 'unlock');
    closeSync(descriptor);
  }
}
