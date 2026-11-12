/** Real TS core + real Claude SDK + one owned local scripted CLI. */
import { readFileSync } from 'node:fs';
import { createDatabase } from '../../../core/database/index.ts';
import { ConversationStore } from '../../record/data/store.ts';
import { SqliteProcessConversationSystem } from '../../system.ts';
import { ConversationMessageFiles } from '../../record/message_files.ts';
import { ConversationStartRequest, ConversationRoleMaterials } from '../../contracts.ts';
import { AgentMessageDeltaFrame, UserInputAnswer } from '../../events.ts';
import { text_message_content } from '../../message_content.ts';
import { delivery_fate_json } from '../../http.ts';
import { create_claude_backend_child_factory } from '../../backends/claude_agent_sdk/adapter.ts';

export interface Part2Spec {
  database: string;
  workspace: string;
  executable: string;
  trace: string;
}
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
const rowOrders: Readonly<Record<string, string>> = {
  conversation_lifecycle_facts: 'conversation_id,turn_number,kind,operation,identity',
  conversation_view_lookups: 'conversation_id,lookup_kind,source_sequence,lookup_id',
};
export async function run_part2_parity(spec: Part2Spec): Promise<unknown> {
  const database = createDatabase(spec.database);
  database.write((tx) =>
    tx.run(
      'INSERT INTO conversation_runtime_epoch VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET epoch=excluded.epoch',
      ['unit18-runtime'],
    ),
  );
  const store = new ConversationStore(database, { integer_now: () => 100 });
  const factory = create_claude_backend_child_factory({
    claude_executable: spec.executable,
    environment_overrides: [['UNIT18_TRACE', spec.trace]],
  });
  const plainLiveText: { type: string; text_delta: string }[] = [];
  const core = new SqliteProcessConversationSystem({
    store,
    backend_child_factories: { claude: factory, codex: factory, hermes: factory },
    message_files: new ConversationMessageFiles(spec.database),
    unix_time_now: () => 100,
    publish_frame: (_id, frame) => {
      if (frame instanceof AgentMessageDeltaFrame) {
        plainLiveText.push({ type: 'agent_message_delta', text_delta: frame.text_delta });
      }
    },
  });
  const id = 'unit18-claude';
  const checkpoints: unknown[] = [];
  let malformedCancelSeconds = 0;
  let malformedAssistantSeconds = 0;
  let sender = 0;
  const send = async (scenario: string, mode: 'queue' | 'steer' = 'queue') =>
    delivery_fate_json(
      await core.send(id, text_message_content(`[CASE:${scenario}]`), {
        sender_label: 'Owner',
        sender_message_id: `sender-${++sender}`,
        mode,
      }),
    );
  const wait = async (condition: () => boolean | Promise<boolean>, description: string) => {
    const deadline = performance.now() + 15000;
    while (!(await condition())) {
      if (performance.now() > deadline) throw new Error(`Timed out: ${description}`);
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    await core.wait_until_quiescent();
  };
  const ended = async (count: number) =>
    wait(async () => {
      const actual = database.read(
        (sql) =>
          sql.get<{ count: number }>(
            "SELECT count(*) AS count FROM conversation_events WHERE kind='turn_ended'",
          )!.count,
      );
      return actual >= count && !(await core.is_running(id));
    }, `turn ${count} ending`);
  const capture = async (name: string, fates: unknown[]) => {
    await core.wait_until_quiescent();
    const rows = database.read((sql) =>
      Object.fromEntries(
        tables.map((table) => [
          table,
          sql.all<Record<string, unknown>>(
            `SELECT * FROM ${table} ORDER BY ${rowOrders[table] ?? '1,2'}`,
          ),
        ]),
      ),
    );
    checkpoints.push({ name, fates, rows, running: await core.is_running(id) });
  };
  try {
    await core.start_conversation(
      new ConversationStartRequest({
        conversation_id: id,
        backend_key: 'claude',
        model: 'haiku',
        reasoning_effort: 'low',
        workspace_folder: spec.workspace,
        workspace_path_policy: 'absolute',
        role_materials: new ConversationRoleMaterials({
          role_text: 'Synthetic owner',
          identity_environment_variables: [['PLAN_ACTOR', 'owner']],
        }),
      }),
    );
    const plain = await send('plain');
    await ended(1);
    await capture('plain', [plain]);
    const steerBase = await send('steer-base');
    const steer = await send('steer', 'steer');
    await ended(2);
    await capture('steer', [steerBase, steer]);
    const queueBase = await send('queue-base');
    const queued = await send('queued', 'steer');
    await core.interrupt(id);
    await ended(3);
    await capture('queued-and-cancelled', [queueBase, queued]);
    const permission = await send('permission');
    await wait(() => core.has_pending_permission_ask(id), 'permission ask');
    const ask = [...(await core.pending_permission_ask_ids(id))][0];
    const permissionAnswer = await core.answer_permission_ask(id, ask, 'approve_once');
    await ended(4);
    await capture('permission', [permission, { answer_fate: permissionAnswer }]);
    const question = await send('question');
    await wait(() => core.has_pending_user_input(id), 'question');
    const request = [...(await core.pending_user_input_request_ids(id))][0];
    const questionAnswer = await core.answer_user_input(id, request, [
      new UserInputAnswer({ question_id: 'Choose a color?', answers: ['Blue'] }),
    ]);
    await ended(5);
    await capture('question', [question, { answer_fate: questionAnswer }]);
    const interrupt = await send('interrupt-base');
    await core.interrupt(id);
    await ended(6);
    await capture('interrupt', [interrupt]);
    const malformedStarted = performance.now();
    const malformed = await send('malformed-cancel-base');
    await core.interrupt(id);
    await ended(7);
    await capture('malformed-cancel-receipt', [malformed]);
    malformedCancelSeconds = (performance.now() - malformedStarted) / 1000;
    const resume = await send('resume-must-fail');
    await core.wait_until_quiescent();
    await capture('failed-resume', [resume]);
    const malformedAssistantStarted = performance.now();
    const malformedId = 'unit18-malformed';
    await core.start_conversation(
      new ConversationStartRequest({
        conversation_id: malformedId,
        backend_key: 'claude',
        model: 'haiku',
        reasoning_effort: 'low',
        workspace_folder: spec.workspace,
        workspace_path_policy: 'absolute',
        role_materials: new ConversationRoleMaterials({
          role_text: 'Synthetic owner',
          identity_environment_variables: [['PLAN_ACTOR', 'owner']],
        }),
      }),
    );
    const malformedAssistant = delivery_fate_json(
      await core.send(malformedId, text_message_content('[CASE:malformed-assistant]'), {
        sender_label: 'Owner',
        sender_message_id: 'malformed-input',
      }),
    );
    await wait(async () => {
      const endings = database.read(
        (sql) =>
          sql.get<{ count: number }>(
            "SELECT count(*) AS count FROM conversation_events WHERE conversation_id=? AND kind='turn_ended'",
            [malformedId],
          )!.count,
      );
      return endings === 1 && !(await core.is_running(malformedId));
    }, 'malformed assistant fails its turn');
    await capture('malformed-assistant', [malformedAssistant]);
    malformedAssistantSeconds = (performance.now() - malformedAssistantStarted) / 1000;
  } finally {
    await core.shutdown();
    database.close();
  }
  return {
    checkpoints,
    plain_live_text: plainLiveText,
    malformed_cancel_seconds: malformedCancelSeconds,
    malformed_assistant_seconds: malformedAssistantSeconds,
    trace: readFileSync(spec.trace, 'utf8')
      .trim()
      .split('\n')
      .map((line) => JSON.parse(line)),
  };
}
