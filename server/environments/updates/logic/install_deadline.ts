/** How long an install may take before settle() calls it unfinished. */
import {
  DATABASE_BACKUP_BYTES_PER_SECOND,
  DATABASE_BACKUP_FLOOR_SECONDS,
  UPDATE_INSTALL_DEADLINE_SECONDS,
} from '../configuration.ts';
import { SCHEMA_STEP_TIMEOUT_SECONDS } from '../installer/configuration.ts';

/** One copy of the database: a backup before the install, or a restore during a rollback. */
export function database_copy_seconds(database_bytes: number): number {
  return Math.max(DATABASE_BACKUP_FLOOR_SECONDS, database_bytes / DATABASE_BACKUP_BYTES_PER_SECOND);
}

export function install_deadline_seconds(input: {
  readonly backup: boolean;
  readonly steps: boolean;
  readonly database_bytes: number;
}): number {
  const copy = database_copy_seconds(input.database_bytes);
  return (
    UPDATE_INSTALL_DEADLINE_SECONDS +
    (input.backup ? copy : 0) +
    (input.steps ? SCHEMA_STEP_TIMEOUT_SECONDS + copy : 0)
  );
}

/** The owner's pause message while the update backs up the database. */
export function backup_detail(database_bytes: number): string {
  return `Backing up the database first (about ${Math.ceil(database_copy_seconds(database_bytes) / 60)} min)`;
}
