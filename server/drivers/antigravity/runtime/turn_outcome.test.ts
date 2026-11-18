import { mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import BetterSqlite3 from 'better-sqlite3';
import { afterEach, beforeEach, expect, test } from 'vitest';
import { QUOTA_EXHAUSTED_TEXT, watch_antigravity_prompt_outcome } from './turn_outcome.ts';

const SESSION = 'd054a0e0-e154-41e0-8aa7-38cef63181da';
let home: string;
let database: BetterSqlite3.Database;
beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), 'agy-turn-outcome-'));
  const conversations = join(home, 'antigravity-cli', 'conversations');
  mkdirSync(conversations, { recursive: true });
  database = new BetterSqlite3(join(conversations, `${SESSION}.db`));
  // agy 1.5.10's own steps table, as found on the VPS.
  database.exec(
    'CREATE TABLE steps (idx integer PRIMARY KEY, step_type integer, status, has_subtrajectory, ' +
      'metadata blob, error_details blob, permissions blob, task_details blob, render_info blob, ' +
      'step_payload blob, step_format)',
  );
});
afterEach(() => {
  database.close();
  rmSync(home, { recursive: true, force: true });
});
const add_step = (type: number, payload: string) =>
  database
    .prepare('INSERT INTO steps (step_type, step_payload) VALUES (?, ?)')
    // A protobuf field header leads the text, as in agy's own payloads.
    .run(type, Buffer.concat([Buffer.from([0x0a, 0x80, 0x01]), Buffer.from(payload)]));

// Risk: an earlier turn's error fails a later, healthy turn, or a turn agy ended `end_turn` on
// an API error is reported as a success. Only the newest step this prompt added counts.
test('only a new error step fails the turn, with the API message or the quota text', async () => {
  add_step(17, 'Error 400, Message: An old failure., Status: INVALID_ARGUMENT, Details: []');
  const quiet = watch_antigravity_prompt_outcome(home, SESSION);
  expect(await quiet.failure()).toBeNull();
  add_step(15, 'a planner response');
  expect(await quiet.failure()).toBeNull();

  const failed = watch_antigravity_prompt_outcome(home, SESSION);
  add_step(
    17,
    'Error 503, Message: The model is overloaded. Please try again later., Status: UNAVAILABLE, Details: []\ngoroutine 1 [running]:',
  );
  expect(await failed.failure()).toBe('The model is overloaded. Please try again later.');

  const exhausted = watch_antigravity_prompt_outcome(home, SESSION);
  add_step(
    17,
    'Error 429, Message: Quota exhausted for test model., Status: RESOURCE_EXHAUSTED, Details: [map[@type:type.googleapis.com/google.rpc.ErrorInfo domain:googleapis.com metadata:map[quotaResetDelay:3600s] reason:QUOTA_EXHAUSTED]]',
  );
  expect(await exhausted.failure()).toBe(QUOTA_EXHAUSTED_TEXT);
});
