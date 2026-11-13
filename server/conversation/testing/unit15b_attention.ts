/** Attention provenance through the real area composition and scripted core. */
import { createDatabase } from '../../core/database/index.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../lifecycle/configuration.ts';
import { loadConfig } from '../../core/config.ts';
import { createAreasRuntime } from '../../areas.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import { ConversationStartRequest } from '../contracts.ts';
import { text_message_content } from '../message_content.ts';
import { delivery_fate_json } from '../http.ts';
import { ScriptedChild } from './scripted_child.ts';
import { read_summary } from '../../attention/actions.ts';
import { summary_request } from '../../attention/contracts.ts';
import { BackendStateStore } from '../backend_state.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../../tickets/execution_admission.ts';

export async function exercise(spec: {
  database: string;
  ticket: string;
  workspace: string;
  start: number;
}): Promise<unknown> {
  let now = spec.start - 86400;
  const database = createDatabase(spec.database);
  const areas = createAreasRuntime();
  const config = loadConfig(`${spec.workspace}/absent-config.yaml`, {
    PLAN_DB_PATH: spec.database,
    PLAN_TEST_MODE: '1',
    PLAN_LOGS_DIR: `${spec.workspace}/logs`,
    PLAN_CREDENTIAL_KEY_PATH: `${spec.workspace}/synthetic-credential-key`,
  });
  areas.initialize(database, config, {
    now: () => new Date(now * 1000),
    nowUnix: () => now,
    isoformat: () => new Date(now * 1000).toISOString(),
  });
  const { store } = areas.getRuntime();
  const children: ScriptedChild[] = [];
  const factory = ({
    event_sink,
  }: Parameters<import('../backends/contracts.ts').BackendChildFactory>[0]) => {
    const child = new ScriptedChild(event_sink);
    children.push(child);
    return child;
  };
  const core = new SqliteProcessConversationSystem({
    store,
    delivery_model_refusal: (backend, model, ticket_id) =>
      new BackendStateStore(database).delivery_model_refusal(backend, model, ticket_id),
    execution_refusal: (id) =>
      database.read((sql) =>
        conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
      ),
    backend_child_factories: { codex: factory, claude: factory, hermes: factory },
    message_files: new ConversationMessageFiles(spec.database),
    unix_time_now: () => now,
    monotonic_now: () => now,
  });
  areas.startRecordReplay(core.workspace_admission);
  try {
    await core.start_conversation(
      new ConversationStartRequest({
        conversation_id: 'attention-system',
        model: 'model',
        workspace_folder: spec.workspace,
      }),
    );
    database.write((tx) =>
      tx.run("UPDATE tickets SET conversation_id='attention-system' WHERE id=?", [spec.ticket]),
    );
    now = spec.start + 100;
    const fates: unknown[] = [];
    for (const text of ['Run the next worker step', 'A queued worker step']) {
      fates.push(
        delivery_fate_json(
          await core.send('attention-system', text_message_content(text), {
            sender_label: 'Panels loop',
          }),
        ),
      );
    }
    for (let index = 0; index < 2; index++) {
      await children[0]!.finish();
      await core.wait_until_quiescent();
    }
    const native_text = (await children[0]!.inspect()).delivered_text;
    const tables = [
      'conversations',
      'conversation_events',
      'conversation_lifecycle_facts',
      'conversation_view_state',
      'conversation_view_rows',
      'conversation_view_turns',
      'conversation_view_groups',
      'conversation_view_lookups',
    ];
    const orders: Record<string, string> = {
      conversation_lifecycle_facts: 'conversation_id,turn_number,kind,operation,identity',
      conversation_view_lookups: 'conversation_id,lookup_kind,source_sequence,lookup_id',
    };
    return database.read((sql) => ({
      fates,
      native_text,
      rows: Object.fromEntries(
        tables.map((table) => [
          table,
          sql.all(`SELECT * FROM ${table} ORDER BY ${orders[table] ?? '1,2'}`),
        ]),
      ),
      attention: {
        attention_history_metadata: sql.all(
          'SELECT * FROM attention_history_metadata ORDER BY 1,2',
        ),
      },
      summary: read_summary(
        sql,
        summary_request({ first_date: '2026-11-02', last_date: '2026-11-02' }),
        spec.start + 86401,
      ),
    }));
  } finally {
    await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
    for (const child of children) await child.terminate();
    await areas.closeRecord();
    database.close();
  }
}
