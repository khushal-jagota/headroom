/** Gap: persisted Mac presence cannot authorize a VPS-local adapter for a retained
 * conversation whose workspace policy is already home_relative. No HTTP/provider runs.
 */
import { dirname, join } from 'node:path';
import { readFileSync } from 'node:fs';
import { expect, test, vi } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import { create } from './ticket_fixture.ts';
import {
  write_ticket_conversation_start,
  claim_ticket_for_worker_step,
  read_ticket,
} from '../tickets/data/index.ts';
import { can_execute_locale } from '../tickets/locale_configuration.ts';
import { PROTOCOL_VERSION, SPOOL_FORMAT_VERSION } from '../execution_configuration.ts';
import { PlannerError, OWNER } from '../core/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { loadConfig } from '../core/config.ts';
import { TestClock } from '../core/clock.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { ConversationLiveTail } from '../conversation/live_tail.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import { ResolvedConversationStart } from '../conversation/contracts.ts';
import type {
  BackendChildFactory,
  RetainedBackendChildFactory,
} from '../conversation/backends/contracts.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import {
  conversation_execution_refusal,
  local_ticket_execution_refusal,
} from '../tickets/execution_admission.ts';
import {
  configured_ticket_execution_refusal,
  send_to_ticket_conversation,
} from '../runtime/conversation_start.ts';
import {
  start_ready_worker_step,
  WorkerStepStartResult,
} from '../runtime/worker_step_readiness_loop.ts';
import { is_ready_for_worker_step } from '../runtime/worker_step_readiness.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { restart_worker } from '../tickets/worker_restart.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { PromptQueueReason } from '../conversation/contracts.ts';
import {
  import_captured_turns,
  reconcile,
  bind_delivery,
  select_recoveries,
  admit_recovery,
  recovery_claim_revisions,
} from '../conversation/deploy_interruptions/data.ts';
import { run_automatic_recovery } from '../conversation/deploy_interruptions/coordinator.ts';
import { create_wake, claim_next_batch } from '../manager_wakes/data.ts';
import { ManagerWakeLoop, deliver_batch } from '../manager_wakes/runtime.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from '../conversation/snapshot/composition.ts';
import { compose_production_backends } from '../lifecycle/production_bindings.ts';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { OneShotChildren } from '../lifecycle/one_shot_children.ts';
import type { ConversationCompositionContext } from '../core/start.ts';

