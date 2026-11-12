/** Incremental private projection replay on the existing write-door connection. */
import { setImmediate as yieldLoop, setTimeout as pause } from 'node:timers/promises';
import type { Database, SqlParameters, TransactionSql } from '../../../core/database/contracts.ts';
import { VIEW_PROJECTION_VERSION } from '../contracts.ts';
import { PROJECTION_BACKFILL_BATCH } from '../configuration.ts';
import type { ConversationEventKind } from '../../events.ts';
import { conversation_event_payload_from_canonical_json } from '../../record/codec.ts';
import { append_events_after, append_projection } from './writer.ts';

const TABLES = ['state', 'rows', 'turns', 'groups', 'lookups'] as const;
const OUT_OF_DATE =
  'SELECT c.conversation_id FROM conversations c ' +
  'LEFT JOIN conversation_view_state v ON v.conversation_id=c.conversation_id ' +
  'WHERE c.latest_sequence>0 AND (v.conversation_id IS NULL OR v.projection_version!=?)';
const RETRY_ROUNDS = 5;
const FIRST_RETRY_WAIT_MILLISECONDS = 30_000;
const LONGEST_RETRY_WAIT_MILLISECONDS = 300_000;

function mapped(statement: string): string {
  return statement.replace(
    /\b(?:main\.)?conversation_view_(state|rows|turns|groups|lookups)\b/g,
    (_match, table: string) => `temp.conversation_rebuild_${table}`,
  );
}
function staging(tx: TransactionSql): TransactionSql {
  return {
    get: <Row>(statement: string, parameters?: SqlParameters) =>
      tx.get<Row>(mapped(statement), parameters),
    all: <Row>(statement: string, parameters?: SqlParameters) =>
      tx.all<Row>(mapped(statement), parameters),
    run: (statement, parameters) => tx.run(mapped(statement), parameters),
  };
}
function create_private_tables(tx: TransactionSql): void {
  for (const table of TABLES) {
    const canonical = `conversation_view_${table}`;
    const row = tx.get<{ sql: string }>(
      "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
      [canonical],
    );
    if (row === undefined) {
      throw new Error('Conversation projection schema is absent');
    }
    const statement = row.sql
      .replace(
        new RegExp(`^CREATE TABLE(?: IF NOT EXISTS)?\\s+["\x60\\[]?${canonical}["\x60\\]]?`, 'i'),
        `CREATE TEMP TABLE IF NOT EXISTS conversation_rebuild_${table}`,
      )
      .replace(/\s+REFERENCES conversations\(conversation_id\) ON DELETE CASCADE/g, '');
    tx.run(statement);
    const indexes = tx.all<{ name: string; sql: string }>(
      "SELECT name,sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL ORDER BY name",
      [canonical],
    );
    for (const index of indexes) {
      tx.run(
        index.sql
          .replace(
            /^CREATE (UNIQUE )?INDEX\s+\S+/i,
            (_match, unique: string | undefined) =>
              `CREATE ${unique ?? ''}INDEX IF NOT EXISTS temp.rebuild_${index.name}`,
          )
          .replace(new RegExp(`\\b${canonical}\\b`, 'g'), `conversation_rebuild_${table}`),
      );
    }
  }
}
function clear_private_tables(tx: TransactionSql): void {
  for (const table of [...TABLES].reverse()) {
    tx.run(`DELETE FROM temp.conversation_rebuild_${table}`);
  }
}
export function out_of_date_conversation_ids(database: Database): readonly string[] {
  return database.read((sql) =>
    sql
      .all<{ conversation_id: string }>(
        OUT_OF_DATE + ' ORDER BY c.latest_sequence,c.conversation_id',
        [VIEW_PROJECTION_VERSION],
      )
      .map((row) => row.conversation_id),
  );
}
/** TEMP names never shadow canonical tables, including during global commit steps. */
export async function rebuild_one_conversation_view(
  database: Database,
  conversation_id: string,
  isClosed: () => boolean = () => false,
): Promise<void> {
  const upper = database.write(
    (tx) => {
      create_private_tables(tx);
      clear_private_tables(tx);
      return (
        tx.get<{ latest_sequence: number }>(
          'SELECT latest_sequence FROM conversations WHERE conversation_id=?',
          [conversation_id],
        )?.latest_sequence ?? 0
      );
    },
    { changeSignal: 'silent' },
  );
  let through = 0;
  while (through < upper && !isClosed()) {
    through = database.write(
      (tx) => {
        const rows = tx.all<{
          sequence: number;
          created_at: number;
          kind: ConversationEventKind;
          payload: string;
        }>(
          'SELECT sequence,created_at,kind,payload FROM conversation_events WHERE conversation_id=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT ?',
          [conversation_id, through, upper, PROJECTION_BACKFILL_BATCH],
        );
        append_projection(
          staging(tx),
          rows.map((row) => ({
            ...row,
            conversation_id,
            payload: conversation_event_payload_from_canonical_json(row.kind, row.payload),
          })),
        );
        return rows.at(-1)?.sequence ?? upper;
      },
      { changeSignal: 'silent' },
    );
    await yieldLoop();
  }
  if (isClosed()) {
    return;
  }
  database.write(
    (tx) => {
      if (
        tx.get(OUT_OF_DATE + ' AND c.conversation_id=?', [
          VIEW_PROJECTION_VERSION,
          conversation_id,
        ]) !== undefined
      ) {
        append_events_after(staging(tx), conversation_id, through);
        for (const table of TABLES) {
          tx.run(`DELETE FROM main.conversation_view_${table} WHERE conversation_id=?`, [
            conversation_id,
          ]);
          tx.run(
            `INSERT INTO main.conversation_view_${table} SELECT * FROM temp.conversation_rebuild_${table}`,
          );
        }
      }
      clear_private_tables(tx);
    },
    { changeSignal: 'silent' },
  );
}

