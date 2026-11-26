import {
  attempt_path,
  exclusive_existing_lock,
  read_record_json,
  replace_existing_json,
  utc_now,
} from '../attempts.ts';
import { is_object } from '../codec.ts';
import { assert_installer_path } from './configuration.ts';
import type { InstallerRequest } from './contracts.ts';

/**
 * Seconds for each part of an update snapshot: `staged_copy` and `late_check` and
 * `retention` while the home serves, `catch_up` or `stopped_copy` in the stopped window.
 */
export type SnapshotSeconds = Readonly<Record<string, number>>;
function snapshot_seconds(current: Record<string, unknown>): Record<string, unknown> {
  return is_object(current.snapshot_seconds) ? current.snapshot_seconds : {};
}

/** Keep unknown cross-version fields outside the typed store. */
export class InstallationRecord {
  readonly path: string;
  readonly lock_path: string;
  readonly request: Pick<InstallerRequest, 'data_directory' | 'attempt_id'>;
  constructor(request: Pick<InstallerRequest, 'data_directory' | 'attempt_id'>) {
    this.request = request;
    assert_installer_path(request.data_directory);
    this.path = attempt_path(request.data_directory);
    this.lock_path = this.path + '.lock';
  }
  read(): Record<string, unknown> {
    assert_installer_path(this.path);
    const value = read_record_json(this.path);
    if (!is_object(value)) throw new TypeError('update attempt must be an object');
    return value;
  }
  /** Caller holds the journal lock. Preserve fields written by earlier releases. */
  private write_step(current: Record<string, unknown>, step: string): void {
    const now = utc_now();
    assert_installer_path(this.path);
    replace_existing_json(this.path, {
      ...current,
      steps: { ...(is_object(current.steps) ? current.steps : {}), [step]: now },
      updated_at: now,
    });
  }
  record(step: string, state?: string, detail: string | null = null): void {
    assert_installer_path(this.lock_path);
    exclusive_existing_lock(this.lock_path, () => {
      const current = this.read();
      if (current.attempt_id !== this.request.attempt_id) return;
      if (state !== undefined) {
        current.state = state;
        current.detail = detail;
      }
      // The installer unit's journal keeps the reason when no app returns to report it.
      if (state === 'failed') console.error(`Update failed: ${detail ?? 'no reason was recorded'}`);
      this.write_step(current, step);
    });
  }
  /** The rollback restores this snapshot, so the record names it before the swap. */
  snapshot_taken(snapshot: string, seconds: SnapshotSeconds = {}): void {
    assert_installer_path(this.lock_path);
    exclusive_existing_lock(this.lock_path, () => {
      const current = this.read();
      if (current.attempt_id !== this.request.attempt_id)
        throw new Error('The update attempt changed before its snapshot was recorded');
      current.backup = snapshot;
      current.snapshot_seconds = { ...snapshot_seconds(current), ...seconds };
      this.write_step(current, 'backup');
    });
  }
  /** Each part of the snapshot's work, so the record shows the real stopped window. */
  snapshot_timed(seconds: SnapshotSeconds): void {
    assert_installer_path(this.lock_path);
    exclusive_existing_lock(this.lock_path, () => {
      const current = this.read();
      if (current.attempt_id !== this.request.attempt_id) return;
      replace_existing_json(this.path, {
        ...current,
        snapshot_seconds: { ...snapshot_seconds(current), ...seconds },
      });
    });
  }
  claim(): Record<string, unknown> | null {
    assert_installer_path(this.lock_path);
    return exclusive_existing_lock(this.lock_path, () => {
      const current = this.read();
      if (current.attempt_id !== this.request.attempt_id || current.state !== 'installing')
        return null;
      this.write_step(current, 'installer_started');
      return current; // The caller needs to know whether this step existed before the claim.
    });
  }
}
