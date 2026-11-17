/** Source-paired run custody and restart recovery without a host or native child. */
import { parse_backend_instance_id } from '../../drivers/core/identity.ts';
import { registry_of_factories } from '../../drivers/testing/registry.ts';
import { readFileSync } from 'node:fs';
import { CLEANUP_SCOPE } from '../../worker_supervision/inspection.ts';
import { createDatabase } from '../../core/database/index.ts';
import { ChangeSignal } from '../../core/changes/index.ts';
import { TestClock } from '../../core/clock.ts';
import { HOME_MACHINE_ID, valid_uuid } from '../../execution_identity.ts';
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
import { VpsExecutorRegistry } from '../registry.ts';
import { close_run_and_record_mac_process_interruption } from '../interruption_coordinator.ts';
import { LocaleBackendChildFactory } from '../routing.ts';
import { read_canonical_skill_snapshot } from '../data/skills.ts';
import { ConversationStore } from '../../conversation/record/data/store.ts';
import { ConversationMessageFiles } from '../../conversation/record/message_files.ts';
import { SqliteProcessConversationSystem } from '../../conversation/system.ts';
import { adopted_running_turn } from '../../conversation/logic/retained_turn.ts';
import { accept_lease_in_transaction } from '../vps_presence_store.ts';
import { isDeepStrictEqual } from 'node:util';
import { write_bindings } from '../../conversation/start_admission/index.ts';
import { setImmediate as yield_turn } from 'node:timers/promises';
import type { PresenceRecord } from '../contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import { RETENTION_SECONDS } from '../../ticket_workspaces/contracts.ts';
const spec = JSON.parse(readFileSync(0, 'utf8'));
// The Python reference has no pending prompt admission field.
const comparable_runs = (runs: readonly { pending_prompt: unknown }[]) =>
  runs.map(({ pending_prompt: _, ...run }) => run);
