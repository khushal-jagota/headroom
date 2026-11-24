/** The run record `move-attempt.json`: in the server folder, outside the database and the state inventory. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { atomic_write_json } from '../../environments/updates/attempts.ts';
import type { ServerMoveAttempt } from '../contracts.ts';
import { SERVER_MOVE_ATTEMPT_FILE } from '../contracts.ts';

const PHASES = new Set(['preparing', 'copying', 'paused', 'committing', 'moved', 'failed']);

function parse_attempt(value: unknown): ServerMoveAttempt {
  const record = value as Record<string, unknown> | null;
  if (
    record === null ||
    typeof record !== 'object' ||
    typeof record.move_id !== 'string' ||
    typeof record.target_machine_id !== 'string' ||
    typeof record.server_url !== 'string' ||
    typeof record.server_id !== 'string' ||
    typeof record.revision !== 'string' ||
    !Number.isSafeInteger(record.move_number) ||
    !Number.isSafeInteger(record.port) ||
    !PHASES.has(record.phase as string)
  )
    throw new Error(`The server move record is invalid: ${SERVER_MOVE_ATTEMPT_FILE}`);
  return record as unknown as ServerMoveAttempt;
}

export class ServerMoveAttemptStore {
  readonly path: string;
  constructor(data_directory: string) {
    this.path = join(data_directory, SERVER_MOVE_ATTEMPT_FILE);
  }
  read(): ServerMoveAttempt | null {
    let text: string;
    try {
      text = readFileSync(this.path, 'utf8');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
      throw error;
    }
    return parse_attempt(JSON.parse(text));
  }
  /** Atomic replace with fsync. The record is durable before any answer depends on it. */
  write(attempt: ServerMoveAttempt): ServerMoveAttempt {
    atomic_write_json(this.path, attempt as unknown as Record<string, unknown>);
    return attempt;
  }
}
