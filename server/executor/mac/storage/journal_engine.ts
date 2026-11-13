/** Synchronous journal rules run only in the owned SQLite worker. */
import { isMainThread } from 'node:worker_threads';
import { join } from 'node:path';
import {
  assertDatabaseVersion,
  createDurableSidecarDatabase,
} from '../../../core/database/index.ts';
import type {
  Database,
  DatabaseCloseHandle,
  DurableSidecarOpenFailure,
  TransactionSql,
} from '../../../core/database/contracts.ts';
import { stringify_pydantic_json } from '../../../core/http/numeric_json.ts';
import { integer_value } from '../../integers.ts';
import type { WireInput } from '../../protocol.ts';
import type { WireRootName, MailboxCommand, CommandResult } from '../../wire.generated.ts';
import type { JournalOutputPolicy } from './journal_contracts.ts';
import type { JournalProtocol } from './journal_internal_contracts.ts';
import {
  MAX_COMMAND_OUTCOMES_PER_REPORT,
  MAX_COMMAND_OUTCOME_REPORT_BYTES,
} from '../configuration.ts';
import {
  JOURNAL_NAME,
  JOURNAL_REFUSAL,
  JOURNAL_SCHEMA,
  COMMAND_JOURNAL_FORMAT_VERSION,
} from './journal_schema.ts';
if (isMainThread) throw new Error('Command journal storage requires its owned worker.');
export interface EncodedJournalRow {
  command_id: string;
  command?: Buffer;
  result?: Buffer;
}
export class JournalEngine {
  private database: Database | undefined;
  private failed_cleanup: DatabaseCloseHandle | undefined;
  private readonly protocol: JournalProtocol;
  private readonly policy: JournalOutputPolicy;
  private readonly health: () => void;
  private readonly held = new Map<string, undefined>();
  private singleton = false;
  private report_sequence = 0;
  private report: { selection: number; command_ids: string[] } | undefined;
  readonly incarnation: string;
  constructor(
    protocol: JournalProtocol,
    policy: JournalOutputPolicy,
    incarnation: string,
    health: () => void,
  ) {
    this.protocol = protocol;
    this.policy = policy;
    this.incarnation = incarnation;
    this.health = health;
  }
  open(root: string): boolean {
    this.health();
    const path = join(root, JOURNAL_NAME);
    const existed = assertDatabaseVersion(path, COMMAND_JOURNAL_FORMAT_VERSION, JOURNAL_REFUSAL);
    try {
      this.database = createDurableSidecarDatabase(path, {
        schemaVersion: COMMAND_JOURNAL_FORMAT_VERSION,
        foreignKeys: false,
        refusalMessage: JOURNAL_REFUSAL,
        initialize: (tx) => {
          this.health();
          for (const sql of JOURNAL_SCHEMA) tx.run(sql);
          this.health();
        },
      });
      this.health();
    } catch (error) {
      this.failed_cleanup = (error as Partial<DurableSidecarOpenFailure>).database_cleanup;
      throw error;
    }
    return !existed;
  }
  close(): void {
    this.database?.close();
    this.database = undefined;
    this.failed_cleanup?.close();
    this.failed_cleanup = undefined;
  }
  private db(): Database {
    if (!this.database) throw new Error('command journal is closed');
    return this.database;
  }
  private write<T>(work: (tx: TransactionSql) => T): T {
    this.health();
    return this.db().write((tx) => {
      this.health();
      const value = work(tx);
      this.health();
      return value;
    });
  }
  private encode<R extends WireRootName>(value: WireInput<R>, root: R): Buffer {
    return Buffer.from(stringify_pydantic_json(this.protocol.encode_value(value, root)));
  }
  private command(raw: Uint8Array | string): MailboxCommand {
    return this.protocol.decode_json(raw, 'MailboxCommand');
  }
  private result(raw: Uint8Array | string): CommandResult {
    return this.protocol.decode_json(raw, 'CommandResult');
  }
  recover(): void {
    this.write((tx) => {
      const rows = tx.all<{
        command_id: string;
        executor_incarnation: string;
        mailbox_command_json: Buffer;
      }>(
        "SELECT command_id,executor_incarnation,mailbox_command_json FROM commands WHERE phase='recorded'",
      );
      for (const row of rows) {
        const command = this.command(row.mailbox_command_json),
          child = command.child_command;
        const result: CommandResult = {
          command_id: command.command_id,
          child_outcome:
            child == null
              ? null
              : {
                  command_id: command.command_id,
                  child: child.child,
                  disposition: 'uncertain',
                  result: { reason: 'executor_process_restarted' },
                },
          model_list: null,
        };
        const own = row.executor_incarnation === this.incarnation;
        tx.run(
          "UPDATE commands SET phase='finished',result_json=?,result_reported=?,outputs_published=? WHERE command_id=?",
          [
            this.encode(result, 'CommandResult'),
            own ? 0 : 1,
            own && this.policy.command_owes_outputs(command, result) ? 0 : 1,
            row.command_id,
          ],
        );
      }
    });
  }
  poll_cursor(): bigint {
    return this.db().read(
      (sql) =>
        sql.get<{ cursor: bigint }>(
          'SELECT COALESCE(MAX(command_sequence),0) AS cursor FROM commands',
        )!.cursor,
    );
  }
  lower_poll_cursor(high: bigint, cursor_at_lease: bigint): boolean {
    if (cursor_at_lease <= high) return false;
    return this.write(
      (tx) =>
        tx.run('UPDATE commands SET command_sequence=0 WHERE command_sequence>?', [high]).changes >
        0,
    );
  }
  record_poll_reply(
    raw: Uint8Array | string,
    incarnation: string,
    after: bigint,
    recorded_at: bigint,
  ): { rows: EncodedJournalRow[]; command_count: number } {
    const reply = this.protocol.decode_json(raw, 'MailboxPollReply');
    const rows = this.write((tx) => {
      const cursor = tx.get<{ value: bigint }>(
        'SELECT COALESCE(MAX(command_sequence),0) AS value FROM commands',
      )!.value;
      if (cursor < after) return [];
      const fresh: EncodedJournalRow[] = [];
      for (const command of reply.commands) {
        const row = tx.get<{ phase: string; result_json: Buffer }>(
          'SELECT phase,result_json FROM commands WHERE command_id=?',
          [command.command_id],
        );
        if (row === undefined) {
          const encoded = this.encode(command, 'MailboxCommand');
          tx.run(
            "INSERT INTO commands(command_id,command_sequence,executor_incarnation,mailbox_command_json,phase,recorded_at_ms) VALUES(?,?,?,?,'recorded',?)",
            [
              command.command_id,
              integer_value(command.command_sequence),
              incarnation,
              encoded,
              recorded_at,
            ],
          );
          fresh.push({ command_id: command.command_id, command: encoded });
          continue;
        }
        tx.run('UPDATE commands SET command_sequence=MAX(command_sequence,?) WHERE command_id=?', [
          integer_value(command.command_sequence),
          command.command_id,
        ]);
        if (row.phase === 'finished') {
          const owed = this.policy.command_owes_outputs(command, this.result(row.result_json));
          tx.run(
            'UPDATE commands SET result_reported=0,outputs_published=CASE WHEN ? THEN 0 ELSE outputs_published END,mailbox_command_json=CASE WHEN ? THEN ? ELSE mailbox_command_json END WHERE command_id=?',
            [
              owed ? 1 : 0,
              owed ? 1 : 0,
              this.encode(command, 'MailboxCommand'),
              command.command_id,
            ],
          );
        }
      }
      return fresh;
    });
    return { rows, command_count: reply.commands.length };
  }
  result_of(id: string): Buffer | null {
    const row = this.db().read((sql) =>
      sql.get<{ result_json: Buffer }>(
        "SELECT result_json FROM commands WHERE command_id=? AND phase='finished'",
        [id],
      ),
    );
    return row === undefined ? null : this.encode(this.result(row.result_json), 'CommandResult');
  }
  finish(id: string, raw: Uint8Array | string, owed: boolean): void {
    const result = this.encode(this.result(raw), 'CommandResult');
    this.write((tx) => {
      tx.run(
        "UPDATE commands SET phase='finished',result_json=?,result_reported=0,outputs_published=? WHERE command_id=? AND phase='recorded'",
        [result, owed ? 0 : 1, id],
      );
    });
  }
  mark_outputs_published(id: string): void {
    this.write((tx) => {
      tx.run('UPDATE commands SET outputs_published=1 WHERE command_id=?', [id]);
    });
  }
  unpublished(): EncodedJournalRow[] {
    const rows = this.db().read((sql) =>
      sql.all<{ mailbox_command_json: Buffer; result_json: Buffer }>(
        "SELECT mailbox_command_json,result_json FROM commands WHERE phase='finished' AND outputs_published=0 ORDER BY recorded_at_ms",
      ),
    );
    return rows.map((row) => {
      const command = this.command(row.mailbox_command_json),
        result = this.result(row.result_json);
      return {
        command_id: command.command_id,
        command: this.encode(command, 'MailboxCommand'),
        result: this.encode(result, 'CommandResult'),
      };
    });
  }
  unreported(limit: number): EncodedJournalRow[] {
    const rows = this.db().read((sql) =>
      sql.all<{ result_json: Buffer }>(
        "SELECT result_json FROM commands WHERE phase='finished' AND result_reported=0 AND outputs_published=1 ORDER BY command_sequence LIMIT ?",
        [limit],
      ),
    );
    return rows.map((row) => {
      const result = this.result(row.result_json);
      return { command_id: result.command_id, result: this.encode(result, 'CommandResult') };
    });
  }
  mark_reported(ids: readonly string[]): void {
    this.write((tx) => {
      for (const id of ids)
        tx.run('UPDATE commands SET result_reported=1 WHERE command_id=?', [id]);
    });
  }
  select_report(): { selection: number; command_ids: string[]; body: Buffer } | null {
    if (this.report) throw new Error('a journal report selection is already active');
    const rows = this.db()
      .read((sql) =>
        sql.all<{ result_json: Buffer }>(
          "SELECT result_json FROM commands WHERE phase='finished' AND result_reported=0 AND outputs_published=1 ORDER BY command_sequence LIMIT ?",
          [MAX_COMMAND_OUTCOMES_PER_REPORT + this.held.size],
        ),
      )
      .map((row) => this.result(row.result_json));
    let candidates = rows.filter((row) => !this.held.has(row.command_id));
    if (!candidates.length) {
      const by_id = new Map(rows.map((row) => [row.command_id, row]));
      candidates = [...this.held.keys()].flatMap((id) => (by_id.has(id) ? [by_id.get(id)!] : []));
    }
    const chosen: CommandResult[] = [];
    let size = Buffer.byteLength('{"outcomes":[]}');
    for (const row of candidates.slice(0, MAX_COMMAND_OUTCOMES_PER_REPORT)) {
      size += this.encode(row, 'CommandResult').length + 1;
      if (chosen.length && (size > MAX_COMMAND_OUTCOME_REPORT_BYTES || this.singleton)) break;
      chosen.push(row);
    }
    if (!chosen.length) {
      this.held.clear();
      return null;
    }
    const body = this.encode({ outcomes: chosen }, 'CommandOutcomeReport');
    this.report = {
      selection: ++this.report_sequence,
      command_ids: chosen.map((row) => row.command_id),
    };
    return { ...this.report, body };
  }
  report_feedback(selection: number, settled_ids: readonly string[] | null): boolean {
    const report = this.report;
    if (!report || report.selection !== selection)
      throw new Error('journal report selection is closed');
    const failed = () => {
      this.singleton = true;
      if (report.command_ids.length === 1) {
        const id = report.command_ids[0]!;
        this.held.delete(id);
        this.held.set(id, undefined);
      }
    };
    try {
      if (settled_ids === null) {
        failed();
        return false;
      }
      const settled = new Set(settled_ids);
      const accepted = report.command_ids.filter((id) => settled.has(id));
      this.mark_reported(accepted);
      if (!accepted.length) {
        failed();
        return false;
      }
      this.singleton = false;
      for (const id of report.command_ids) this.held.delete(id);
      return true;
    } catch (error) {
      failed();
      throw error;
    } finally {
      this.report = undefined;
    }
  }
  shrink_to_tombstones(): void {
    this.write((tx) => {
      const rows = tx.all<{ command_id: string; result_json: Buffer }>(
        "SELECT command_id,result_json FROM commands WHERE phase='finished' AND result_reported=1 AND outputs_published=1 AND length(mailbox_command_json)>0",
      );
      for (const row of rows) {
        const result = this.result(row.result_json);
        const kept: CommandResult = {
          command_id: result.command_id,
          child_outcome: result.child_outcome ?? null,
          model_list: null,
        };
        tx.run("UPDATE commands SET mailbox_command_json=X'',result_json=? WHERE command_id=?", [
          this.encode(kept, 'CommandResult'),
          row.command_id,
        ]);
      }
    });
  }
}