/** Starts after ownership; stale views remain unavailable until their atomic installation. */
export class ConversationViewRebuilder {
  #closed = false;
  #task: Promise<void> | undefined;
  #wake: (() => void) | undefined;
  start(database: Database): void {
    this.#task = this.#run(database).catch((error) =>
      console.error('Conversation view rebuild failed', error),
    );
  }
  close(): void {
    this.#closed = true;
    this.#wake?.();
  }
  async finish(): Promise<void> {
    await this.#task;
  }
  async #wait(milliseconds: number): Promise<void> {
    let wake!: () => void;
    const stopped = new Promise<void>((resolve) => {
      wake = resolve;
    });
    this.#wake = wake;
    const controller = new AbortController();
    try {
      await Promise.race([pause(milliseconds, undefined, { signal: controller.signal }), stopped]);
    } finally {
      controller.abort();
      this.#wake = undefined;
    }
  }
  async #run(database: Database): Promise<void> {
    let pending = out_of_date_conversation_ids(database);
    let wait = FIRST_RETRY_WAIT_MILLISECONDS;
    for (
      let attempt = 1;
      pending.length > 0 && attempt <= RETRY_ROUNDS + 1 && !this.#closed;
      attempt++
    ) {
      if (attempt > 1) {
        await this.#wait(wait);
        wait = Math.min(wait * 2, LONGEST_RETRY_WAIT_MILLISECONDS);
      }
      const failed: string[] = [];
      for (const conversation_id of pending) {
        if (this.#closed) {
          return;
        }
        const started = performance.now();
        try {
          await rebuild_one_conversation_view(database, conversation_id, () => this.#closed);
        } catch (error) {
          failed.push(conversation_id);
          console.error(
            `Conversation view rebuild failed for ${conversation_id} (attempt ${attempt})`,
            error,
          );
        }
        if (!this.#closed) {
          await this.#wait(performance.now() - started);
        }
      }
      pending = failed;
    }
    if (pending.length > 0 && !this.#closed) {
      console.error(
        `Conversation views left out of date after ${RETRY_ROUNDS + 1} attempts`,
        pending,
      );
    }
  }
}
