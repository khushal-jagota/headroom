/** Source-paired run custody and restart recovery without a host or native child. */
import { readFileSync } from 'node:fs';
import { createDatabase } from '../../core/database/index.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import {
  ResolvedConversationStart,
  ConversationRoleMaterials,
} from '../../conversation/contracts.ts';
import type {
  BackendChildFactory,
  RetainedBackendChildFactory,
} from '../../conversation/backends/contracts.ts';
import { as_object, decode_value, encode_value } from '../protocol.ts';
import type { WireTypes } from '../wire.generated.ts';
import { MacPresenceService } from '../vps_presence.ts';
import { SqliteCommandMailbox } from '../vps_mailbox.ts';
import { MacRuns } from '../vps_runs.ts';
import { close_run_and_record_mac_process_interruption } from '../interruption_coordinator.ts';
import { LocaleBackendChildFactory } from '../routing.ts';
import { read_canonical_skill_snapshot } from '../data/skills.ts';
const spec = JSON.parse(readFileSync(0, 'utf8'));
const database = createDatabase(spec.path),
  now = spec.now as number;
const mailbox = new SqliteCommandMailbox(database, { clock: () => now });
const presence = new MacPresenceService(database, mailbox, {
  changes: new ChangeSignal(),
  clock: () => now,
});
let runs = new MacRuns(database, presence, mailbox);
const old = decode_value(spec.old, 'ExecutorIdentity'),
  fresh = decode_value(spec.fresh, 'ExecutorIdentity');
const children = spec.children.map((child: unknown) =>
  decode_value(child, 'ChildReference'),
) as WireTypes['ChildReference'][];
const trace: unknown[] = [],
  output: Record<string, unknown> = {};
