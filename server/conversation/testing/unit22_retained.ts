/** Retained core proof over the real run store; no provider or listener starts. */
import { readFileSync } from 'node:fs';
import { createDatabase } from '../../core/database/index.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import { ConversationStore } from '../record/data/store.ts';
import { ConversationMessageFiles } from '../record/message_files.ts';
import { SqliteProcessConversationSystem } from '../system.ts';
import { retained_open_runs } from '../runtime/remote.ts';
import { text_message_content } from '../message_content.ts';
import { delivery_fate_json } from '../http.ts';
import { InboxStore } from '../../executor/data/inbox.ts';
import { FileStaging } from '../../executor/vps_files.ts';
import { ExecutorStore } from '../../executor/data/runs.ts';
import { MacRuns } from '../../executor/vps_runs.ts';
import { SqliteCommandMailbox } from '../../executor/vps_mailbox.ts';
import { MacPresenceService } from '../../executor/vps_presence.ts';
import { accept_lease_in_transaction } from '../../executor/vps_presence_store.ts';
import { as_object, parse_identity } from '../../executor/protocol.ts';
import type { WireValue } from '../../executor/contracts.ts';
import { ToolCallStarted, TurnEnded } from '../backends/contracts.ts';
import { deployment_participants } from '../deploy_interruptions/participants.ts';
import { run_automatic_recovery } from '../deploy_interruptions/coordinator.ts';
import { executor_remote_recovery } from '../../lifecycle/production.ts';
import { is_memory_notice_sender_message_id } from '../../runtime/worker_memory.ts';
import { SignalPublicationCoordinator } from '../publication/coordinator.ts';
import { UserInputAnswer, UserInputQuestion } from '../events.ts';
import { BackendUserInputRequest, UserInputRequested } from '../backends/contracts.ts';
const spec = JSON.parse(readFileSync(0, 'utf8')) as {
  path: string;
  old: WireValue<'ExecutorIdentity'>;
  fresh: WireValue<'ExecutorIdentity'>;
  child: WireValue<'ChildReference'>;
  turn: WireValue<'TurnToken'>;
  asks: Record<string, { permission: string; user_input: string }>;
};
const database = createDatabase(spec.path),
  trace: unknown[] = [],
  old = parse_identity(spec.old);
const original_now = Date.now;
Date.now = () => 1000000;
let core!: SqliteProcessConversationSystem,
  runs!: MacRuns,
  mailbox!: SqliteCommandMailbox,
  presence!: MacPresenceService;
