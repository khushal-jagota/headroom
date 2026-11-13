/** Actual core joins over the copied source database; no provider or listener. */
import { readFileSync } from 'node:fs';
import { createDatabase } from '../../core/database/index.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import { ConversationStore } from '../record/data/store.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { retained_open_runs } from '../runtime/remote.ts';
import { text_message_content } from '../message_content.ts';
import { delivery_fate_json } from '../http.ts';
import { UserInputAnswer } from '../events.ts';
import { SignalPublicationCoordinator } from '../publication/coordinator.ts';
import { deployment_participants } from '../deploy_interruptions/participants.ts';
import { run_automatic_recovery } from '../deploy_interruptions/coordinator.ts';
import { executor_remote_recovery } from '../../lifecycle/production.ts';
import { InboxStore } from '../../executor/data/inbox.ts';
import { FileStaging } from '../../executor/vps_files.ts';
import { MacRuns } from '../../executor/vps_runs.ts';
import { SqliteCommandMailbox } from '../../executor/vps_mailbox.ts';
import { MacPresenceService } from '../../executor/vps_presence.ts';
import { accept_lease_in_transaction } from '../../executor/vps_presence_store.ts';
import { as_object, parse_identity } from '../../executor/protocol.ts';
import type { WireValue } from '../../executor/contracts.ts';

const spec = JSON.parse(readFileSync(0, 'utf8'));
const database = createDatabase(spec.path),
  old = parse_identity(spec.old);
const original_now = Date.now;
Date.now = () => 1000000;
let core!: SqliteProcessConversationSystem, runs!: MacRuns;
let mailbox!: SqliteCommandMailbox, presence!: MacPresenceService;
function cycle() {
  mailbox = new SqliteCommandMailbox(database, { clock: () => 1000000 });
  presence = new MacPresenceService(database, mailbox, {
    changes: new ChangeSignal(),
    clock: () => 1000000,
  });
  runs = new MacRuns(database, presence, mailbox);
  const remote = runs.as_factory();
  const factories = { codex: remote, claude: remote, hermes: remote };
  const store = new ConversationStore(database, {
    ...deployment_participants(spec.path),
    integer_now: () => 1000,
  });
  database.write((tx) => store.reconcile_in_transaction(tx, retained_open_runs(factories)));
  core = new SqliteProcessConversationSystem({
    store,
    backend_child_factories: factories,
    message_files: new ConversationMessageFiles(spec.path),
    model_is_enabled: () => true,
    execution_refusal: () => null,
    unix_time_now: () => 1000,
    monotonic_now: () => 1000,
    remote_recovery: executor_remote_recovery(database, () => core),
  });
  runs.conversations = core;
}
async function close() {
  const options = { deadline: performance.now() / 1000 + 5 };
  await core.shutdown(options);
  await runs.stop(options);
  await presence.stop(options);
  await mailbox.stop(options);
  await Promise.all([runs.settled(), presence.settled(), mailbox.settled()]);
}
cycle();
try {
  const running = core.conversations.get('retained')!.running_turn!;
  const stopped = core.conversations.get('stopping')!;
  const result: Record<string, unknown> = {
    adopted: [running.token.turn_number, [...running.pending_user_input_questions.keys()].sort()],
    stopping: [stopped.running_turn === null, stopped.child_is_quarantined],
  };
  const publication = new SignalPublicationCoordinator({
    database,
    require_runtime_ownership: () => core.require_runtime_ownership(),
  });
  publication.bind_runtime((id) => core.runtime_turn(id));
  publication.initialize();
  const attachment = publication.attach_signal_publication({
    install: (_tx, snapshot) => {
      result.first_signal = snapshot.values.find(
        (value) => value.conversation_id === 'retained',
      )!.attention.awaiting_answer;
    },
    participate: () => {},
    unavailable: () => {},
  });
  attachment.close();
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
  cycle();
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
  database.write((tx) => accept_lease_in_transaction(tx, spec.fresh_lease, 1000000));
  cycle();
  result.stale = delivery_fate_json(
    await core.send('retained', text_message_content('wait'), { sender_label: 'owner' }),
  );
  process.stdout.write(JSON.stringify(result));
} finally {
  await close();
  database.close();
  Date.now = original_now;
}
