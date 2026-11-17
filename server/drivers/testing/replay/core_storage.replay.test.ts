/**
 * Rule 3 through the real core: one replayed Codex tool call, with the real Codex driver behind
 * a real `SqliteProcessConversationSystem`. Live deltas reach the live tail and write no row.
 * Each fact the driver reports writes exactly one row.
 */
import { rm } from 'node:fs/promises';
import { join } from 'node:path';
import { expect, test } from 'vitest';
import * as B from '../../../conversation/backends/contracts.ts';
import type {
  BackendChildFactory,
  BackendEventValue,
} from '../../../conversation/backends/contracts.ts';
import { LocalChildCustody } from '../../../conversation/child_custody.ts';
import {
  ConversationAccess,
  ConversationStartRequest,
  WorkspacePathPolicy,
} from '../../../conversation/contracts.ts';
import { text_message_content } from '../../../conversation/message_content.ts';
import { ConversationStore } from '../../../conversation/record/data/store.ts';
import { ConversationMessageFiles } from '../../../conversation/record/message_files.ts';
import { SqliteProcessConversationSystem } from '../../../conversation/system.ts';
import { monotonic_seconds } from '../../../lifecycle/configuration.ts';
import { fixture } from '../../../tests/ticket_fixture.ts';
import { parse_backend_instance_id } from '../../core/identity.ts';
import { registry_of_factories } from '../registry.ts';
import { driver_factory, prepare_replay, replay_services, settle } from './harness.ts';
import { LIVE_DELTA_CLASSES, REPLAY_TEST_TIMEOUT_MS } from './rules.ts';
import { TOOL_CALL } from './scenarios.ts';
import { transcript_paths } from './transcript.ts';

const CONVERSATION_ID = 'replay';
const WAIT_MS = 15_000;
/** The stored row kind each fact class writes. */
const FACT_ROW_KINDS: Readonly<Record<string, string>> = {
  AgentMessageCompleted: 'agent_message',
  CommandReplyReported: 'command_reply',
  ToolCallStarted: 'tool_call_started',
  ToolCallFinished: 'tool_call_finished',
  PlanUpdated: 'plan_updated',
  TokenUsageReported: 'token_usage',
  ContextCompacted: 'context_compacted',
  PermissionAskRaised: 'permission_asked',
  UserInputRequested: 'user_input_requested',
  UserInputFailed: 'user_input_failed',
  TurnEnded: 'turn_ended',
};