let initial_awaiting: Record<string, boolean> = {};
const resumes: Promise<void>[] = [];
function cycle() {
  mailbox = new SqliteCommandMailbox(database, { clock: () => 1000000 });
  presence = new MacPresenceService(database, mailbox, {
    changes: new ChangeSignal(),
    clock: () => 1000000,
  });
  runs = new MacRuns(database, presence, mailbox);
  const factory = runs.as_factory(),
    factories = { codex: factory, hermes: factory, claude: factory };
  const store = new ConversationStore(database, {
    ...deployment_participants(spec.path),
    integer_now: () => 1000,
    is_memory_notice: is_memory_notice_sender_message_id,
  });
  database.write((tx) => store.reconcile_in_transaction(tx, retained_open_runs(factories)), {
    changeSignal: 'silent',
  });
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
  const resume = core.resume_undelivered_prompt.bind(core);
  core.resume_undelivered_prompt = (...args) => {
    const task = resume(...args);
    resumes.push(task);
    return task;
  };
  const publication = new SignalPublicationCoordinator({
    database,
    require_runtime_ownership: () => core.require_runtime_ownership(),
  });
  publication.bind_runtime((id) => core.runtime_turn(id));
  publication.initialize();
  initial_awaiting = {};
  const attachment = publication.attach_signal_publication({
    install: (_tx, snapshot) => {
      initial_awaiting = Object.fromEntries(
        snapshot.values.map((value) => [value.conversation_id, value.attention.awaiting_answer]),
      );
    },
    participate: () => {},
    unavailable: () => {},
  });
  attachment.close();
  runs.conversations = core;
}
function capture(name: string, reply: unknown = null, id = 'retained') {
  const state = core.conversations.get(id)!,
    running = state.running_turn;
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
  const custody = database.read((sql) =>
    Object.fromEntries(
      ['mac_runs', 'executor_child_assignments', 'mac_inbox_positions', 'deploy_interruptions'].map(
        (table) => [
          table,
          sql.all(
            `SELECT * FROM ${table} ORDER BY ${table === 'executor_child_assignments' ? 'conversation_id,executor_incarnation,child_id' : '1,2'}`,
          ),
        ],
      ),
    ),
  );
  custody.commands = database
    .read((sql) =>
      sql.all<{ arguments: string }>(
        "SELECT child_id,operation,turn_number,state,json_extract(command_json,'$.child_command.arguments') AS arguments FROM executor_commands ORDER BY command_sequence",
      ),
    )
    .map((row) => ({ ...row, arguments: JSON.parse(row.arguments) }));
  trace.push({
    name,
    reply,
    rows,
    custody,
    initial_awaiting,
    allocated_children: database.read((sql) =>
      sql.all(
        "SELECT conversation_id,child_id AS identity FROM executor_child_assignments WHERE conversation_id='closed-never-ran' AND child_id<>'closed-old' ORDER BY rowid",
      ),
    ),
    runtime: {
      running: running !== null,
      token: running?.token ?? null,
      callbacks_revoked: running?.callbacks_revoked ?? null,
      memory_notice_only: running?.memory_notice_only ?? null,
      permission_ids: [...(running?.pending_permission_ask_ids ?? [])].sort(),
      input_ids: [...(running?.pending_user_input_questions.keys() ?? [])].sort(),
      questions: Object.fromEntries(running?.pending_user_input_questions ?? []),
      quarantined: state.child_is_quarantined,
      child: state.child !== null,
      reference: state.retained_child_reference,
    },
  });
}
async function close() {
  const deadline = performance.now() / 1000 + 5;
  await core.shutdown({ deadline });
  await runs.stop({ deadline });
  await presence.stop({ deadline });
  await mailbox.stop({ deadline });
  await Promise.all([runs.settled(), presence.settled(), mailbox.settled()]);
}
cycle();
try {
  capture('adopted-asks');
  capture('stopping-run-quarantined', null, 'stopping');
  if (
    core.conversations.get('stopping')!.running_turn !== null ||
    !core.conversations.get('stopping')!.child_is_quarantined
  )
    throw new Error('stopping run was adopted live');
  capture('closed-run-before-recovery', null, 'closed-never-ran');
  if (
    runs.store
      .retained_runs()
      .some(
        (run) => run.reference.conversation_id === 'closed-never-ran' && !run.retirement_confirmed,
      )
  )
    throw new Error('crash fixture already registered a fresh child');
  await run_automatic_recovery(core, database);
  capture('closed-run-message-recovered', null, 'closed-never-ran');
  await run_automatic_recovery(core, database);
  capture('closed-run-recovery-not-repeated', null, 'closed-never-ran');
  const recovered = database.read((sql) =>
    sql.all<{ command_json: string }>(
      "SELECT command_json FROM executor_commands WHERE conversation_id='closed-never-ran' AND operation='write_prompt'",
    ),
  );
  if (recovered.length !== 1) throw new Error('startup did not recover the closed run once');
  const recovery_text = JSON.parse(recovered[0]!.command_json)
    .child_command.arguments.content.map((piece: { text: string }) => piece.text)
    .join('');
  if (
    !recovery_text.includes('Inspect the interrupted work') ||
    !recovery_text.includes('A Mac restart ended this turn.') ||
    !recovery_text.includes('never reached you') ||
    !recovery_text.includes('The message was: work')
  )
    throw new Error('startup lost the closed run or never-ran message notice');
  const claim = database.read((sql) =>
    sql.get<{ worker_step_claim: string; worker_step_claim_revision: number }>(
      "SELECT worker_step_claim,worker_step_claim_revision FROM tickets WHERE id='t-closed-never-ran'",
    ),
  )!;
  if (claim.worker_step_claim !== 'out' || claim.worker_step_claim_revision !== 9)
    throw new Error('startup did not restore the captured claim');
  if (
    database.read((sql) =>
      sql.get<{ recovery_state: string }>(
        "SELECT recovery_state FROM deploy_interruptions WHERE source_conversation_id='closed-never-ran'",
      ),
    )?.recovery_state !== 'resumed'
  )
    throw new Error('startup did not settle the captured recovery');
  if (!initial_awaiting.retained || !initial_awaiting.cancelled)
    throw new Error('initial Signal omitted an adopted ask');
  const answers = [
    new UserInputAnswer({ question_id: 'timing', answers: ['Now'] }),
    new UserInputAnswer({ question_id: 'scope', answers: ['Backend'] }),
  ];
  const answer_both = async (id: string) => [
    await core.answer_permission_ask(id, spec.asks[id]!.permission, 'allow-once'),
    await core.answer_user_input(id, spec.asks[id]!.user_input, answers),
  ];
  if (presence.is_present()) throw new Error('answer fixture must keep the Mac away');
  for (const id of ['retained', 'cancelled'])
    if (JSON.stringify(await answer_both(id)) !== '["saved","saved"]')
      throw new Error('away answers were not saved');
  capture('away-answers-saved');
  const cancelled = core.conversations.get('cancelled')!;
  // The exact crash point: revocation and local attempt committed, before native dispatch.
  await cancelled.lock.run(async () => {
    await core.revoke_turn_requests(cancelled, cancelled.running_turn!);
    await core.begin_attempt(cancelled, cancelled.running_turn!.token, 'cancel');
  });
  await close();
  cycle();
  capture('cancel-attempt-adopted', null, 'cancelled');
  if (!core.conversations.get('cancelled')!.running_turn!.callbacks_revoked)
    throw new Error('cancelled callbacks reopened');
  if (JSON.stringify(await answer_both('cancelled')) !== '["refused","refused"]')
    throw new Error('cancelled ask accepted another answer');
  const late = await core.take_backend_event(
    { conversation_id: 'cancelled', turn_number: 1 },
    new UserInputRequested({
      request: new BackendUserInputRequest({
        request_id: 'late',
        questions: [
          new UserInputQuestion({
            question_id: 'late',
            header: 'Late',
            question: 'Late?',
            options: [],
            multi_select: false,
            allow_other: true,
          }),
        ],
      }),
    }),
  );
  if (late.taken) throw new Error('cancelled turn accepted a late ask');
  capture('cancelled-late-ask-refused', null, 'cancelled');
  await mailbox.poll(old, 0, 0);
  const expired = database.read((sql) =>
    sql.all<{ state: string }>(
      "SELECT state FROM executor_commands WHERE conversation_id='cancelled'",
    ),
  );
  if (expired.length !== 2 || expired.some((row) => row.state !== 'expired'))
    throw new Error('cancelled answers reached the mailbox consumer');
  capture('cancelled-answers-expired', null, 'cancelled');
  for (const id of ['notice', 'notice-steer']) {
    capture(id + '-adopted', null, id);
    await core.take_backend_event(
      { conversation_id: id, turn_number: 1 },
      new ToolCallStarted({ tool_call_id: 'tool', title: 'ls', tool_kind: 'execute' }),
    );
    await core.take_backend_event(
      { conversation_id: id, turn_number: 1 },
      new TurnEnded({ ending: 'completed' }),
    );
    const record = core.options.store.read_conversation(id)!;
    if (
      record.latest_agent_activity_sequence !== (id === 'notice-steer' ? record.latest_sequence : 0)
    )
      throw new Error('adopted maintenance turn changed activity eligibility');
    capture(id + '-ended', null, id);
  }
  await runs.resume_replacement_drains();
  await Promise.all(resumes.splice(0));
  capture('fresh-registered-resumed', null, 'rewrite');
  await runs.resume_replacement_drains();
  await Promise.all(resumes.splice(0));
  capture('fresh-registered-no-repeat', null, 'rewrite');
  const rewritten = database.read((sql) =>
    sql.all<{ command_json: string }>(
      "SELECT command_json FROM executor_commands WHERE child_id='fresh' AND operation='write_prompt'",
    ),
  );
  if (rewritten.length !== 1)
    throw new Error('startup did not write the registered child prompt once');
  const prompt = JSON.parse(rewritten[0]!.command_json).child_command.arguments;
  if (JSON.stringify(prompt.content) !== JSON.stringify(text_message_content('role\n\nwork')))
    throw new Error('startup changed the owed prompt');
  const inbox = new InboxStore(database, new FileStaging(spec.path));
  const answer_ids = database.read((sql) =>
    sql.all<{ command_id: string; operation: string }>(
      "SELECT command_id,operation FROM executor_commands WHERE conversation_id='retained' ORDER BY command_sequence",
    ),
  );
  await mailbox.record_results(old, {
    outcomes: answer_ids.map(({ command_id }) => ({
      command_id,
      child_outcome: { command_id, child: spec.child, disposition: 'succeeded', result: {} },
      model_list: null,
    })),
  });
  for (const [index, row] of answer_ids.entries()) {
    const kind = row.operation === 'answer_permission_ask' ? 'permission' : 'user_input';
    const envelope: WireValue<'QueuedEnvelope'> = {
      sender_id: 'sender',
      conversation_id: 'retained',
      sequence: index + 1,
      submission_id: 'saved-' + kind,
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
    const provider = kind === 'permission' ? 'ask-1' : 'input-1';
    const accepted = await core.take_answer_application(
      spec.turn,
      row.command_id,
      provider,
      kind,
      participant,
    );
    capture('saved-' + kind + '-applied', { result: accepted.result, taken: accepted.taken });
    const replay = await core.take_answer_application(
      spec.turn,
      row.command_id,
      provider,
      kind,
      participant,
    );
    capture('saved-' + kind + '-replay', { result: replay.result, taken: replay.taken });
  }
  const refused = await answer_both('retained');
  if (JSON.stringify(refused) !== '["refused","refused"]')
    throw new Error('applied answer accepted another answer');
  capture('applied-answers-refuse-reanswer', refused);
  await close();
  database.write((tx) =>
    accept_lease_in_transaction(
      tx,
      {
        identity: spec.fresh,
        process_id: 4242,
        mac_wall_clock_ms: 1000000,
        queue_depth: 0,
        oldest_item_age_ms: null,
        waiting_conversations: [],
        waiting_conversations_truncated: false,
        working_turns: [],
        admission_stopped: false,
        going_to_sleep: false,
      },
      1000000,
    ),
  );
  cycle();
  const fate = await core.send('retained', text_message_content('wait behind stale child'), {
    sender_label: 'owner',
    sender_message_id: 'queued',
  });
  capture('stale-fenced', delivery_fate_json(fate));
  await core.confirm_remote_child_termination({ ...spec.child, child_id: 'foreign' });
  capture('foreign-proof-ignored');
  new ExecutorStore(database).close_child(spec.child, 1000000);
  await core.confirm_remote_child_termination(spec.child);
  capture('exact-retirement');
  process.stdout.write(JSON.stringify(trace));
} finally {
  await close();
  database.close();
  Date.now = original_now;
}