const typed_command = (value: unknown) => decode_value(value, 'ChildCommand');
const lease = (waiting: string[]) => ({
  identity: fresh,
  process_id: 4242,
  mac_wall_clock_ms: now,
  queue_depth: waiting.length,
  oldest_item_age_ms: null,
  waiting_conversations: waiting.map((conversation_id) => ({
    conversation_id,
    oldest_sequence: 1,
  })),
  waiting_conversations_truncated: false,
  working_turns: [],
  admission_stopped: false,
  going_to_sleep: false,
});
function snapshot() {
  return {
    runs: runs.store.retained_runs(),
    assignments: database.read((sql) =>
      sql.all('SELECT * FROM executor_child_assignments ORDER BY executor_incarnation,child_id'),
    ),
    stalls: database.read((sql) => runs.stall_source().tracked_waits(sql, now)),
    claims: database.read((sql) =>
      sql.all('SELECT id,worker_step_claim,worker_step_claim_revision FROM tickets ORDER BY id'),
    ),
    commands: database.read((sql) =>
      sql
        .all<{ command_id: string; state: string; outcome_json: string | null }>(
          'SELECT command_id,state,outcome_json FROM executor_commands ORDER BY command_sequence',
        )
        .map((row) => ({
          ...row,
          outcome_json: row.outcome_json === null ? null : JSON.parse(row.outcome_json),
        })),
    ),
  };
}
function attach() {
  runs.conversations = {
    async emit_answer(_turn, emit) {
      await emit();
    },
    async retire_replaced_remote_child(reference, options) {
      trace.push({ retire: reference, options });
      close_run_and_record_mac_process_interruption(database, reference, options.replaced_by, now, {
        undelivered_message:
          options.undelivered_prompt === null || options.undelivered_prompt === undefined
            ? null
            : 'prompt',
      });
    },
    async resume_undelivered_prompt(reference, prompt, options) {
      // The core requires native pieces rather than opaque protocol dictionaries.
      const { require_message_content } = await import('../../conversation/message_content.ts');
      require_message_content(prompt.content);
      require_message_content(prompt.sender_content);
      trace.push({ resume: reference, prompt, options });
    },
    async await_late_steer(conversation_id, sequence, verdict) {
      trace.push({ watch: conversation_id, sequence });
      void verdict.then((not_delivered) => trace.push({ verdict: conversation_id, not_delivered }));
    },
    async confirm_remote_child_termination(reference) {
      trace.push({ terminated: reference });
    },
    async stop_interrupted_remote_child() {
      throw new Error('unexpected interrupted child');
    },
  };
}
async function drain_tasks() {
  await Promise.all([...(runs as unknown as { tasks: Set<Promise<unknown>> }).tasks]);
}
async function error(work: () => unknown | Promise<unknown>) {
  try {
    await work();
    return null;
  } catch (error) {
    const caught = error as Error;
    return { type: caught.constructor.name, message: caught.message };
  }
}
const original_now = Date.now;
Date.now = () => now;
try {
  for (const child of children) runs.store.register_child(child, 'ticket', spec.ticket, now);
  await mailbox.put_child_command(typed_command(spec.steer));
  await mailbox.put_child_command(typed_command(spec.answer));
  await mailbox.poll({ ...old, connection_generation: old.connection_generation ?? 0 }, 0, 0);
  const recorded = await mailbox.record_results(old, {
    outcomes: [
      {
        command_id: spec.answer.command_id,
        child_outcome: {
          command_id: spec.answer.command_id,
          child: spec.answer.child,
          disposition: 'succeeded',
          result: {},
        },
        model_list: null,
      },
    ],
  });
  if (JSON.stringify(recorded.recorded) !== JSON.stringify([spec.answer.command_id]))
    throw new Error('the permission answer did not finish before replacement');
  await mailbox.put_child_command(typed_command(spec.prompt));
  await mailbox.put_child_command(typed_command(spec.input_answer));
  output.before = snapshot();
  await presence.accept_lease(decode_value(lease(['drain', 'late']), 'LeaseRequest'));
  await runs.stop({ deadline: performance.now() / 1000 + 2 });
  runs = new MacRuns(database, presence, mailbox);
  attach();
  await runs.resume_replacement_drains();
  output.waiting = snapshot();
  await mailbox.poll({ ...fresh, connection_generation: fresh.connection_generation ?? 0 }, 0, 0);
  await presence.accept_lease(decode_value(lease([]), 'LeaseRequest'));
  await drain_tasks();
  output.drained = snapshot();
  output.retained_recovery = runs.recover(children[0]!) === null;
  const next = {
    ...children[0]!,
    executor_incarnation: fresh.executor_incarnation,
    child_id: 'next',
  };
  runs.store.register_child(next, 'ticket', spec.ticket, now);
  runs.store.close_child(children[0]!, now + 1);
  output.historical_authority = await error(() => runs.authority(children[0]!));
  output.next_authority = runs.authority(next);
  output.exact_stop_refusal = await error(() =>
    runs.validate_stop({
      command_id: spec.prompt.command_id,
      child: next,
      disposition: 'succeeded',
      result: {},
    }),
  );
  output.recovered_history = runs.recover(next)?.initial_history_complete;
  await runs.resume_replacement_drains();
  await drain_tasks();
  await mailbox.put_child_command(
    typed_command({ ...spec.prompt, command_id: 'rewritten', child: next }),
  );
  await runs.resume_replacement_drains();
  await drain_tasks();
  output.interruptions = database.read((sql) =>
    sql.all(
      'SELECT interruption_id,recovery_state,detail FROM deploy_interruptions ORDER BY interruption_id',
    ),
  );
  output.after_rewrite = snapshot();
  output.skills = database.read((sql) => read_canonical_skill_snapshot(sql, spec.ticket));
  const choices: string[] = [];
  const local: BackendChildFactory = () => {
    choices.push('local');
    return {} as ReturnType<BackendChildFactory>;
  };
  const remote = Object.assign(
    () => {
      choices.push('remote');
      return {} as ReturnType<BackendChildFactory>;
    },
    {
      retained_runs: () => [],
      recover: () => {
        choices.push('recover');
        return null;
      },
    },
  ) as RetainedBackendChildFactory;
  const router = new LocaleBackendChildFactory(database, local, remote);
  for (const ticket of [spec.ticket, spec.local_ticket, null]) {
    const start = new ResolvedConversationStart({
      conversation_id: 'routing',
      backend_key: 'codex',
      model: 'fixture',
      reasoning_effort: null,
      workspace_folder: '/tmp',
      access: 'full',
      role_materials:
        ticket === null
          ? null
          : new ConversationRoleMaterials({
              role_text: 'fixture',
              identity_environment_variables: [
                ['PLAN_ACTOR', 'worker'],
                ['PLAN_TICKET_ID', ticket],
              ],
            }),
    });
    router.create({
      resolved_start: start,
      event_sink: { async report() {} },
      message_files: {} as Parameters<BackendChildFactory>[0]['message_files'],
    });
  }
  router.recover(next);
  output.choices = choices;
  output.trace = trace.sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b), 'en'));
  await runs.stop({ deadline: performance.now() / 1000 + 2 });
  output.sealed = await error(() => runs.resume_replacement_drains());
  const third = { ...fresh, executor_incarnation: '33333333-3333-4333-8333-333333333333' };
  await presence.accept_lease(
    decode_value({ ...lease(['drain']), identity: third }, 'LeaseRequest'),
  );
  runs = new MacRuns(database, presence, mailbox);
  await runs.resume_replacement_drains();
  let unrelated_drained = false;
  const unrelated = presence.wait_until_drained('drain').then(() => {
    unrelated_drained = true;
  });
  await runs.stop({ deadline: performance.now() / 1000 + 2 });
  await runs.settled();
  output.pending_shutdown = { unrelated_survives: !unrelated_drained };
  await presence.accept_lease(decode_value({ ...lease([]), identity: third }, 'LeaseRequest'));
  await unrelated;
  output.pending_shutdown = { ...(output.pending_shutdown as object), unrelated_drained };
  runs = new MacRuns(database, presence, mailbox);
  let retry_enter!: () => void;
  const retry_entered = new Promise<void>((resolve) => {
    retry_enter = resolve;
  });
  runs.conversations = {
    async emit_answer(_turn, emit) {
      await emit();
    },
    async retire_replaced_remote_child() {
      retry_enter();
      throw new Error('fixture retirement failed');
    },
    async resume_undelivered_prompt() {},
    async await_late_steer() {},
    async confirm_remote_child_termination() {},
    async stop_interrupted_remote_child() {},
  };
  const original_error = console.error;
  let retry_errors = 0;
  console.error = (...values: unknown[]) => {
    if (String(values[0]).startsWith('The run of a replaced Mac process failed to retire'))
      retry_errors++;
    else original_error(...values);
  };
  try {
    await runs.resume_replacement_drains();
    await retry_entered;
    await runs.stop({ deadline: performance.now() / 1000 + 2 });
    await runs.settled();
    output.retry_shutdown = retry_errors;
  } finally {
    console.error = original_error;
  }
} finally {
  Date.now = original_now;
  await runs.stop({ deadline: performance.now() / 1000 + 2 });
  await presence.stop({ deadline: performance.now() / 1000 + 2 });
  await mailbox.stop({ deadline: performance.now() / 1000 + 2 });
  database.close();
}
process.stdout.write(JSON.stringify(output));
