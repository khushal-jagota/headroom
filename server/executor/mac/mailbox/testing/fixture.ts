/** Trusted finite oracle plus one real SQLite prepare fault. No wire decoder. */
import { existsSync, unlinkSync } from 'node:fs';
import { isMainThread } from 'node:worker_threads';
import type {
  RequiredJournalProtocol,
  JournalOutputPolicy,
} from '../../storage/journal_contracts.ts';
const parameters = new URL(import.meta.url).searchParams;
const source = new URL('../../storage/testing/journal_fixture.ts', import.meta.url);
source.search = parameters.toString();
const finite = await import(source.href);
export const decode_json: RequiredJournalProtocol['decode_json'] = finite.decode_json;
export const encode_value: RequiredJournalProtocol['encode_value'] = finite.encode_value;
export const command_owes_outputs: JournalOutputPolicy['command_owes_outputs'] =
  finite.command_owes_outputs;
if (!isMainThread) {
  const fault = parameters.get('feedback_fault'),
    database = parameters.get('database');
  const { default: Database } = await import('better-sqlite3');
  const prototype = Database.prototype as { name: string; prepare(sql: string): unknown };
  const prepare = prototype.prepare;
  prototype.prepare = function (sql: string) {
    if (
      this.name === database &&
      /^UPDATE commands SET result_reported=1/.test(sql) &&
      fault &&
      existsSync(fault)
    ) {
      unlinkSync(fault);
      throw Object.assign(new Error('synthetic report feedback write failure'), {
        name: 'SqliteError',
        code: 'SQLITE_BUSY',
      });
    }
    return prepare.call(this, sql);
  };
}
