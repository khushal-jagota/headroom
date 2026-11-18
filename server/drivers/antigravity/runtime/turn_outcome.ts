/** Adapted from T3 Code b79d277c (apps/server/src/provider/acp/AntigravityTurnOutcome.ts).
 * Copyright (c) 2026 T3 Tools Inc. MIT permission notice: server/conversation/usage/T3-LICENSE.
 */
/**
 * agy reports an upstream API failure as a chat message and `end_turn`. Its session database
 * ends that turn with an error step, so the watch reads only the steps the current prompt added,
 * read-only. No protobuf is decoded: the error text is plain inside the step payload.
 */
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import BetterSqlite3 from 'better-sqlite3';
import type { AcpPromptOutcomeWatch } from '../../acp/flavor.ts';
import {
  APP_DATA_DIRECTORY_NAME,
  ERROR_STEP_PAYLOAD_READ_LIMIT_BYTES,
  SESSION_DATABASE_BUSY_TIMEOUT_MS,
} from './configuration.ts';

/** CORTEX_STEP_TYPE_ERROR_MESSAGE in agy's trajectory. */
const ERROR_STEP_TYPE = 17;
/** The Go harness's rendering of a Google API error. */
const API_ERROR = /Error (\d{3}), Message: (.+?), Status: ([A-Z_]+)/s;
const QUOTA_EXHAUSTED = /\breason:QUOTA_EXHAUSTED\b/;
const SESSION_ID = /^[\w-]+$/;
export const QUOTA_EXHAUSTED_TEXT = 'You have reached your Antigravity quota for this period.';
const UNEXPLAINED_ERROR_TEXT = 'Antigravity stopped this turn because of an error.';

/** Runs `read` on the steps table, or returns undefined when the file has none. */
function read_steps<T>(path: string, read: (database: BetterSqlite3.Database) => T): T | undefined {
  const database = new BetterSqlite3(path, {
    readonly: true,
    fileMustExist: true,
    timeout: SESSION_DATABASE_BUSY_TIMEOUT_MS,
  });
  try {
    const has_steps = database
      .prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'steps'")
      .get();
    return has_steps === undefined ? undefined : read(database);
  } finally {
    database.close();
  }
}
function last_index(database: BetterSqlite3.Database): number {
  const row = database.prepare('SELECT max(idx) AS idx FROM steps').get() as
    { idx: unknown } | undefined;
  return typeof row?.idx === 'number' ? row.idx : -1;
}
/** The newest step after `index`, with the head of its payload. */
function newest_step_after(
  database: BetterSqlite3.Database,
  index: number,
): { type: unknown; payload: unknown } | undefined {
  return database
    .prepare(
      `SELECT step_type AS type, substr(step_payload, 1, ?) AS payload
       FROM steps WHERE idx > ? ORDER BY idx DESC LIMIT 1`,
    )
    .get(ERROR_STEP_PAYLOAD_READ_LIMIT_BYTES, index) as
    { type: unknown; payload: unknown } | undefined;
}
/** The turn's failure text when the newest step is an error step, else null. */
function error_step_text(type: unknown, payload: unknown): string | null {
  if (type !== ERROR_STEP_TYPE) return null;
  const text = new TextDecoder().decode(payload instanceof Uint8Array ? payload : undefined);
  if (QUOTA_EXHAUSTED.test(text)) return QUOTA_EXHAUSTED_TEXT;
  const api_error = API_ERROR.exec(text);
  return api_error === null ? UNEXPLAINED_ERROR_TEXT : api_error[2]!.trim();
}
/** Take the baseline now, before the prompt is written. */
export function watch_antigravity_prompt_outcome(
  home: string,
  session_id: string,
): AcpPromptOutcomeWatch {
  if (!SESSION_ID.test(session_id)) return { failure: async () => null };
  const path = join(home, APP_DATA_DIRECTORY_NAME, 'conversations', `${session_id}.db`);
  let baseline: number | null;
  try {
    // A prompt that creates the database adds every step in it.
    baseline = existsSync(path) ? (read_steps(path, last_index) ?? -1) : -1;
  } catch {
    baseline = null;
  }
  return {
    async failure() {
      if (baseline === null) return null;
      try {
        const step = read_steps(path, (database) => newest_step_after(database, baseline!));
        return step === undefined ? null : error_step_text(step.type, step.payload);
      } catch {
        return null;
      }
    },
  };
}
