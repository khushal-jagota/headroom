/** Retained joins through the existing server lifecycle over copied source state. */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import type { Database } from '../../core/database/contracts.ts';
import { createDatabase } from '../../core/database/index.ts';
import { startServer } from '../../core/start.ts';
import type { StartedServer } from '../../core/start.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import { text_message_content } from '../message_content.ts';
import { delivery_fate_json } from '../http.ts';
import { UserInputAnswer } from '../events.ts';
import { values as signal_values } from '../publication/persistence.ts';
import { run_automatic_recovery } from '../deploy_interruptions/coordinator.ts';
import { create_production_conversation_core } from '../../lifecycle/production.ts';
import { compose_production_backends } from '../../lifecycle/production_bindings.ts';
import { create_backend_runtime, unavailable_backend_providers } from '../snapshot/composition.ts';
import { create_test_environment_runtime } from '../../environments/testing/composition.ts';
import { InboxStore } from '../../executor/data/inbox.ts';
import { FileStaging } from '../../executor/vps_files.ts';
import { VpsExecutor } from '../../executor/vps_transport.ts';
import { accept_lease_in_transaction } from '../../executor/vps_presence_store.ts';
import { as_object, parse_identity } from '../../executor/protocol.ts';
import type { WireValue } from '../../executor/contracts.ts';

const spec = JSON.parse(readFileSync(0, 'utf8'));
const old = parse_identity(spec.old),
  root = dirname(spec.path);
Object.assign(process.env, {
  PLAN_DB_PATH: spec.path,
  PLAN_PORT: '0',
  PLAN_TEST_MODE: '1',
  PLAN_EXECUTOR_ID: 'synthetic-executor',
  PLAN_DISPATCH_ENABLED: 'false',
  PLAN_LOGS_DIR: join(root, 'logs'),
  PLAN_BACKUP_DIR: join(root, 'backups'),
  PLAN_DISPATCHER_LOCK_PATH: join(root, 'dispatcher.lock'),
});
const original_now = Date.now;
Date.now = () => 1000000;
let database!: Database, core!: SqliteProcessConversationSystem, endpoint!: VpsExecutor;
let server!: StartedServer,
  first_signal = false;
async function cycle() {
  let bound!: ReturnType<typeof compose_production_backends>;
  const local = () => {
    throw new Error('retained Mac work selected a local child');
  };
  server = await startServer({
    createExecutor(current) {
      database = current.database;
      endpoint = new VpsExecutor(database, current.clock, {
        db_path: spec.path,
        changes: current.changes,
        media_signal: current.media_signal,
      });
      return endpoint;
    },
    readOpenRuns: () => endpoint.open_runs(),
    createConversationCore(current) {
      bound = compose_production_backends(current, {
        claude: () => local,
        codex: () => local,
        hermes: () => local,
        http: () =>
          create_backend_runtime({
            database,
            config: current.config,
            now: () => current.clock.now(),
            providers: unavailable_backend_providers(),
            one_shot_children: current.one_shot_children,
          }),
        stall_sources: () => [],
      });
      core = create_production_conversation_core(current, bound);
      return core;
    },
    createBackendServices: () => bound.http,
    createEnvironments: create_test_environment_runtime,
    testAreas(current) {
      first_signal = current.database.read(
        (sql) =>
          signal_values(sql).find((value) => value.conversation_id === 'retained')!.attention
            .awaiting_answer,
      );
      return [];
    },
  });
}
async function close() {
  await server.close({ deadline: performance.now() / 1000 + 5 });
  await server.shutdown_settled;
}
await cycle();
try {
  const running = core.conversations.get('retained')!.running_turn!;
  const stopped = core.conversations.get('stopping')!;
  const result: Record<string, unknown> = {
    adopted: [running.token.turn_number, [...running.pending_user_input_questions.keys()].sort()],
    stopping: [stopped.running_turn === null, stopped.child_is_quarantined],
  };
  result.first_signal = first_signal;
  result.saved = [
    await core.answer_permission_ask('retained', spec.permission, 'allow-once'),
    await core.answer_user_input('retained', spec.user_input, [
      new UserInputAnswer({ question_id: 'timing', answers: ['Now'] }),
      new UserInputAnswer({ question_id: 'scope', answers: ['Backend'] }),
    ]),
  ];
  await run_automatic_recovery(core, database);
  await run_automatic_recovery(core, database);
  result.recovery = database.read((sql) =>
    Object.values(
      sql.get(
        "SELECT recovery_state FROM deploy_interruptions WHERE source_conversation_id='closed'",
      )!,
    ),
  );
  result.claim = database.read((sql) =>
    Object.values(
      sql.get(
        "SELECT worker_step_claim,worker_step_claim_revision FROM tickets WHERE id='t-closed'",
      )!,
    ),
  );
  result.prompts = database
    .read((sql) =>
      sql.all<{ command_json: string }>(
        "SELECT command_json FROM executor_commands WHERE conversation_id='closed' AND operation='write_prompt'",
      ),
    )
    .map((row) => JSON.parse(row.command_json).child_command.arguments.content);
  await close();
  await cycle();
  const inbox = new InboxStore(database, new FileStaging(spec.path));
  const applications: unknown[] = [];
  result.applications = applications;
  const answers = database.read((sql) =>
    sql.all<{ command_id: string; operation: string }>(
      "SELECT command_id,operation FROM executor_commands WHERE conversation_id='retained' ORDER BY command_sequence",
    ),
  );
  for (const [index, row] of answers.entries()) {
    const kind = row.operation === 'answer_permission_ask' ? 'permission' : 'user_input';
    const envelope: WireValue<'QueuedEnvelope'> = {
      sender_id: 'sender',
      conversation_id: 'retained',
      sequence: index + 1,
      submission_id: kind,
      kind: 'answer_application',
      payload: {},
      file_dependencies: [],
    };
    const participant: import('../record/contracts.ts').RecordCommitParticipant<
      WireValue<'JsonObject'>
    > = (tx, take) => {
      const [receipt, taken] = inbox.accept_in_transaction(tx, envelope, () => take(), 1000000, {
        identity: old,
      });
      return { result: as_object(receipt, 'AcceptanceReceipt'), taken, written: [] };
    };
    for (let repeat = 0; repeat < 2; repeat++) {
      const accepted = await core.take_answer_application(
        { conversation_id: 'retained', turn_number: 1 },
        row.command_id,
        kind === 'permission' ? 'ask-1' : 'input-1',
        kind,
        participant,
      );
      applications.push([kind, accepted.taken, accepted.result.disposition]);
    }
  }
  await close();
  // The stopped server owns its closed database; reopen only for the source lease seed.
  const seed = createDatabase(spec.path);
  try {
    seed.write((tx) => accept_lease_in_transaction(tx, spec.fresh_lease, 1000000));
  } finally {
    seed.close();
  }
  await cycle();
  result.stale = delivery_fate_json(
    await core.send('retained', text_message_content('wait'), { sender_label: 'owner' }),
  );
  process.stdout.write(JSON.stringify(result));
} finally {
  await close();
  Date.now = original_now;
}
