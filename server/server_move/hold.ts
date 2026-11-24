/** A server that starts inside a move keeps its update barrier closed until the move settles. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ServerMoveAttempt, ServerMoveHeldMarker } from './contracts.ts';
import { SERVER_MOVE_ATTEMPT_FILE, SERVER_MOVE_HELD_FILE } from './contracts.ts';

function read_json(path: string): unknown {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
}

export function read_held_marker(data_directory: string): ServerMoveHeldMarker | null {
  const value = read_json(join(data_directory, SERVER_MOVE_HELD_FILE)) as ServerMoveHeldMarker;
  if (value === null) return null;
  if (typeof value?.move_id !== 'string' || !value.move_id)
    throw new Error(`The held marker is invalid: ${SERVER_MOVE_HELD_FILE}`);
  return value;
}

/**
 * The move that holds this server at startup, or null. A new server waits for its commit. An
 * old server past the point of no return never lifts its pause. An old server that stopped
 * before that point is not held: its move service ends the run as failed.
 */
export function startup_move_hold(data_directory: string): string | null {
  const held = read_held_marker(data_directory);
  if (held !== null) return held.move_id;
  const attempt = read_json(join(data_directory, SERVER_MOVE_ATTEMPT_FILE)) as ServerMoveAttempt;
  if (attempt === null) return null;
  return attempt?.phase === 'committing' && typeof attempt.move_id === 'string'
    ? attempt.move_id
    : null;
}
