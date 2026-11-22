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
import { Runtime } from '../runtime/supervisor.ts';
import type { RunResult } from '../contracts.ts';
import type { Database } from '../../core/database/contracts.ts';
import * as source_data from '../data.ts';
import { latest_due_slot, source_slot } from '../logic/local_time.ts';
interface Spec {
  db_path: string;
  now: number;
  source_id: string;
  results: Record<'failure' | 'high' | 'low', RunResult>;
  runs: string[];
}
const tables: Record<string, string> = {
  goals: 'id',
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
    participants: build_participants(context, new Runtime()),
  });
  const store = new ConversationStore(database, { integer_now: () => 50 });
  const system = {
    async start_conversation(request: ConversationStartRequest) {
      store.create_conversation(resolve_conversation_start_request(request), request.machine_id);
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
  const complete = (index: number, kind: 'failure' | 'high' | 'low' = 'failure') => {
    database.write((sql) => runs.claim_run(sql, spec.runs[index]!, { now: spec.now }));
    database.write((sql) =>
      runs.complete_run(sql, spec.runs[index]!, spec.results[kind], { now: spec.now }),
    );
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
    complete(0);
    const first = claim()!;
    const delivered = await deliver(first, 2);
    complete(1);
    snapshot('delivered-notice-and-repeat-dedup', { sent: delivered, repeat_claim: claim() });
    complete(2, 'high');
    snapshot('collection-achieves-goal');
    complete(3);
    const closed = claim()!;
    offer(closed, 'unit13-not-offered-closure');
    complete(4, 'low');
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
    const admitted = database.write((sql) =>
      wakes.admit_notice_batch(sql, {
        conversation_id: 'unit13-not-offered-transfer',
        sender_message_id: transferred.sender_message_id,
        now: spec.now + 4,
      }),
    );
    const chief = claim()!;
    snapshot('transfer-discards-old-batch-and-delivers-new-recipient', {
      admission: admitted,
      sent: await deliver(chief, 5),
    });
    const began = performance.now();
    snapshot('latest-occurrence-catchup', catchup_proof(database, loop, clock, spec));
    return { snapshots, catchup_seconds: (performance.now() - began) / 1000 };
  } finally {
    await loop.stop({ deadline: performance.now() / 1000 + 5 });
    await store.finish_writes();
    database.close();
  }
}

function catchup_proof(database: Database, loop: SourceScheduleLoop, clock: TestClock, spec: Spec) {
  const due = new Date(spec.now * 1000);
  due.setSeconds(0, 0);
  database.write((sql) =>
    actions.set_schedule(
      sql,
      spec.source_id,
      {
        expected_revision: source_data.read_source(sql, spec.source_id).source_revision,
        enabled: true,
        local_time: source_slot(due)[1],
      },
      { principal: { kind: 'owner', id: 'owner' }, now: spec.now },
    ),
  );
  const old = [2, 1].map((days) => {
    const local_now = new Date(due);
    local_now.setDate(local_now.getDate() - days);
    return database.write((sql) =>
      runs.admit_scheduled_run(sql, spec.source_id, {
        local_now,
        now: spec.now - days * 86400,
      }),
    ).id;
  });
  const stored_runs = () =>
    database.read((sql) =>
      sql
        .all<Record<string, unknown>>(
          "SELECT * FROM measure_source_runs WHERE source_id=? AND trigger='scheduled' ORDER BY created_at,schedule_date,schedule_time",
          [spec.source_id],
        )
        .map((row) =>
          Object.fromEntries(
            Object.entries(row).map(([key, value]) => [
              key,
              [
                'required_outputs',
                'dataset_outputs',
                'failure',
                'metrics',
                'test_candidates',
                'test_dataset_results',
              ].includes(key) && typeof value === 'string'
                ? JSON.parse(value)
                : value,
            ]),
          ),
        ),
    );
  const notices = () => database.read((sql) => sql.all('SELECT * FROM manager_wakes ORDER BY id'));
  const episodes = () =>
    database.read((sql) =>
      sql.all('SELECT * FROM measure_source_failure_episodes ORDER BY source_id,episode'),
    );
  const before_notices = notices(),
    before_episodes = episodes();
  clock.set(new Date(due.getTime() + 30 * 60 * 1000).toISOString());
  database.write((sql) =>
    sql.run('UPDATE measure_sources SET created_at=? WHERE id=?', [spec.now + 60, spec.source_id]),
  );
  loop.poll_once();
  const creation_refused = stored_runs();
  if (creation_refused.length !== 2) throw new Error('Source admitted a slot before creation');
  database.write((sql) =>
    sql.run('UPDATE measure_sources SET created_at=? WHERE id=?', [spec.now, spec.source_id]),
  );
  loop.poll_once();
  loop.poll_once();
  const admitted = stored_runs();
  if (
    admitted.length !== 3 ||
    admitted[2]!.schedule_date !== source_slot(due)[0] ||
    admitted[2]!.schedule_time !== source_slot(due)[1]
  )
    throw new Error('Source did not admit exactly the latest occurrence');
  for (const id of old)
    if (database.write((sql) => runs.claim_run(sql, id, { now: clock.nowUnix() })) !== null)
      throw new Error('Source claimed an obsolete occurrence');
  if (
    database.write((sql) =>
      runs.claim_run(sql, String(admitted[2]!.id), { now: clock.nowUnix() }),
    ) === null
  )
    throw new Error('Source refused the latest occurrence');
  const claimed = stored_runs();
  if (
    JSON.stringify(claimed.map((row) => row.status)) !==
    JSON.stringify(['interrupted', 'interrupted', 'running'])
  )
    throw new Error('Source claim states differ');
  if (
    JSON.stringify(before_notices) !== JSON.stringify(notices()) ||
    JSON.stringify(before_episodes) !== JSON.stringify(episodes())
  )
    throw new Error('Skipped schedule created a failure notice');
  const previous = process.env.TZ;
  process.env.TZ = 'Europe/Berlin';
  let slots: (number | null)[];
  try {
    slots = [
      ['02:30', '2026-03-29T03:30:00+02:00'],
      ['02:30', '2026-03-29T03:30:01+02:00'],
      ['02:30', '2026-10-25T02:15:00+01:00'],
      ['09:00', '2026-10-27T08:59:00+01:00'],
    ].map(([time, now]) => {
      const slot = latest_due_slot(time!, new Date(now!));
      return slot === null ? null : slot.getTime() / 1000;
    });
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
  return { creation_refused, admitted, claimed, episodes: before_episodes, slots };
}
