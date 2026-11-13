/** The real TS adapter and core, with one local scripted ACP peer. */
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../../../lifecycle/configuration.ts';
import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createDatabase } from '../../../core/database/index.ts';
import { ConversationStore } from '../../record/data/store.ts';
import { SqliteProcessConversationSystem } from '../../system.ts';
import { BackendStateStore } from '../../backend_state.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from '../../../tickets/execution_admission.ts';
import { ConversationMessageFiles, MessageFileMissing } from '../../record/message_files.ts';
import { ConversationStartRequest, ConversationRoleMaterials } from '../../contracts.ts';
import { MessageImage, text_message_content } from '../../message_content.ts';
import { delivery_fate_json } from '../../http.ts';
import { create_hermes_backend_child_factory } from '../../backends/hermes_acp/adapter.ts';
import type { BackendChildFactory } from '../../backends/contracts.ts';

interface Action {
  operation:
    | 'send'
    | 'ended'
    | 'answer'
    | 'interrupt'
    | 'kill'
    | 'finish'
    | 'sweep'
    | 'remember_ask'
    | 'late_answer';
  text?: string;
  mode?: 'queue' | 'steer' | 'send_now';
  count?: number;
  effort?: string;
  model?: string;
  image?: boolean;
  fail_read?: boolean;
  seconds?: number;
}
interface Case {
  name: string;
  actions: Action[];
}
interface Spec {
  database: string;
  workspace: string;
  executable: string;
  trace: string;
  state: string;
  control: string;
  cases: Case[];
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
const rowOrders: Record<string, string> = {
  conversation_lifecycle_facts: 'conversation_id,turn_number,kind,operation,identity',
  conversation_view_lookups: 'conversation_id,lookup_kind,source_sequence,lookup_id',
};
export async function run_part2_parity(spec: Spec): Promise<unknown> {
  const parentEnvironmentBefore = { ...process.env };
  const database = createDatabase(spec.database);
  database.write((tx) =>
    tx.run(
      'INSERT INTO conversation_runtime_epoch VALUES (1,?) ON CONFLICT(singleton) DO UPDATE SET epoch=excluded.epoch',
      ['unit16-runtime'],
    ),
  );
  let now = 100;
  let serial = 0;
  let failReads = 0;
  let failedReads = 0;
  const files = new ConversationMessageFiles(spec.database);
  const read = files.read.bind(files);
  files.read = async (id, file) => {
    if (failReads > 0) {
      failReads--;
      failedReads++;
      throw new MessageFileMissing(file);
    }
    return read(id, file);
  };
  const backendEvents: unknown[] = [];
  const kept: unknown[] = [];
  const child_processes = new ChildProcesses();
  const base = create_hermes_backend_child_factory(
    {
      argv: [process.execPath, spec.executable],
      environment_overrides: [
        ['UNIT16_TRACE', spec.trace],
        ['UNIT16_STATE', spec.state],
        ['UNIT16_CONTROL', spec.control],
      ],
    },
    { child_processes },
  );
  const missing = create_hermes_backend_child_factory(
    {
      argv: [spec.workspace + '/missing-hermes-python'],
      environment_overrides: [],
    },
    { child_processes },
  );
  const factory: BackendChildFactory = (options) =>
    (options.resolved_start.conversation_id === 'unit16-missing-interpreter' ? missing : base)({
      ...options,
      event_sink: {
        async report(turn, event) {
          const value = JSON.parse(JSON.stringify(event));
          backendEvents.push({ turn, event: event.constructor.name, value });
          if (event.constructor.name === 'AgentMessageCompleted') {
            for (const part of value.content ?? [])
              if (part.stored_file_id) {
                const bytes = await read(
                  options.resolved_start.conversation_id,
                  part.stored_file_id,
                );
                kept.push({
                  stored_file_id: part.stored_file_id,
                  media_type: part.media_type,
                  sha256: createHash('sha256').update(bytes).digest('hex'),
                  size: bytes.length,
                });
              }
          }
          await options.event_sink.report(turn, event);
        },
      },
    });
  const frames: unknown[] = [];
  const core = new SqliteProcessConversationSystem({
    store: new ConversationStore(database, { integer_now: () => now }),
    delivery_model_refusal: (backend, model, ticket_id) => new BackendStateStore(database).delivery_model_refusal(backend, model, ticket_id),
    execution_refusal: (id) =>
      database.read((sql) =>
        conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
      ),
    backend_child_factories: { hermes: factory, codex: factory, claude: factory },
    message_files: files,
    unix_time_now: () => now,
    publish_frame: (id, frame) => {
      if (
        ['AgentMessageDeltaFrame', 'ModelThinkingFrame', 'ToolCallProgressFrame'].includes(
          frame.constructor.name,
        )
      )
        frames.push({
          conversation_id: id,
          frame: frame.constructor.name,
          value: JSON.parse(JSON.stringify(frame)),
        });
    },
  });
  const checkpoints: unknown[] = [];
  const wait = async (predicate: () => boolean | Promise<boolean>, reason: string) => {
    const deadline = performance.now() + 10000;
    while (!(await predicate())) {
      if (performance.now() > deadline) throw new Error('Timed out: ' + reason);
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    await core.wait_until_quiescent();
  };
  try {
    for (const item of spec.cases) {
      // Keep prior conversations outside the maintenance lookback.
      now += 10000;
      const started = performance.now();
      const id = 'unit16-' + item.name;
      const eventStart = backendEvents.length,
        frameStart = frames.length,
        fileStart = kept.length;
      const fates: unknown[] = [];
      await core.start_conversation(
        new ConversationStartRequest({
          conversation_id: id,
          backend_key: 'hermes',
          model: 'synthetic-model',
          workspace_folder: spec.workspace,
          workspace_path_policy: 'absolute',
          role_materials: new ConversationRoleMaterials({
            role_text: 'Synthetic owner',
            identity_environment_variables: [
              ['PLAN_ACTOR', 'owner'],
              ['PLAN_CONVERSATION_ID', id],
              ['UNIT16_CASE', item.name],
            ],
          }),
        }),
      );
      let sender = 0;
      let lastAsk: string | null = null;
      for (const action of item.actions) {
        if (action.operation === 'send') {
          sender++;
          let content = text_message_content(action.text!);
          if (action.image) {
            const image = await files.keep(id, Buffer.from('synthetic-image'), {
              media_type: 'image/png',
              sender_message_id: 'seed-image',
              piece_index: 0,
            });
            content = [
              ...content,
              new MessageImage({ stored_file_id: image.stored_file_id, media_type: 'image/png' }),
            ];
          }
          if (action.fail_read) failReads = 1;
          fates.push(
            delivery_fate_json(
              await core.send(id, content, {
                sender_label: 'Owner',
                sender_message_id: `${item.name}-sender-${sender}`,
                mode: action.mode ?? 'queue',
                reasoning_effort_change: action.effort ?? null,
                model_change: action.model ?? null,
              }),
            ),
          );
        } else if (action.operation === 'ended') {
          await wait(
            async () =>
              database.read(
                (sql) =>
                  sql.get<{ count: number }>(
                    "SELECT count(*) AS count FROM conversation_events WHERE conversation_id=? AND kind='turn_ended'",
                    [id],
                  )!.count,
              ) >= action.count! && !(await core.is_running(id)),
            id + ' ended ' + action.count,
          );
        } else if (action.operation === 'answer') {
          await wait(() => core.has_pending_permission_ask(id), id + ' permission');
          const ask = [...(await core.pending_permission_ask_ids(id))][0]!;
          fates.push({ answer_fate: await core.answer_permission_ask(id, ask, 'allow') });
        } else if (action.operation === 'remember_ask') {
          await wait(() => core.has_pending_permission_ask(id), id + ' unanswered permission');
          lastAsk = [...(await core.pending_permission_ask_ids(id))][0]!;
        } else if (action.operation === 'late_answer') {
          if (lastAsk === null) throw new Error('No remembered permission ask');
          fates.push({ late_answer_fate: await core.answer_permission_ask(id, lastAsk, 'allow') });
        } else if (action.operation === 'interrupt') await core.interrupt(id);
        else if (action.operation === 'kill') await core.kill(id);
        else if (action.operation === 'sweep') {
          now += action.seconds ?? 3000;
          await core.sweep_idle_children();
        } else if (action.operation === 'finish') {
          writeFileSync(
            spec.control + '.new',
            JSON.stringify({
              serial: ++serial,
              scenario: item.name,
              command: 'finish',
              text: action.text,
            }),
          );
          renameSync(spec.control + '.new', spec.control);
        }
      }
      await core.wait_until_quiescent();
      const rows = database.read((sql) =>
        Object.fromEntries(
          tables.map((table) => [
            table,
            sql.all(
              `SELECT * FROM ${table} WHERE conversation_id=? ORDER BY ${rowOrders[table] ?? '1,2'}`,
              [id],
            ),
          ]),
        ),
      );
      checkpoints.push({
        name: item.name,
        fates,
        rows,
        backend_events: backendEvents.slice(eventStart),
        frames: frames.slice(frameStart),
        kept: kept.slice(fileStart),
        running: await core.is_running(id),
        seconds: (performance.now() - started) / 1000,
      });
      await core.kill(id);
    }
  } finally {
    await core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
    database.close();
  }
  return {
    parent_environment_unchanged:
      JSON.stringify(process.env) === JSON.stringify(parentEnvironmentBefore),
    checkpoints,
    failed_reads: failedReads,
    trace: readFileSync(spec.trace, 'utf8')
      .trim()
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line)),
  };
}
