import { configure_conversation_start } from '../../runtime/conversation_start.ts';
/** Existing minimal durable conversation boundary, paired with fresh Python each run. */
import { createDatabase } from '../../core/database/index.ts';
import { loadConfig } from '../../core/config.ts';
import { TestClock } from '../../core/clock.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import type { ConversationSystem, ConversationStartRequest } from '../../conversation/contracts.ts';
import { PromptDeliveryStarted } from '../../conversation/contracts.ts';
import { PromptEventPayload } from '../../conversation/events.ts';
import { resolve_conversation_start_request } from '../../conversation/logic/conversation_start_resolution.ts';
import { ConversationStore } from '../../conversation/record/data/store.ts';
import type { MessageContent } from '../../conversation/message_content.ts';
import { deliver_batch } from '../../manager_wakes/runtime.ts';
import * as wakes from '../../manager_wakes/data.ts';
import type { WakeBatch } from '../../manager_wakes/contracts.ts';
import { SourceScheduleLoop } from '../schedule_loop.ts';
import { build_participants } from '../scheduling.ts';
import * as actions from '../actions.ts';
import * as runs from '../run_actions.ts';
import { failure } from '../contracts.ts';
import type { RunResult } from '../contracts.ts';
interface Spec {
  db_path: string;
  now: number;
  source_id: string;
  measure_id: string;
  goal_id: string;
  runs: string[];
}
const tables: Record<string, string> = {
  measure_sources: 'id',
  measure_source_runs: 'id',
  measure_observations: 'measure_id,date',
  goals: 'id',
  measure_source_failure_episodes: 'source_id,episode',
  manager_wakes: 'id',
  manager_wake_batches: 'id',
  manager_wake_batch_members: 'batch_id,wake_id',
};
export async function run(spec: Spec) {
  const database = createDatabase(spec.db_path);
  configure_conversation_start(database, { db_path: spec.db_path, workspace_root: null });
  const clock = new TestClock(new Date(spec.now * 1000).toISOString());
  const context = {
    database,
    clock,
    changes: new ChangeSignal(),
    config: loadConfig('/missing-unit13-config', {
      PLAN_DB_PATH: spec.db_path,
      PLAN_TEST_MODE: '1',
      PLAN_CREDENTIAL_KEY_PATH: spec.db_path + '.key',
    }),
    assert_database_owned() {},
  };
  const loop = new SourceScheduleLoop({
    ...context,
    participants: build_participants(context, {
      execute: async () => {
        throw new Error('no dispatcher in this proof');
      },
      cancel() {},
    }),
  });
  const store = new ConversationStore(database, { integer_now: () => 50 });
  const system = {
    async start_conversation(request: ConversationStartRequest) {
      store.create_conversation(resolve_conversation_start_request(request));
    },
    async send(
      id: string,
      content: MessageContent,
      options: Parameters<ConversationSystem['send']>[2],
    ) {
      store.append_event(
        id,
        new PromptEventPayload({
          content,
          sender_label: options.sender_label,
          mode: options.mode ?? 'queue',
          sender_message_id: options.sender_message_id,
        }),
      );
      return new PromptDeliveryStarted();
    },
    async is_running() {
      return false;
    },
    async kill() {},
  } as unknown as ConversationSystem;
  const snapshots: unknown[] = [];
  const snapshot = (phase: string, outcome: unknown = null) =>
    snapshots.push(
      database.read((sql) => ({
        phase,
        outcome,
        tables: Object.fromEntries(
          Object.entries(tables).map(([table, order]) => [
            table,
            sql.all(`SELECT * FROM ${table} ORDER BY ${order}`),
          ]),
        ),
        prompts: sql
          .all<{ payload: string }>(
            "SELECT payload FROM conversation_events WHERE kind='prompt' ORDER BY created_at,rowid",
          )
          .map((row) => JSON.parse(row.payload)),
      })),
    );
  const complete = (index: number, value: number | null = null) => {
    database.write((sql) => runs.claim_run(sql, spec.runs[index]!, { now: spec.now }));
    const result: RunResult = {
      observations:
        value === null
          ? []
          : [
              {
                measure_id: spec.measure_id,
                date: '2026-10-27',
                value,
                unavailable_reason: null,
                query_start_at: null,
                query_end_at: null,
                limitation: null,
              },
            ],
      dataset_results: [],
      failure: value === null ? failure('http_error', { http_status: 401 }) : null,
      metrics: {
        request_count: 0,
        response_bytes: 0,
        child_output_bytes: 0,
        elapsed_milliseconds: 1,
        script_cpu_milliseconds: 0,
      },
    };
    database.write((sql) => runs.complete_run(sql, spec.runs[index]!, result, { now: spec.now }));
    loop.poll_once();
  };
  const claim = () =>
    database.write((sql) =>
      wakes.claim_next_batch(sql, { process_token: 'unit13-source', now: spec.now + 1 }),
    );
  const offer = (batch: WakeBatch, conversation_id: string) =>
    database.write((sql) =>
      wakes.record_batch_offering(sql, batch.id, {
        conversation_id,
        process_token: 'unit13-source',
        now: spec.now + 2,
      }),
    );
  const deliver = (batch: WakeBatch, extra: number) =>
    deliver_batch(batch, {
      database,
      conversation_system: system,
      process_token: 'unit13-source',
      now: () => spec.now + extra,
      workspace_context: { db_path: spec.db_path, workspace_root: null },
      assert_database_owned() {},
    });
  try {
    loop.poll_once();
    snapshot('initial');
    complete(0);
    const first = claim()!;
    snapshot('producer-and-claim', first);
    snapshot('durable-prompt-and-delivered-receipt', await deliver(first, 2));
    complete(1);
    snapshot('same-episode-deduplicates', claim());
    complete(2, 12);
    snapshot('collection-achieves-goal');
    complete(3);
    const closed = claim()!;
    offer(closed, 'unit13-not-offered-closure');
    complete(4, 3);
    snapshot(
      'episode-closure-discards-and-correction-reopens-goal',
      database.write((sql) =>
        wakes.admit_notice_batch(sql, {
          conversation_id: 'unit13-not-offered-closure',
          sender_message_id: closed.sender_message_id,
          now: spec.now + 3,
        }),
      ),
    );
    complete(5);
    const transferred = claim()!;
    offer(transferred, 'unit13-not-offered-transfer');
    database.write((sql) =>
      actions.set_responsible(
        sql,
        spec.source_id,
        { kind: 'chief', id: 'chief' },
        {
          expected_responsibility_revision: 1,
          principal: { kind: 'owner', id: 'owner' },
          now: spec.now + 3,
        },
      ),
    );
    snapshot(
      'responsibility-transfer-discards-old-batch',
      database.write((sql) =>
        wakes.admit_notice_batch(sql, {
          conversation_id: 'unit13-not-offered-transfer',
          sender_message_id: transferred.sender_message_id,
          now: spec.now + 4,
        }),
      ),
    );
    const chief = claim()!;
    snapshot('replacement-recipient-claim', chief);
    snapshot('replacement-recipient-durable-prompt', await deliver(chief, 5));
    complete(6, 15);
    snapshot('later-collection-restores-goal');
    return snapshots;
  } finally {
    await loop.stop({ deadline: performance.now() / 1000 + 5 });
    await store.finish_writes();
    database.close();
  }
}