async function until<T>(
  what: string,
  found: () => T | undefined | Promise<T | undefined>,
): Promise<T> {
  const deadline = Date.now() + WAIT_MS;
  for (;;) {
    const value = await found();
    if (value !== undefined) return value;
    if (Date.now() > deadline) throw new Error(`no ${what} within ${WAIT_MS} ms`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

test(
  'a replayed Codex tool call stores each fact once and no live delta',
  async () => {
    const [path] = transcript_paths(TOOL_CALL.name, 'codex');
    const prepared = await prepare_replay(path!, TOOL_CALL);
    const owned = replay_services();
    const database = fixture();
    database.write((tx) =>
      tx.run('INSERT INTO conversation_runtime_epoch VALUES (1,?)', ['replay-runtime']),
    );
    const store = new ConversationStore(database, {
      local_child_custody: new LocalChildCustody({
        runtime: owned.worker_supervision,
        child_processes: owned.child_processes,
        one_shot_children: owned.one_shot_children,
      }),
    });
    const reported: BackendEventValue[] = [];
    const codex = driver_factory('codex', prepared.executable, prepared.folders, owned);
    // The core's own sink, with each report also kept here.
    const factory: BackendChildFactory = (values) =>
      codex({
        ...values,
        event_sink: Object.assign(Object.create(values.event_sink) as B.BackendEventSink, {
          report: (turn: B.TurnToken | null, event: BackendEventValue) => {
            reported.push(event);
            return values.event_sink.report(turn, event);
          },
        }),
      });
    const live_frames: string[] = [];
    const core = new SqliteProcessConversationSystem({
      store,
      delivery_model_refusal: () => null,
      execution_refusal: () => null,
      backend_registry: registry_of_factories({ codex: factory }),
      message_files: new ConversationMessageFiles(
        join(prepared.folders.root, 'conversation.sqlite'),
      ),
      publish_frame: (_conversation_id, frame) => live_frames.push(frame.constructor.name),
    });
    try {
      await core.start_conversation(
        new ConversationStartRequest({
          conversation_id: CONVERSATION_ID,
          model: prepared.transcript.model,
          backend_key: parse_backend_instance_id('codex'),
          workspace_folder: prepared.folders.workspace,
          access: ConversationAccess.full,
          workspace_path_policy: WorkspacePathPolicy.absolute,
        }),
      );
      const prompt = TOOL_CALL.steps[0]!;
      if (prompt.step !== 'write_prompt') throw new Error('TOOL_CALL starts with a prompt');
      await core.send(CONVERSATION_ID, text_message_content(prompt.text), {
        sender_label: 'Owner',
        sender_message_id: 'replay-prompt',
      });
      const ask = await until('permission ask', () =>
        reported.find(
          (event): event is B.PermissionAskRaised => event instanceof B.PermissionAskRaised,
        ),
      );
      // The core keeps the ask under its own id once it takes it from its event queue.
      const [ask_id] = await until('pending ask', async () => {
        const pending = [...(await core.pending_permission_ask_ids(CONVERSATION_ID))];
        return pending.length ? pending : undefined;
      });
      const allow = ask.ask.options.find((option) => option.option_kind === 'allow_once')!;
      expect(await core.answer_permission_ask(CONVERSATION_ID, ask_id!, allow.option_id)).toBe(
        'applied',
      );
      await until('stored turn end', () =>
        store.read_events_after(CONVERSATION_ID, 0).find((row) => row.kind === 'turn_ended'),
      );
      await core.shutdown({ deadline: monotonic_seconds() + 10 });
      await settle(owned.child_processes);
      await prepared.finished();
    } catch (error) {
      throw await prepared.explain(error);
    } finally {
      await core.shutdown({ deadline: monotonic_seconds() + 10 }).catch(() => {});
      await owned.worker_supervision.close({ deadline: monotonic_seconds() + 10 }).catch(() => {});
      await rm(prepared.folders.root, { recursive: true, force: true });
    }

    const reported_classes = reported.map((event) => event.constructor.name);
    const rows = store.read_events_after(CONVERSATION_ID, 0).map((row) => row.kind as string);
    // Each delta class this replay carries reached the live tail as a frame.
    const deltas = LIVE_DELTA_CLASSES.filter((name) => reported_classes.includes(name));
    expect(deltas).toEqual(expect.arrayContaining(['AgentMessageDelta', 'ModelThinkingHappened']));
    for (const name of deltas)
      expect(live_frames).toContain(
        {
          AgentMessageDelta: 'AgentMessageDeltaFrame',
          ModelThinkingHappened: 'ModelThinkingFrame',
          ToolCallProgress: 'ToolCallProgressFrame',
        }[name],
      );
    // No row besides the facts and the core's own record of the prompt and the answer.
    const fact_kinds = new Set(Object.values(FACT_ROW_KINDS));
    expect(rows.filter((kind) => !fact_kinds.has(kind))).toEqual(['prompt', 'permission_answered']);
    // Each fact the driver reported is one row of its kind.
    for (const [name, kind] of Object.entries(FACT_ROW_KINDS))
      expect(
        rows.filter((row) => row === kind).length,
        `${name} reported ${reported_classes.filter((each) => each === name).length} times`,
      ).toBe(reported_classes.filter((each) => each === name).length);
  },
  REPLAY_TEST_TIMEOUT_MS,
);
