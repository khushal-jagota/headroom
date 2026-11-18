import { readFileSync } from 'node:fs';
import {
  attempt_path,
  exclusive_existing_lock,
  replace_existing_json,
  utc_now,
} from '../attempts.ts';
import { is_object } from '../codec.ts';
import { assert_installer_path } from './configuration.ts';
import type { InstallerRequest } from './contracts.ts';

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
    const value = JSON.parse(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(readFileSync(this.path)),
    );
    if (!is_object(value)) throw new TypeError('update attempt must be an object');
    return value;
  }
  record(step: string, state?: string, detail: string | null = null): void {
    assert_installer_path(this.lock_path);
    exclusive_existing_lock(this.lock_path, () => {
      const current = this.read();
      if (current.attempt_id !== this.request.attempt_id) return;
      const now = utc_now();
      current.steps = { ...(is_object(current.steps) ? current.steps : {}), [step]: now };
      if (state !== undefined) {
        current.state = state;
        current.detail = detail;
      }
      // The installer unit's journal keeps the reason when no app returns to report it.
      if (state === 'failed') console.error(`Update failed: ${detail ?? 'no reason was recorded'}`);
      current.updated_at = now;
      assert_installer_path(this.path);
      replace_existing_json(this.path, current);
    });
  }
  /** The rollback restores this snapshot, so the record names it before the swap. */
  snapshot_taken(snapshot: string): void {
    assert_installer_path(this.lock_path);
    exclusive_existing_lock(this.lock_path, () => {
      const current = this.read();
      if (current.attempt_id !== this.request.attempt_id)
        throw new Error('The update attempt changed before its snapshot was recorded');
      const now = utc_now();
      current.backup = snapshot;
      current.steps = { ...(is_object(current.steps) ? current.steps : {}), backup: now };
      current.updated_at = now;
      assert_installer_path(this.path);
      replace_existing_json(this.path, current);
    });
  }
  claim(): Record<string, unknown> | null {
    assert_installer_path(this.lock_path);
    const current = exclusive_existing_lock(this.lock_path, () => {
      const value = this.read();
      return value.attempt_id === this.request.attempt_id && value.state === 'installing'
        ? value
        : null;
    });
    if (current !== null) this.record('installer_started');
    return current;
  }
}
