/** Paired uncertain receipt and concurrent attachment proof on the real core. */
import { createDatabase } from '../../core/database/index.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../lifecycle/configuration.ts';
import { ConversationStore } from '../record/data/store.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { ConversationStartRequest } from '../contracts.ts';
import { conversation_message_content, delivery_fate_json } from '../http.ts';
import { text_message_content } from '../message_content.ts';
import { ScriptedBackendRegistry } from './scripted_registry.ts';
import { BackendStateStore } from '../backend_state.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../../tickets/execution_admission.ts';

export async function run_receipts(spec: { database: string }): Promise<unknown> {
  const database = createDatabase(spec.database);
  database.write((tx) =>
    tx.run('INSERT INTO conversation_runtime_epoch VALUES (1,?)', ['unit15b-runtime']),
  );
  const store = new ConversationStore(database, { integer_now: () => 100 });
  const registry = new ScriptedBackendRegistry();
  const files = new ConversationMessageFiles(spec.database);
  const core = new SqliteProcessConversationSystem({
    store,
    delivery_model_refusal: (backend, model, ticket_id) =>
      new BackendStateStore(database).delivery_model_refusal(backend, model, ticket_id),
    execution_refusal: (id) =>
      database.read((sql) =>
        conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
      ),
    backend_child_factories: registry.factories,
    message_files: files,
    unix_time_now: () => 100,
    monotonic_now: () => 100,
  });
  const owner = { kind: 'owner', id: 'owner' } as const;
  const worker = { kind: 'ticket', id: 'worker' } as const;
  const checkpoints: unknown[] = [];
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
  async function capture(name: string, id: string, fates: unknown[]) {
    const rows = database.read((sql) =>
      Object.fromEntries(
        tables.map((table) => [
          table,
          sql.all(
            `SELECT * FROM ${table} ORDER BY ${table === 'conversation_lifecycle_facts' ? 'conversation_id,turn_number,kind,operation,identity' : table === 'conversation_view_lookups' ? 'conversation_id,lookup_kind,source_sequence,lookup_id' : '1,2'}`,
          ),
        ]),
      ),
    );
    checkpoints.push({ name, fates, rows, native: await registry.inspect(id) });
  }
  async function addressed(id: string, text: string) {
    await core.record_message_to_owner(id, text_message_content(text), {
      sender_label: 'Worker',
      sender: worker,
      recipient: owner,
    });
    return store.read_conversation(id)!.latest_sequence;
  }
  try {
    const id = 'uncertain';
    await core.start_conversation(
      new ConversationStartRequest({
        conversation_id: id,
        model: 'model',
        workspace_folder: '/tmp',
      }),
    );
    await core.send(id, text_message_content('busy'), { sender_label: 'system' });
    const displayed = await addressed(id, 'displayed');
    await registry.configure(id, 'steer', {
      result: { fate: 'uncertain', composed_content_delivered: true },
    });
    const fate = await core.send(id, text_message_content('reply'), {
      sender_label: 'owner',
      sender: owner,
      recipient: worker,
      sender_message_id: 'uncertain-review',
      mode: 'steer',
      owner_read_through_sequence: displayed,
    });
    await capture('uncertain', id, [delivery_fate_json(fate)]);

    const attachment = 'attachment';
    await core.start_conversation(
      new ConversationStartRequest({
        conversation_id: attachment,
        model: 'model',
        workspace_folder: '/tmp',
      }),
    );
    let bound = await addressed(attachment, 'displayed');
    let bytes = 'reply attachment';
    const content = async () =>
      conversation_message_content(
        files,
        attachment,
        [
          {
            piece: 'file',
            data: Buffer.from(bytes).toString('base64'),
            media_type: 'text/plain',
            file_name: 'reply.txt',
          },
        ],
        { sender_message_id: 'attachment-review' },
      );
    async function attempt() {
      return core.send(attachment, await content(), {
        sender_label: 'owner',
        sender: owner,
        recipient: worker,
        sender_message_id: 'attachment-review',
        owner_read_through_sequence: bound,
      });
    }
    const fates = await Promise.all([attempt(), attempt()]);
    await capture('attachment-concurrent', attachment, fates.map(delivery_fate_json));
    bound = await addressed(attachment, 'arrived after the lost response');
    const retry = await attempt();
    await capture('attachment-retry', attachment, [delivery_fate_json(retry)]);
    const kept = await content();
    const piece = kept[0];
    if (!('stored_file_id' in piece)) throw new Error('attachment missing');
    const attachment_bytes = Buffer.from(
      await files.read(attachment, piece.stored_file_id),
    ).toString('hex');
    bytes = 'different reply attachment';
    let conflict: string | null = null;
    try {
      await attempt();
    } catch (error) {
      conflict = (error as Error).message;
    }
    await capture('attachment-conflict', attachment, []);
    return { checkpoints, attachment_bytes, stored_file_id: piece.stored_file_id, conflict };
  } finally {
    await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
    await registry.close();
    database.close();
  }
}