if (spec.mode === 'machines') {
  Date.now = () => spec.now;
  const db = createDatabase(spec.path);
  const clock = new TestClock(new Date(spec.now).toISOString());
  const options = {
    db_path: spec.path,
    changes: new ChangeSignal(),
    media_signal: new AbortController().signal,
  };
  const registry = new VpsExecutorRegistry(db, clock, {
    ...options,
    original_enabled: true,
  });
  db.read((sql) => registry.load(sql));
  const endpoints = (spec.machines as string[]).map((machine) => registry.get(machine)!);
  const owners = endpoints.map((endpoint) => endpoint.runs);
  const references = spec.children as WireTypes['ChildReference'][];
  const attempt = (work: () => unknown) => {
    try {
      work();
      return null;
    } catch (error) {
      return (
        (error as { code?: string; constructor: { name: string } }).code ??
        (error as Error).constructor.name
      );
    }
  };
  const rows = () =>
    db.read((sql) => ({
      runs: sql.all('SELECT * FROM mac_runs ORDER BY conversation_id'),
      assignments: sql.all(
        'SELECT * FROM executor_child_assignments ORDER BY executor_incarnation,child_id',
      ),
      commands: sql.all('SELECT * FROM executor_commands ORDER BY command_sequence'),
    }));
  const snapshot = () =>
    owners.map((owner) => ({
      machine_id: owner.machine_id,
      retained: comparable_runs(owner.retained_runs()),
      unresolved: owner.store.unresolved_runs_of_replaced_incarnations(),
      owns: references.map((child) => owner.owns_conversation(child.conversation_id)),
      stalls: db.read((sql) => owner.stall_source().tracked_waits(sql, spec.now)),
    }));
  const prompt_acknowledgement = async () => {
    const owner = owners[0]!,
      endpoint = endpoints[0]!,
      mailbox = owner.mailbox;
    const source = references[0]!,
      turn = { conversation_id: source.conversation_id, turn_number: 3 };
    db.write((tx) =>
      write_bindings(tx, turn, [
        {
          identity: 'ack-proof',
          work_class: 'worker',
          destination: spec.machines[0],
        },
      ]),
    );
    const factory = registry.as_factory();
    const core = new SqliteProcessConversationSystem({
      store: new ConversationStore(db),
      backend_registry: registry_of_factories({ codex: factory, claude: factory, hermes: factory }),
      message_files: endpoint.message_files,
      delivery_model_refusal: () => null,
      execution_refusal: () => null,
    });
    const previous = owner.conversations;
    owner.conversations = core;
    const confirmations: Record<string, unknown>[] = [];
    const original = core.confirm_remote_prompt_accepted.bind(core);
    const bindings = () =>
      db.read((sql) => sql.all('SELECT * FROM automatic_start_bindings ORDER BY identity'));
    core.confirm_remote_prompt_accepted = async (reference, token) => {
      const fresh = createDatabase(spec.path);
      try {
        const row = fresh.read((sql) =>
          sql.get<{ state: string; outcome_json: string }>(
            "SELECT state,outcome_json FROM executor_commands WHERE command_id='ack-proven'",
          ),
        );
        confirmations.push({
          child: reference,
          turn: token,
          committed: row ? { state: row.state, outcome: JSON.parse(row.outcome_json) } : null,
        });
      } finally {
        fresh.close();
      }
      await original(reference, token);
    };
    const prompt = decode_value(
      {
        command_id: 'ack-generic',
        child: source,
        operation: 'write_prompt',
        turn,
        arguments: {
          kind: 'write_prompt',
          content: [{ type: 'text', text: 'ack' }],
          sender_content: [{ type: 'text', text: 'ack' }],
          sender_label: 'fixture',
          mode: 'queue',
        },
      },
      'ChildCommand',
    );
    const current = endpoint.presence.current_identity()!;
    const outcome = (
      payload: WireTypes['JsonObject'],
      child = source,
      disposition: WireTypes['CommandOutcome']['disposition'] = 'succeeded',
    ): WireTypes['CommandOutcome'] => ({
      command_id: 'ack-generic',
      child,
      disposition,
      result: payload,
    });
    const settle = async (command: WireTypes['ChildCommand'], payload: WireTypes['JsonObject']) => {
      const placed = await mailbox.put_child_command(command);
      if (!('command_sequence' in placed)) throw new Error('Prompt was not admitted');
      await mailbox.poll(current, 0, 0);
      await mailbox.record_results(current, {
        outcomes: [
          {
            command_id: command.command_id,
            child_outcome: { ...outcome(payload), command_id: command.command_id },
            model_list: null,
          },
        ],
      });
    };
    try {
      const before = bindings();
      await settle(prompt, { kind: 'prompt_accepted', composed_content_delivered: true });
      for (const [command, result] of [
        [prompt, outcome({ agent_acceptance_proven: false })],
        [prompt, outcome({ agent_acceptance_proven: true }, { ...source, child_id: 'wrong' })],
        // A native refusal reconciles its turn. A mailbox settlement only must not confirm.
        [
          prompt,
          outcome(
            { agent_acceptance_proven: true, reason: 'executor_process_replaced' },
            source,
            'refused',
          ),
        ],
      ] as const)
        mailbox.settled_listener!(
          {
            command_sequence: 1,
            command_id: command.command_id,
            created_at_vps_ms: spec.now,
            child_command: command,
            model_list_request: null,
          },
          { command_id: command.command_id, child_outcome: result, model_list: null },
        );
      await yield_turn();
      const after_filters = bindings(),
        calls_after_filters = confirmations.length;
      await settle(
        { ...prompt, command_id: 'ack-proven' },
        {
          kind: 'prompt_accepted',
          composed_content_delivered: true,
          agent_acceptance_proven: true,
        },
      );
      await Promise.all([...(owner as unknown as { tasks: Set<Promise<unknown>> }).tasks]);
      return {
        before,
        after_filters,
        calls_after_filters,
        confirmations,
        after_proven: bindings(),
      };
    } finally {
      await core.shutdown({ deadline: performance.now() / 1000 + 5 });
      owner.conversations = previous;
      db.write((tx) =>
        tx.run("DELETE FROM executor_commands WHERE command_id IN ('ack-generic','ack-proven')"),
      );
    }
  };
  const workspace_authority = async () => {
    const first = endpoints[0]!,
      second = endpoints[1]!;
    const current = first.presence.current_identity()!,
      other = second.presence.current_identity()!;
    const physical = {
      machine_id: '88888888-8888-4888-8888-888888888888',
      workspace_id: '99999999-9999-4999-8999-999999999999',
    };
    const registration = { child: references[0]!, workspace: physical };
    const workspace_rows = () =>
      db.read((sql) => ({
        registered: sql.all(
          'SELECT * FROM ticket_workspace_conversations ORDER BY conversation_id',
        ),
        grants: sql.all(
          'SELECT * FROM ticket_workspace_retirements ORDER BY ticket_id,machine_id,workspace_id',
        ),
        lifecycle: sql.all('SELECT * FROM ticket_workspace_lifecycle ORDER BY ticket_id'),
      }));
    const refused = async (work: () => Promise<unknown>) => {
      const before = workspace_rows();
      try {
        await work();
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        return { error: error.toPayload(), unchanged: isDeepStrictEqual(workspace_rows(), before) };
      }
      throw new Error('Workspace authority unexpectedly accepted the request');
    };
    db.write((tx) =>
      tx.run('UPDATE tickets SET machine_id=? WHERE id=?', [spec.machines[1], spec.local_ticket]),
    );
    const historical = { ticket_ids: [spec.ticket, 't_zzzzzzzz'] };
    const current_request = { ticket_ids: [spec.local_ticket] };
    const states = {
      historical: await first.workspace_states(current, historical),
      current: await second.workspace_states(other, current_request),
    };
    const historical_states = [states.historical, await second.workspace_states(other, historical)];
    const refusals = [
      await refused(() => first.workspace_states(current, current_request)),
      await refused(() =>
        second.register_workspace(
          { ...other, executor_incarnation: references[1]!.executor_incarnation },
          { ...registration, child: references[1]! },
        ),
      ),
      await refused(() =>
        first.register_workspace(current, {
          ...registration,
          child: { ...references[0]!, child_id: 'missing-child' },
        }),
      ),
      await refused(() =>
        first.register_workspace(current, { ...registration, child: references[1]! }),
      ),
    ];
    await first.register_workspace(current, registration);
    const registered = workspace_rows().registered;
    const ended = spec.now / 1000 - RETENTION_SECONDS - 1;
    const state = { ticket_id: spec.ticket, ended_at: ended, deleted: false };
    db.write((tx) =>
      tx.run('UPDATE ticket_workspace_lifecycle SET ended_at=? WHERE ticket_id=?', [
        ended,
        spec.ticket,
      ]),
    );
    refusals.push(
      await refused(() => second.retire_workspace(other, state, physical)),
      await refused(() =>
        first.retire_workspace(current, state, {
          ...physical,
          workspace_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        }),
      ),
    );
    const retirement = [
      (await first.retire_workspace(current, state, physical, { replay: true })).authorized,
      (await first.retire_workspace(current, { ...state, ended_at: ended - 1 }, physical))
        .authorized,
      (await first.retire_workspace(current, state, physical)).authorized,
    ];
    db.write((tx) =>
      tx.run("UPDATE tickets SET worker_step_claim='none' WHERE id=?", [spec.ticket]),
    );
    retirement.push((await first.retire_workspace(current, state, physical)).authorized);
    db.write((tx) =>
      tx.run('UPDATE ticket_workspace_lifecycle SET ended_at=NULL WHERE ticket_id=?', [
        spec.ticket,
      ]),
    );
    retirement.push(
      (await first.retire_workspace(current, state, physical)).authorized,
      (await first.retire_workspace(current, state, physical, { replay: true })).authorized,
    );
    return {
      states,
      historical: historical_states,
      physical,
      refused: refusals,
      registered,
      retirement,
      grants: workspace_rows().grants,
    };
  };
  const cleanup_recheck = async () => {
    const endpoint = endpoints[1]!,
      other = endpoints[0]!;
    const presence = endpoint.presence,
      mailbox = endpoint.mailbox;
    const base = presence.record()!;
    const current = presence.current_identity()!;
    const source = references[1]!;
    const requests: Record<string, unknown>[] = [],
      ids: string[] = [],
      budgets: boolean[] = [];
    const result: Record<string, unknown> = {};
    result.wrong_ticket = await registry.recheck_cleanup(source, 'missing-ticket');
    result.missing_source = await registry.recheck_cleanup(
      { ...source, child_id: 'missing-child' },
      spec.ticket,
    );
    try {
      await other.runs.recheck_cleanup(source, spec.ticket);
    } catch (error) {
      result.wrong_machine = (error as { code: string }).code;
    }
    result.mailbox_refused = await endpoint.runs.recheck_cleanup(source, 'missing-ticket');
    Date.now = () => spec.now + 61_000;
    try {
      result.unreachable = await registry.recheck_cleanup(source, spec.ticket);
    } finally {
      Date.now = () => spec.now;
    }
    const original_put = mailbox.put_child_command.bind(mailbox);
    const original_wait = mailbox.wait_result.bind(mailbox);
    const original_drain = presence.wait_until_drained.bind(presence);
    const memory = presence as unknown as {
      set_record(record: PresenceRecord): void;
      wake_drains(): void;
    };
    const execute = async (mode: string) => {
      const entered = Promise.withResolvers<void>();
      let reply: WireTypes['CommandResult'] | null = null;
      const performance_now = performance.now.bind(performance);
      const descriptor = Object.getOwnPropertyDescriptor(performance, 'now');
      mailbox.put_child_command = async (command, options) => {
        const placed = await original_put(command, options);
        if (!('command_sequence' in placed)) throw new Error('Cleanup command was not admitted');
        if (!valid_uuid(command.command_id) || ids.includes(command.command_id))
          throw new Error('Cleanup command identity was not unique');
        ids.push(command.command_id);
        requests.push({
          ...as_object(command, 'ChildCommand'),
          command_id: `cleanup-command-${ids.length}`,
        });
        await mailbox.poll(current, 0, 0);
        reply = {
          command_id: command.command_id,
          child_outcome: {
            command_id: command.command_id,
            child: command.child,
            disposition: mode === 'refused' ? 'refused' : 'succeeded',
            result: { complete: mode !== 'incomplete', reason: 'fixture cleanup' },
          },
          model_list: null,
        };
        await mailbox.record_results(current, { outcomes: [reply] });
        const row = mailbox.command_row(command.command_id);
        if (
          row?.command.command_id !== command.command_id ||
          row.result?.command_id !== command.command_id ||
          row.result.child_outcome?.command_id !== command.command_id
        )
          throw new Error('Cleanup result lost its exact command correlation');
        return placed;
      };
      mailbox.wait_result = async (id, timeout, options) => {
        budgets.push(timeout != null && timeout >= 0 && timeout <= 30);
        if (mode === 'no_result') return null;
        if (mode === 'expired') {
          Object.defineProperty(performance, 'now', {
            configurable: true,
            value: () => performance_now() + 31_000,
          });
          return reply;
        }
        return original_wait(id, timeout, options);
      };
      presence.wait_until_drained = async (conversation, options) => {
        entered.resolve();
        await original_drain(conversation, options);
      };
      try {
        if (mode === 'blocked') memory.set_record({ ...base, last_lease_at_ms: spec.now - 26_000 });
        const task = registry.recheck_cleanup(source, spec.ticket);
        let completed = false;
        void task.then(
          () => {
            completed = true;
          },
          () => {
            completed = true;
          },
        );
        if (mode === 'blocked') {
          await entered.promise;
          const held = [!completed];
          const lease = base.lease!;
          for (const changed of [
            { ...lease, waiting_conversations_truncated: true },
            {
              ...lease,
              waiting_conversations: [
                { conversation_id: source.conversation_id, oldest_sequence: 1 },
              ],
            },
            {
              ...lease,
              cleanup_uncertainties: [
                {
                  ticket_id: spec.ticket,
                  conversation_id: source.conversation_id,
                  reason: 'still pending',
                },
              ],
            },
            {
              ...lease,
              identity: {
                ...lease.identity,
                executor_incarnation: '99999999-9999-4999-8999-999999999999',
              },
            },
          ]) {
            memory.set_record({ ...base, last_lease_at_ms: spec.now, lease: changed });
            memory.wake_drains();
            await yield_turn();
            held.push(!completed);
          }
          memory.set_record(base);
          memory.wake_drains();
          result.held = held;
        }
        return await task;
      } finally {
        mailbox.put_child_command = original_put;
        mailbox.wait_result = original_wait;
        presence.wait_until_drained = original_drain;
        if (descriptor) Object.defineProperty(performance, 'now', descriptor);
        else Reflect.deleteProperty(performance, 'now');
      }
    };
    try {
      for (const mode of ['refused', 'incomplete', 'no_result', 'blocked', 'recent', 'expired'])
        result[mode] = await execute(mode);
      result.requests = requests;
      result.budgets = budgets;
      return result;
    } finally {
      memory.set_record(base);
      db.write((tx) => {
        for (const id of ids) tx.run('DELETE FROM executor_commands WHERE command_id=?', [id]);
      });
    }
  };
  let result;
  try {
    const named_registry = new VpsExecutorRegistry(db, clock, options);
    let named_only: boolean[];
    try {
      db.read((sql) => named_registry.load(sql));
      named_only = [
        named_registry.get(spec.machines[0]) === null,
        named_registry.get(spec.machines[1]) !== null,
      ];
    } finally {
      await named_registry.stop({ deadline: performance.now() / 1000 + 2 });
      await named_registry.settled();
    }
    const loaded = (spec.machines as string[]).map((machine) => registry.get(machine) !== null);
    const unknown = '77777777-7777-4777-8777-777777777777';
    const unknown_present = registry.is_present(unknown);
    const unknown_allocated = registry.get(unknown) !== null;
    const refused_destinations = [];
    for (const machine of [unknown, HOME_MACHINE_ID]) {
      try {
        await registry.ensure(machine);
        refused_destinations.push(false);
      } catch (error) {
        if (!(error instanceof Error) || error.message !== 'Unknown executor destination')
          throw error;
        refused_destinations.push(true);
      }
    }
    const catalogs = endpoints.map(
      (endpoint) => registry.catalog_for(endpoint.machine_id) === endpoint.catalog,
    );
    owners.forEach((owner, index) =>
      owner.store.register_child(references[index]!, 'ticket', spec.ticket, spec.now),
    );
    const before = snapshot(),
      original = rows();
    const refused = owners.map((owner, index) => {
      const foreign = references[1 - index]!;
      return [
        attempt(() => owner.recover(foreign)),
        attempt(() => owner.authority(foreign)),
        attempt(() => owner.store.has_closed_child_assignment(foreign)),
        attempt(() => owner.store.register_child(foreign, 'ticket', spec.ticket, spec.now)),
      ];
    });
    const unchanged = isDeepStrictEqual(rows(), original);
    const choices: string[] = [];
    const constructors = owners.map((owner) => owner.create);
    for (const owner of owners)
      owner.create = () => {
        choices.push(owner.machine_id);
        throw new Error('selection only');
      };
    const router = new LocaleBackendChildFactory(
      db,
      () => {
        choices.push('local');
        throw new Error('selection only');
      },
      registry.as_factory(),
    );
    const start = new ResolvedConversationStart({
      conversation_id: 'drain',
      backend_key: parse_backend_instance_id('codex'),
      model: 'fixture',
      reasoning_effort: null,
      role_materials: new ConversationRoleMaterials({
        role_text: 'fixture',
        identity_environment_variables: [
          ['PLAN_ACTOR', 'worker'],
          ['PLAN_TICKET_ID', spec.ticket],
        ],
      }),
      workspace_folder: '/tmp',
      access: 'full',
    });
    try {
      for (const machine of [...spec.machines, HOME_MACHINE_ID]) {
        db.write((tx) =>
          tx.run('UPDATE tickets SET machine_id=? WHERE id=?', [machine, spec.ticket]),
        );
        try {
          router.create({
            resolved_start: start,
            event_sink: { async report() {} },
            message_files: endpoints[0]!.message_files,
          });
        } catch (error) {
          if (!(error instanceof Error) || error.message !== 'selection only') throw error;
        }
      }
    } finally {
      owners.forEach((owner, index) => (owner.create = constructors[index]!));
    }
    const recovered = references.map(
      (reference) => registry.recover(reference)?.initial_history_complete,
    );
    const acknowledgement = await prompt_acknowledgement();
    db.write((tx) =>
      accept_lease_in_transaction(
        tx,
        decode_value(spec.replacement, 'LeaseRequest'),
        spec.now + 1,
        spec.machines[1],
      ),
    );
    const replaced = snapshot();
    const tagged_stalls = db.read((sql) =>
      registry
        .stall_sources()
        .flatMap((source) =>
          source
            .tracked_waits(sql, spec.now)
            .filter((wait) => wait.kind === 'mac_replacement_pending'),
        ),
    );
    const retained_recovery = owners[1]!.recover(references[1]!)?.initial_history_complete;
    // This trusted exact-reference participant intentionally remains global, as in Python.
    owners[0]!.store.close_child(references[1]!, spec.now + 2);
    const cleanup = await cleanup_recheck();
    const workspaces = await workspace_authority();
    result = {
      before,
      acknowledgement,
      cleanup_scope: CLEANUP_SCOPE,
      cleanup,
      workspaces,
      loaded,
      named_only,
      unknown_present,
      unknown_allocated,
      refused_destinations,
      catalogs,
      choices,
      tagged_stalls,
      refused,
      unchanged,
      recovered,
      replaced,
      retained_recovery,
      closed: owners[1]!.store.has_closed_child_assignment(references[1]!),
      closed_recovery: owners[1]!.recover(references[1]!) === null,
      rows: rows(),
    };
  } finally {
    const stop = { deadline: performance.now() / 1000 + 2 };
    await registry.stop(stop);
    await registry.settled();
    db.close();
  }
  await new Promise<void>((resolve, reject) =>
    process.stdout.write(JSON.stringify(result), (error) => (error ? reject(error) : resolve())),
  );
  process.exit(0);
}
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
const store = new ConversationStore(database);
const no_child: BackendChildFactory = () => {
  throw new Error('No child belongs to the answer gate');
};
const core = new SqliteProcessConversationSystem({
  store,
  backend_registry: registry_of_factories({ codex: no_child, claude: no_child, hermes: no_child }),
  message_files: new ConversationMessageFiles(spec.path),
  delivery_model_refusal: () => null,
  execution_refusal: () => null,
});
const answer_state = core.conversation_state('drain')!;
answer_state.running_turn = adopted_running_turn(
  { conversation_id: 'drain', turn_number: 3 },
  store.lifecycle_facts('drain'),
  new Map(),
  { executor_process_current: true },
);
if (answer_state.running_turn === null) throw new Error('Answer gate requires its live owner turn');
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
  cleanup_uncertainties: [],
  legacy_cleanup_notice: null,
});
function snapshot() {
  return {
    runs: comparable_runs(runs.store.retained_runs()),
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
    emit_answer: core.emit_answer.bind(core),
    confirm_remote_prompt_accepted: core.confirm_remote_prompt_accepted.bind(core),
    reconcile_remote_command: core.reconcile_remote_command.bind(core),
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
  attach();
  await mailbox.put_child_command(typed_command(spec.steer));
  output.answer_gate = [
    await runs.save_answer(
      typed_command({
        ...spec.answer,
        command_id: 'wrong-turn',
        turn: { conversation_id: 'drain', turn_number: 4 },
      }),
    ),
  ];
  answer_state.running_turn.callbacks_revoked = true;
  (output.answer_gate as boolean[]).push(
    await runs.save_answer(
      typed_command({
        ...spec.answer,
        command_id: 'revoked-answer',
      }),
    ),
  );
  answer_state.running_turn.callbacks_revoked = false;
  await runs.save_answer(
    typed_command({
      ...spec.answer,
      command_id: 'wrong-ask',
      arguments: { ...spec.answer.arguments, ask_id: 'closed-ask' },
    }),
  );
  (output.answer_gate as boolean[]).push(await runs.save_answer(typed_command(spec.answer)));
  const delivered = await mailbox.poll(
    { ...old, connection_generation: old.connection_generation ?? 0 },
    0,
    0,
  );
  if (delivered.commands.some((row) => row.command_id === 'wrong-ask'))
    throw new Error('A closed ask reached delivery');
  const wrong_ask = mailbox.command_row('wrong-ask')!;
  output.closed_ask = [wrong_ask.state, wrong_ask.result?.child_outcome?.result.reason];
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
      conversation_id: ticket === spec.ticket ? 'drain' : 'routing',
      backend_key: parse_backend_instance_id('codex'),
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
  attach();
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
    emit_answer: core.emit_answer.bind(core),
    confirm_remote_prompt_accepted: core.confirm_remote_prompt_accepted.bind(core),
    reconcile_remote_command: core.reconcile_remote_command.bind(core),
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
  await core.shutdown({ deadline: performance.now() / 1000 + 2 });
  await core.settled();
  database.close();
}
process.stdout.write(JSON.stringify(output));