test('local composition refuses a retained Mac Ticket before constructing its selected child', async () => {
  const began = performance.now();
  let bound!: ReturnType<typeof compose_production_backends>;
  let database!: Database;
  const delegate: BackendChildFactory = (values) =>
    bound.factories[values.resolved_start.backend_key](values);
  const f = setup({
    record: {
      admit_recovery,
      recovery_claim_revisions,
      bind_interruption_delivery: (sql, id, turn, references, identities) =>
        bind_delivery(sql, id, turn, references, identities ?? undefined),
    },
    core: {
      execution_refusal: (id) =>
        database.read((sql) =>
          conversation_execution_refusal(sql, id, local_ticket_execution_refusal),
        ),
      backend_child_factories: {
        claude: delegate,
        codex: delegate,
        hermes: delegate,
      },
    },
  });
  database = f.database;
  const root = dirname(f.database_path);
  const config = loadConfig(join(root, 'absent-mac-guard.yaml'), {
    PLAN_DB_PATH: f.database_path,
    PLAN_TEST_MODE: '1',
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
  });
  const clock = new TestClock('2026-11-12T12:00:00+00:00');
  const child_processes = new ChildProcesses();
  const children = new OneShotChildren(child_processes);
  const live_tail = new ConversationLiveTail({
    view_output_directory: join(root, 'mac-guard-output'),
  });
  const http = create_backend_runtime({
    database: f.database,
    config,
    now: () => clock.now(),
    providers: unavailable_backend_providers(),
  });
  const local_constructor = vi.fn<BackendChildFactory>(() => {
    throw new Error('Synthetic local constructor boundary');
  });
  const runs: ReturnType<RetainedBackendChildFactory['retained_runs']> = [];
  const recover = vi.fn(function (
    this: BackendChildFactory,
    _reference: Parameters<RetainedBackendChildFactory['recover']>[0],
  ) {
    expect(this).toBe(local_constructor);
    return null;
  });
  Object.assign(local_constructor, {
    recover,
    retained_runs: function (this: BackendChildFactory) {
      expect(this).toBe(local_constructor);
      return runs;
    },
  });
  let recovery_core: SqliteProcessConversationSystem | undefined;
  try {
    await f.start();
    const ticket = create(f.database, { locale: 'macos' });
    f.database.write((sql) => {
      const schema = JSON.parse(
        readFileSync(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
      ) as { statements: string[] };
      for (const name of ['mac_executor_presence', 'mac_runs']) {
        if (sql.get('SELECT 1 FROM sqlite_master WHERE name=?', [name])) continue;
        const statement = schema.statements.find((value) =>
          value.startsWith(`CREATE TABLE ${name} (`),
        );
        if (!statement) throw new Error(`Missing schema fixture table ${name}`);
        sql.run(statement);
      }
      sql.run(
        `INSERT INTO mac_executor_presence
        (singleton,executor_incarnation,identity_json,last_lease_at_ms) VALUES(1,?,?,?)`,
        [
          'retained-mac',
          JSON.stringify({
            protocol_version: PROTOCOL_VERSION,
            spool_format_version: SPOOL_FORMAT_VERSION,
            application_revision: 'synthetic-mac',
            executor_incarnation: 'retained-mac',
            connection_generation: 1,
          }),
          Date.now(),
        ],
      );
      write_ticket_conversation_start(sql, ticket.id, {
        conversation_id: 'core',
        backend: 'codex',
        model: 'model',
        reasoning_effort: null,
        now: 100,
      });
    });
    expect(f.database.read((sql) => can_execute_locale(sql, 'macos'))).toBe(true);
    const retained = f.store.read_conversation('core')!.resolved_start();
    expect(retained.workspace_path_policy).toBe('home_relative');
    // There is deliberately no PLAN_TICKET_ID hint. The canonical link owns locality.
    expect(retained.role_materials!.identity_environment_variables).not.toContainEqual([
      'PLAN_TICKET_ID',
      ticket.id,
    ]);
    const context: ConversationCompositionContext = {
      database: f.database,
      config,
      clock,
      changes: new ChangeSignal(),
      record: {
        database: f.database,
        store: f.store,
        live_tail,
        sse_heartbeat_ms: 15_000,
        runtime_facts: async () => ({ running: false, permissions: [], inputs: [] }),
      },
      publication: new SignalPublicationCoordinator({
        database: f.database,
        require_runtime_ownership() {},
      }),
      require_runtime_ownership() {},
      revoke_signal() {},
      one_shot_children: children,
      child_processes,
    };
    bound = compose_production_backends(context, {
      claude: () => local_constructor,
      codex: () => local_constructor,
      hermes: () => local_constructor,
      http: () => http,
      stall_sources: () => [],
    });
    const snapshot = () =>
      database.read((sql) =>
        Object.fromEntries(
          sql
            .all<{ name: string }>(
              "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            )
            .map(({ name }) => [
              name,
              sql
                .all(`SELECT * FROM "${name.replaceAll('"', '""')}"`)
                .map((row) => JSON.stringify(row))
                .sort(),
            ]),
        ),
      );
    const unchanged = async <T>(operation: () => T | Promise<T>): Promise<T> => {
      const before = snapshot();
      try {
        return await operation();
      } finally {
        expect(snapshot()).toEqual(before);
        expect(local_constructor).not.toHaveBeenCalled();
        expect(f.children).toHaveLength(0);
      }
    };
    const refused = async (operation: () => unknown | Promise<unknown>, id: string) => {
      let failure: unknown;
      await unchanged(async () => {
        try {
          await operation();
        } catch (error) {
          failure = error;
        }
      });
      expect(failure).toBeInstanceOf(PlannerError);
      if (!(failure instanceof PlannerError)) throw failure;
      expect(failure.statusCode).toBe(400);
      expect(failure.toPayload()).toEqual({
        error: {
          code: 'validation',
          message: 'Waiting for the Mac',
          detail: { ticket_id: id, locale: 'macos', machine_locale: 'vps' },
        },
      });
    };
    await refused(() => f.send(), ticket.id);
    const unlinked = create(database, { locale: 'macos' });
    await refused(
      () =>
        send_to_ticket_conversation(
          f.core,
          database,
          unlinked.id,
          text_message_content('new Mac start'),
          {
            conversation_id: null,
            created_conversation_id: 'must-not-exist',
            sender_label: 'Owner',
            now: 100,
          },
        ),
      unlinked.id,
    );
    const start_worker_step = vi.fn(async () => new WorkerStepStartResult({ started: false }));
    await refused(
      () =>
        restart_worker(f.core, database, OWNER, ticket.id, { now: () => 100, start_worker_step }),
      ticket.id,
    );
    expect(start_worker_step).not.toHaveBeenCalled();
    expect(
      (
        await unchanged(() =>
          start_ready_worker_step(unlinked.id, {
            connect_database: () => database,
            conversation_system: f.core,
            worker_type_registry: configured_worker_type_registry(),
            now: () => 100,
          }),
        )
      ).started,
    ).toBe(false);
    const state = f.core.conversation_state('core')!;
    const held = f.core.hold_prompt(state, {
      held_prompt_id: '',
      content: text_message_content('retained queue'),
      sender_label: 'Owner',
      sender: null,
      recipient: null,
      sender_message_id: 'retained-queue',
      sent_at_unix_milliseconds: null,
      snapshot_sent_at_unix_milliseconds: 0,
      model_change: null,
      reasoning_effort_change: null,
      reasoning_effort_is_set: false,
      owner_read_through_sequence: null,
      acknowledge_owner_on_failure: true,
      recovery_references: [],
      recording_receipt: null,
      queue_reason: PromptQueueReason.requested,
      delivery_uncertain: false,
      system_generated: false,
    });
    const held_before = JSON.stringify(state.held_prompts);
    await unchanged(() => f.core.drain_held_prompts(state));
    await refused(
      () => f.core.promote_held_prompt('core', state.held_prompts[0]!.held_prompt_id, 'send_now'),
      ticket.id,
    );
    expect(held.queue_position).toBe(1);
    expect(JSON.stringify(state.held_prompts)).toBe(held_before);

    const recovery_fixture = (name: string, unknown: boolean) => {
      const id = 'mac-recovery-' + name;
      f.store.create_conversation(
        new ResolvedConversationStart({ ...retained, conversation_id: id }),
      );
      const owner = create(database, { locale: 'macos' });
      database.write((sql) =>
        write_ticket_conversation_start(sql, owner.id, {
          conversation_id: id,
          backend: 'codex',
          model: 'model',
          reasoning_effort: null,
          now: 100,
        }),
      );
      const claimed = database.write((sql) =>
        claim_ticket_for_worker_step(sql, owner.id, {
          readiness_check: is_ready_for_worker_step,
          now: 100,
        }),
      );
      if (claimed === null) throw new Error('Recovery fixture must hold a real eligible claim');
      const turn = f.store.reserve_turn(id);
      database.write((sql) => {
        const epoch = sql.get<{ epoch: string }>(
          'SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1',
        )!.epoch;
        import_captured_turns(
          sql,
          {
            version: 1,
            deployment_id: id,
            capture_id: id,
            captured_at: 100,
            state: 'captured',
            detail: null,
            turns: [
              {
                conversation_id: id,
                turn_number: turn,
                occurrence_id: null,
                runtime_epoch: epoch,
                original_status: 'intent',
                display_name: name,
                owner_kind: 'ticket',
                owner_id: owner.id,
                claim_revision: claimed.worker_step_claim_revision,
                claim: 'out',
              },
            ],
          },
          100,
          { claim_error_reason: 'fixture deploy', ended_by: 'Fixture deployment' },
        );
        if (unknown) {
          const interruption = sql.get<{ interruption_id: string }>(
            'SELECT interruption_id FROM deploy_interruptions WHERE source_conversation_id=?',
            [id],
          )!;
          bind_delivery(
            sql,
            id,
            turn,
            [{ interruption_id: interruption.interruption_id, expected_stage: owner.stage }],
            new Map([[interruption.interruption_id, 'unknown-recovery']]),
          );
          reconcile(sql, [], 100);
        }
        f.store.record_background_task_started_in_transaction(
          sql,
          id,
          'persisted-task',
          'Retained Mac task',
        );
      });
      return id;
    };
    const unknown = recovery_fixture('unknown', true);
    const selected = recovery_fixture('selected', false);
    expect(database.read(select_recoveries).has(selected)).toBe(true);
    expect(
      database.read((sql) =>
        sql.get(
          'SELECT 1 FROM deploy_interruption_deliveries WHERE conversation_id=? AND status=?',
          [unknown, 'unknown'],
        ),
      ),
    ).toBeDefined();
    recovery_core = new SqliteProcessConversationSystem(f.core.options);
    await unchanged(() => recovery_core!.tell_agents_of_background_tasks_lost_at_restart());
    const recovery_send = vi.spyOn(recovery_core, 'send');
    const recovery_record = vi.spyOn(recovery_core, 'record_events_transaction');
    await unchanged(() => run_automatic_recovery(recovery_core!, database));
    expect(recovery_send).not.toHaveBeenCalled();
    expect(recovery_record).not.toHaveBeenCalled();

    database.write((sql) =>
      create_wake(sql, {
        manager_id: null,
        ticket_id: ticket.id,
        source_kind: 'worker_error',
        source_revision: 1,
        summary: 'Blocked Mac wake',
        now: 100,
        target: { kind: 'ticket', id: ticket.id },
      }),
    );
    const wake_options = {
      database,
      clock,
      conversation_system: f.core,
      workspace_context: { db_path: config.db_path, workspace_root: root },
      assert_database_owned() {},
    };
    const wake = new ManagerWakeLoop(wake_options);
    try {
      expect(await unchanged(() => wake.poll_once())).toEqual([]);
    } finally {
      await wake.stop();
      await wake.settled();
    }
    const blocked_wakes = () =>
      database.read((sql) =>
        sql.all('SELECT * FROM manager_wakes WHERE target_id=? ORDER BY id', [ticket.id]),
      );
    const before_control = blocked_wakes();
    const control = create(database, { locale: 'vps' });
    database.write((sql) =>
      create_wake(sql, {
        manager_id: null,
        ticket_id: control.id,
        source_kind: 'worker_error',
        source_revision: 1,
        summary: 'Admitted later wake',
        now: 101,
        target: { kind: 'ticket', id: control.id },
      }),
    );
    const claimed = database.write((sql) =>
      claim_next_batch(sql, {
        process_token: 'selection-proof',
        now: 101,
        target_admitted: (current, target) =>
          target.kind !== 'ticket' ||
          configured_ticket_execution_refusal(
            database,
            current,
            read_ticket(current, target.id!),
          ) === null,
      }),
    );
    expect(claimed?.target).toEqual({ kind: 'ticket', id: control.id });
    expect(blocked_wakes()).toEqual(before_control);
    expect(
      database.read((sql) =>
        sql.all('SELECT * FROM manager_wake_batches WHERE target_id=?', [ticket.id]),
      ),
    ).toEqual([]);
    // A batch from before this composition must also stop before offering/uncertainty.
    const pending_mac = database.write((sql) =>
      claim_next_batch(sql, { process_token: 'old-composition', now: 101 }),
    );
    expect(pending_mac?.target).toEqual({ kind: 'ticket', id: ticket.id });
    if (pending_mac === null)
      throw new Error('The canonical control must prepare a pending Mac batch');
    expect(
      await unchanged(() =>
        deliver_batch(pending_mac, {
          ...wake_options,
          process_token: 'local-composition',
          now: () => 101,
        }),
      ),
    ).toBe(false);
    expect(local_constructor).not.toHaveBeenCalled();
    const values = {
      resolved_start: retained,
      event_sink: f.core.make_sink(f.core.conversation_state('core')!),
      message_files: f.core.options.message_files,
    };
    const standalone = f.store
      .create_conversation(
        new ResolvedConversationStart({ ...retained, conversation_id: 'non-ticket' }),
      )
      .resolved_start();
    const wrapped = bound.factories.codex;
    const wrapped_recover = wrapped.recover,
      retained_runs = wrapped.retained_runs;
    if (typeof wrapped_recover !== 'function' || typeof retained_runs !== 'function')
      throw new Error('The production wrapper lost its retained factory methods');
    const mac_reference = {
      conversation_id: 'core',
      executor_incarnation: 'synthetic',
      child_id: 'synthetic',
    };
    for (const invoke of [
      () => bound.factories.codex(values),
      () => wrapped_recover.call(wrapped, mac_reference),
    ]) {
      let invariant: unknown;
      try {
        invoke();
      } catch (error) {
        invariant = error;
      }
      expect(invariant).toBeInstanceOf(Error);
      expect(invariant).not.toBeInstanceOf(PlannerError);
      if (!(invariant instanceof Error)) throw invariant;
      expect(invariant.message).toBe(
        'Execution admission invariant violated: blocked conversation core reached a local backend binding',
      );
      expect(invariant.cause).toBeInstanceOf(PlannerError);
    }
    expect(local_constructor).not.toHaveBeenCalled();
    expect(recover).not.toHaveBeenCalled();
    expect(retained_runs.call(wrapped)).toBe(runs);
    const reference = {
      conversation_id: 'non-ticket',
      executor_incarnation: 'synthetic',
      child_id: 'synthetic',
    };
    expect(wrapped_recover.call(wrapped, reference)).toBeNull();
    expect(recover).toHaveBeenCalledWith(reference, undefined);
    expect(() => bound.factories.codex({ ...values, resolved_start: standalone })).toThrow(
      'Synthetic local constructor boundary',
    );
    expect(local_constructor).toHaveBeenCalledTimes(1);
  } finally {
    await recovery_core?.shutdown();
    await recovery_core?.settled();
    await http.shutdown?.();
    await http.settled();
    children.seal();
    await children.settled();
    await child_processes.settled();
    await f.core.shutdown();
    live_tail.close();
  }
  console.info(`Temporary Mac guard component proof: ${(performance.now() - began).toFixed(3)} ms`);
});
