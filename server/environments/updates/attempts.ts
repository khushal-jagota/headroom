/** The cross-version update attempt file, outside SQLite and database backups. */
import {
  closeSync,
  constants,
  existsSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { basename, dirname, join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { flock } from '../../core/locks/flock.ts';
import { getSystemErrorName } from 'node:util';
import type { UpdateAttempt, UpdateAttemptState } from './contracts.ts';
import { ATTEMPT_FILE_NAME } from './configuration.ts';
import {
  attempt_to_dict,
  canonical_json,
  is_object,
  merged,
  parse_attempt,
  parse_attempt_json,
  parse_release,
} from './codec.ts';
import { JsonNumber } from '../../core/http/numeric_json.ts';

export const SETTLED_ATTEMPT_STATES: ReadonlySet<UpdateAttemptState> = new Set(['done', 'failed']);
export class UpdateConflict extends Error {}
export const attempt_path = (data_directory: string): string =>
  join(data_directory, ATTEMPT_FILE_NAME);
export function utc_now(): string {
  return new Date()
    .toISOString()
    .replace(
      /\.(\d{3})Z$/,
      (_, fraction: string) => (fraction === '000' ? '' : '.' + fraction + '000') + '+00:00',
    );
}
export function exclusive_lock<T>(path: string, action: () => T): T {
  mkdirSync(dirname(path), { recursive: true });
  return exclusive_existing_lock(path, action);
}
function lock(fd: number, operation: 'exclusive' | 'unlock'): void {
  const errno = flock(fd, operation);
  if (errno !== 0) {
    throw Object.assign(new Error('flock failed'), {
      errno: -errno,
      code: getSystemErrorName(-errno),
      syscall: 'flock',
    });
  }
}
/** Installer records must not recreate a removed data parent. No await in action. */
export function exclusive_existing_lock<T>(path: string, action: () => T): T {
  const fd = openSync(path, 'a+');
  try {
    lock(fd, 'exclusive');
    try {
      return action();
    } finally {
      lock(fd, 'unlock');
    }
  } finally {
    closeSync(fd);
  }
}
export function missing(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}
function read_record_json(path: string): unknown {
  // Reject invalid UTF-8 and leave a BOM for JSON to reject.
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
    readFileSync(path),
  );
  return parse_attempt_json(text);
}
export function atomic_write_json(path: string, payload: Record<string, unknown>): void {
  const parent = dirname(path);
  mkdirSync(parent, { recursive: true });
  try {
    const entry = lstatSync(path);
    if (entry.isSymbolicLink() || !entry.isFile())
      throw new Error(`record path must be a regular file: ${path}`);
  } catch (error) {
    if (!missing(error)) throw error;
  }
  replace_existing_json(path, payload);
}
/** Atomically replace the record in its existing parent directory. */
export function replace_existing_json(path: string, payload: Record<string, unknown>): void {
  const parent = dirname(path);
  const encoded = Buffer.from(canonical_json(payload) + '\n');
  const temporary = join(parent, `.${basename(path)}.${randomUUID()}.tmp`);
  let descriptor: number | undefined;
  try {
    descriptor = openSync(temporary, 'wx', 0o600);
    writeFileSync(descriptor, encoded);
    fsyncSync(descriptor);
    closeSync(descriptor);
    descriptor = undefined;
    renameSync(temporary, path);
  } catch (error) {
    if (descriptor !== undefined) closeSync(descriptor);
    try {
      unlinkSync(temporary);
    } catch (cleanup) {
      if (!missing(cleanup)) throw cleanup;
    }
    throw error;
  }
  const directory = openSync(parent, constants.O_RDONLY | constants.O_DIRECTORY);
  try {
    fsyncSync(directory);
  } finally {
    closeSync(directory);
  }
}
export class FileUpdateAttemptStore {
  readonly path: string;
  readonly lock_path: string;
  private readonly emit: () => void;
  private readonly now: () => string;
  constructor(path: string, emit: () => void, now: () => string = utc_now) {
    this.path = path;
    this.lock_path = path + '.lock';
    this.emit = emit;
    this.now = now;
  }
  read(): UpdateAttempt | null {
    try {
      return parse_attempt(read_record_json(this.path));
    } catch (error) {
      if (!missing(error)) console.warn('The update attempt record is unreadable', error);
      return null;
    }
  }
  private write(attempt: UpdateAttempt): UpdateAttempt {
    const payload = attempt_to_dict(attempt);
    try {
      const previous = read_record_json(this.path);
      if (is_object(previous) && previous.attempt_id === attempt.attempt_id)
        payload.release = merged(previous.release, payload.release);
    } catch {
      /* A missing or unreadable previous record does not block replacement. */
    }
    atomic_write_json(this.path, payload);
    return attempt;
  }
  create(attempt: UpdateAttempt): UpdateAttempt {
    const result = exclusive_lock(this.lock_path, () => {
      const current = this.read();
      if (current !== null && !SETTLED_ATTEMPT_STATES.has(current.state))
        throw new UpdateConflict('An update is already in progress.');
      return this.write(attempt);
    });
    this.emit();
    return result;
  }
  change(
    attempt_id: string,
    expected: ReadonlySet<UpdateAttemptState>,
    change: (current: UpdateAttempt) => UpdateAttempt,
  ): UpdateAttempt {
    const result = exclusive_lock(this.lock_path, () => {
      const current = this.read();
      if (current === null || current.attempt_id !== attempt_id || !expected.has(current.state))
        throw new UpdateConflict('The update changed or already moved on.');
      return this.write({ ...change(current), updated_at: this.now() });
    });
    this.emit();
    return result;
  }
  remove(attempt_id: string, expected: ReadonlySet<UpdateAttemptState>): void {
    exclusive_lock(this.lock_path, () => {
      const current = this.read();
      if (current === null || current.attempt_id !== attempt_id || !expected.has(current.state))
        throw new UpdateConflict('The update changed or already moved on.');
      unlinkSync(this.path);
    });
    this.emit();
  }
  /** Adopt the legacy GitHub request that stopped its connected Mac before dispatch. */
  import_legacy_request(data_directory: string, running_sha: string | null): UpdateAttempt | null {
    const result = exclusive_lock(this.lock_path, () => {
      if (running_sha === null || existsSync(this.path)) return null;
      let payload: Record<string, unknown>, release;
      try {
        const decoded = read_record_json(join(data_directory, 'owner-update-request.json'));
        if (
          !is_object(decoded) ||
          !is_object(decoded.release) ||
          !['request_id', 'state', 'mode'].every((key) => key in decoded)
        )
          return null;
        payload = decoded;
        release = parse_release(decoded.release);
      } catch {
        return null;
      }
      if (
        !['applying', 'applied'].includes(payload.state as string) ||
        release.manifest.app_sha !== running_sha
      )
        return null;
      let from_sha: unknown = '';
      try {
        const lifecycle = read_record_json(join(data_directory, 'deployment-lifecycle.json'));
        if (
          is_object(lifecycle) &&
          lifecycle.requested_sha === running_sha &&
          'prior_sha' in lifecycle
        )
          from_sha = lifecycle.prior_sha;
      } catch {
        /* A missing or malformed lifecycle does not prevent adoption. */
      }
      const now = this.now(),
        created = payload.created_at;
      const has_created =
        created instanceof JsonNumber
          ? created.value !== 0
          : Array.isArray(created)
            ? created.length !== 0
            : is_object(created)
              ? Object.keys(created).length !== 0
              : created !== undefined &&
                created !== null &&
                created !== false &&
                created !== 0 &&
                created !== '';
      const attempt = parse_attempt({
        attempt_id: payload.request_id,
        from_sha,
        to_sha: running_sha,
        mode: payload.mode === 'now' || payload.mode === 'idle' ? payload.mode : 'now',
        confirm_activity: payload.confirm_activity,
        state: 'done',
        created_at: has_created ? created : now,
        updated_at: now,
        release: payload.release,
        steps: { done: now },
      });
      return this.write({ ...attempt, mac_attempt_id: attempt.attempt_id });
    });
    if (result !== null) this.emit();
    return result;
  }
}
