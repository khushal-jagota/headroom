/** Deferred core steer behavior on the existing scripted process fixture. */
import { readFileSync } from 'node:fs';
import { createDatabase } from '../../core/database/index.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { ConversationStore } from '../record/data/store.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import {
  ConversationStartRequest,
  PromptDeliveryMode,
  HeldPromptPromotionMode,
} from '../contracts.ts';
import { delivery_fate_json } from '../http.ts';
import { BackendSteerUncertain } from '../backends/contracts.ts';
import { PromptDeliveryUncertainEventPayload } from '../events.ts';
import { message_content_text, text_message_content } from '../message_content.ts';
import { ScriptedBackendRegistry } from './scripted_registry.ts';
import { Deferred } from '../runtime/state.ts';
const spec = JSON.parse(readFileSync(0, 'utf8')) as { path: string };
const database = createDatabase(spec.path),
  trace: unknown[] = [];
let registry!: ScriptedBackendRegistry,
  store!: ConversationStore,
  core!: SqliteProcessConversationSystem;
function cycle() {
  registry = new ScriptedBackendRegistry();
  store = new ConversationStore(database, { integer_now: () => 1000 });
  database.write((tx) => store.reconcile_in_transaction(tx, new Map()), { changeSignal: 'silent' });
  core = new SqliteProcessConversationSystem({
    store,
    backend_child_factories: registry.factories,
    message_files: new ConversationMessageFiles(spec.path),
    model_is_enabled: () => true,
    execution_refusal: () => null,
    unix_time_now: () => 1000,
    monotonic_now: () => 1000,
  });
}
async function start(conversation_id: string) {
  await core.start_conversation(
    new ConversationStartRequest({
      conversation_id,
      backend_key: 'codex',
      model: 'model',
      workspace_folder: '/tmp',
    }),
  );
}
async function send(id: string, text: string, steer = false) {
  return delivery_fate_json(
    await core.send(id, text_message_content(text), {
      sender_label: 'owner',
      sender_message_id: `${id}-${text}`,
      mode: steer ? PromptDeliveryMode.steer : PromptDeliveryMode.queue,
    }),
  );
}
async function finish(id: string) {
  await registry.child(id).finish();
  await core.wait_until_quiescent();
}
function rows() {
  return database.read((sql) =>
    Object.fromEntries(
      [
        'conversations',
        'conversation_events',
        'conversation_lifecycle_facts',
        'conversation_view_state',
        'conversation_view_rows',
        'conversation_view_turns',
        'conversation_view_groups',
        'conversation_view_lookups',
      ].map((table) => [
        table,
        sql.all(
          `SELECT * FROM ${table} ORDER BY ${table === 'conversation_lifecycle_facts' ? 'conversation_id,turn_number,kind,operation,identity' : table === 'conversation_view_lookups' ? 'conversation_id,lookup_kind,source_sequence,lookup_id' : '1,2'}`,
        ),
      ]),
    ),
  );
}
async function capture(name: string, id: string, fates: unknown[] = []) {
  trace.push({
    name,
    fates,
    held: (await core.held_prompts(id)).map((item) => ({
      text: message_content_text(item.content),
      reason: item.queue_reason,
    })),
    rows: rows(),
  });
}
async function close() {
  await core.shutdown({ deadline: performance.now() / 1000 + 5 });
  await registry.close();
}
cycle();
try {
  await start('refused');
  await send('refused', 'incumbent');
  await registry.configure('refused', 'steer', {
    result: { fate: 'refused', refusal_reason: 'remote_steer_not_delivered' },
  });
  const refused = await send('refused', 'late steer', true);
  await capture('refused-queued', 'refused', [refused]);
  await finish('refused');
  await capture('refused-next', 'refused');
  await finish('refused');
  await start('promoted');
  await send('promoted', 'incumbent');
  await send('promoted', 'late steer');
  const selected = (await core.held_prompts('promoted'))[0]!;
  await registry.configure('promoted', 'steer', {
    result: { fate: 'refused', refusal_reason: 'remote_steer_not_delivered' },
  });
  const promoted = await core.promote_held_prompt(
    'promoted',
    selected.held_prompt_id,
    HeldPromptPromotionMode.steer,
  );
  await capture('promoted-queued', 'promoted', [delivery_fate_json(promoted)]);
  await finish('promoted');
  await capture('promoted-next', 'promoted');
  await finish('promoted');
  await start('late');
  await send('late', 'incumbent');
  const verdict = new Deferred<boolean>();
  registry.child('late').steer = async () =>
    new BackendSteerUncertain({ composed_content_delivered: null, not_delivered: verdict.promise });
  const uncertain = await send('late', 'late steer', true);
  await send('late', 'next');
  await finish('late');
  await capture('awaiting-non-arrival', 'late', [uncertain]);
  verdict.resolve(true);
  await Promise.all([...core.conversations.get('late')!.late_steers]);
  await core.wait_until_quiescent();
  await capture('late-first', 'late');
  await finish('late');
  await start('arrived');
  await send('arrived', 'incumbent');
  const arrived_verdict = new Deferred<boolean>();
  registry.child('arrived').steer = async () =>
    new BackendSteerUncertain({
      composed_content_delivered: null,
      not_delivered: arrived_verdict.promise,
    });
  await send('arrived', 'already arrived', true);
  await send('arrived', 'next');
  await finish('arrived');
  arrived_verdict.resolve(false);
  await Promise.all([...core.conversations.get('arrived')!.late_steers]);
  await core.wait_until_quiescent();
  await capture('arrived-no-repeat', 'arrived');
  await finish('arrived');
  await start('restart');
  await send('restart', 'incumbent');
  registry.child('restart').steer = async () =>
    new BackendSteerUncertain({
      composed_content_delivered: null,
      not_delivered: new Promise<boolean>(() => {}),
    });
  await send('restart', 'late steer', true);
  await finish('restart');
  const sequence = store
    .read_events_after('restart', 0)
    .find((row) => row.payload instanceof PromptDeliveryUncertainEventPayload)!.sequence;
  await capture('before-restart', 'restart');
  await close();
  cycle();
  await core.await_late_steer('restart', sequence, Promise.resolve(true));
  await Promise.all([...core.conversations.get('restart')!.late_steers]);
  await core.wait_until_quiescent();
  await capture('restart-once', 'restart');
  await close();
  cycle();
  await core.await_late_steer('restart', sequence, Promise.resolve(true));
  await core.wait_until_quiescent();
  await capture('restart-no-repeat', 'restart');
  process.stdout.write(JSON.stringify(trace));
} finally {
  await close();
  database.close();
}
