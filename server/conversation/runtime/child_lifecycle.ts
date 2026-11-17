/** Local child custody, child retirement, and Worker cleanup checks. */
import { ConversationState } from './state.ts';
import type { BackendChild, TurnToken, ChildReference } from '../backends/contracts.ts';
import type { CoreBackendEventSink } from './contracts.ts';
import { background_tasks_ended_with_child } from './maintenance.ts';
import * as remote from './remote.ts';
import type { CleanupResult } from '../../worker_supervision/contracts.ts';
import type { WireValue } from '../../executor/contracts.ts';
import { decode_json } from '../../executor/protocol.ts';
import { PROTOCOL_VERSION } from '../../execution_configuration.ts';
import { LEASE_CONTINUITY_SECONDS } from '../../executor/configuration.ts';
import { cleanup_result } from '../../worker_supervision/inspection.ts';
import { HOME_MACHINE_ID } from '../../execution_identity.ts';
import { isDeepStrictEqual } from 'node:util';
import type { ConversationSystem } from '../contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { TransactionSql, ReadSql } from '../../core/database/contracts.ts';
import type {
  WorkspaceIdentity,
  MaybePromise,
  RetirementTarget,
} from '../../ticket_workspaces/contracts.ts';
import { conversation_link_lock } from '../../runtime/conversation_start.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';

export function ensure_local_custody(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
): Promise<void> {
  state.custody_ready ??= (async () => {
    const id = state.record.conversation_id;
    state.local_child_retirement_proven =
      await host.options.store.recorded_source_retirement_proven(id);
    state.local_preparation_cancelled = !(await host.options.store.has_recorded_source(id));
    if (host.options.store.blocked_local_conversations.has(id)) state.child_is_quarantined = true;
  })();
  return state.custody_ready;
}

export async function record_local_child_preparation(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  child: BackendChild,
  sink: CoreBackendEventSink,
): Promise<void> {
  await host.ensure_local_custody(state);
  state.local_child_retirement_proven = false;
  state.local_preparation_cancelled = false;
  host.local_child_generations.set(child, sink.generation);
  await host.options.store.record_child_custody(state.record.conversation_id, sink.generation);
}

export async function check_local_child_started(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  turn: TurnToken,
): Promise<boolean> {
  const child = state.child;
  const generation = child === null ? undefined : host.local_child_generations.get(child);
  return (
    generation !== undefined &&
    (await host.options.store.check_local_child_started(
      state.record.conversation_id,
      generation,
      turn.turn_number,
    ))
  );
}

export async function _confirm_local_child_stopped(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  child: BackendChild,
): Promise<void> {
  const generation = host.local_child_generations.get(child);
  if (!child.stop_outside_lock && generation !== undefined)
    await host.options.store.record_child_custody(state.record.conversation_id, generation, null, {
      retired: true,
    });
  await host.persist(state, () =>
    host.start_admission.retire_source(host.options.store.start_admission_database, child),
  );
  state.local_child_retirement_proven = true;
  state.child_event_sink = null;
  host.child_stop_ownership.delete(child);
  await background_tasks_ended_with_child(host, state);
}

export async function discard_child(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  child: import('../backends/contracts.ts').BackendChild,
): Promise<boolean> {
  if (child.stop_outside_lock) {
    remote.schedule_child_retirement(host, state, child);
    return false;
  }
  // A pending stop remains owned even when the active slot becomes available.
  host.child_stop_ownership.set(child, state);
  if (state.child === child) state.child = null;
  let stopped = false;
  try {
    await host.native_operation(() => child.stop());
    await child.settled?.();
    await host._confirm_local_child_stopped(state, child);
    host.child_stop_ownership.delete(child);
    state.child_is_quarantined = false;
    stopped = true;
  } catch (error) {
    if (state.child === null) state.child = child;
    state.child_is_quarantined = true;
    console.error(`conversation ${state.record.conversation_id} could not stop its child`, error);
  }
  return stopped;
}

export function retire_child_for_rebind(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  child: BackendChild,
): Promise<boolean> {
  return child.stop_outside_lock
    ? remote.schedule_child_retirement(host, state, child, false)
    : host.discard_child(state, child);
}

export async function recheck_worker_cleanup(
  host: SqliteProcessConversationSystem,
  ticket_id: string,
): Promise<CleanupResult> {
  host.require_runtime_ownership();
  const read = () =>
    host.options.store.start_admission_database.read((sql) => {
      const row = sql.get<{ machine_id: string; conversation_id: string | null }>(
        'SELECT machine_id,conversation_id FROM tickets WHERE id=?',
        [ticket_id],
      );
      if (row === undefined) return null;
      const local_ids = sql
        .all<{ conversation_id: string }>(
          'SELECT w.conversation_id FROM ticket_workspace_conversations w JOIN conversation_child_custody c USING(conversation_id) WHERE w.ticket_id=?',
          [ticket_id],
        )
        .map((item) => item.conversation_id);
      const presence = sql.get<{
        lease_json: string | null;
        last_lease_at_ms: number | null;
        executor_incarnation: string;
      }>(
        'SELECT lease_json,last_lease_at_ms,executor_incarnation FROM mac_executor_presence WHERE machine_id=?',
        [row.machine_id],
      );
      let lease: WireValue<'LeaseRequest'> | null = null;
      let fresh = false;
      if (presence?.lease_json !== null && presence?.lease_json !== undefined) {
        try {
          lease = decode_json(presence.lease_json, 'LeaseRequest');
          fresh =
            lease.cleanup_uncertainties !== undefined &&
            lease.identity.protocol_version === PROTOCOL_VERSION &&
            lease.identity.executor_incarnation === presence.executor_incarnation &&
            presence.last_lease_at_ms !== null &&
            Math.floor(host.unix_time_now() * 1000) - presence.last_lease_at_ms <=
              LEASE_CONTINUITY_SECONDS * 1000;
        } catch (error) {
          // Invalid lease evidence cannot prove cleanup.
          console.warn('executor lease evidence is invalid; it cannot prove cleanup', error);
        }
      }
      const uncertain = new Set(
        (lease?.cleanup_uncertainties ?? [])
          .filter((item) => item.ticket_id === ticket_id)
          .map((item) => item.conversation_id),
      );
      const historical = sql
        .all<{ executor_incarnation: string; child_id: string; conversation_id: string }>(
          "SELECT executor_incarnation,child_id,conversation_id FROM executor_child_assignments WHERE principal_kind='ticket' AND principal_id=?",
          [ticket_id],
        )
        .filter((item) => uncertain.has(item.conversation_id) || uncertain.has(''));
      return { ...row, local_ids, historical, fresh: fresh && uncertain.size === 0 };
    });
  const ticket = read();
  if (ticket === null) return cleanup_result(false, 'The Ticket is unavailable');
  if (ticket.machine_id !== HOME_MACHINE_ID) {
    const state =
      ticket.conversation_id === null ? null : host.conversation_state(ticket.conversation_id);
    const source =
      state === null
        ? null
        : (host.retained_run(state)?.reference ?? state.retained_child_reference);
    const source_key = (item: ChildReference) =>
      JSON.stringify([item.executor_incarnation, item.child_id, item.conversation_id]);
    const sources = new Map(ticket.historical.map((item) => [source_key(item), item]));
    if (source !== null) sources.set(source_key(source), source);
    if (sources.size === 0) {
      const current = read();
      const current_source =
        state === null
          ? null
          : (host.retained_run(state)?.reference ?? state.retained_child_reference);
      if (
        !current ||
        current.machine_id !== ticket.machine_id ||
        current.conversation_id !== ticket.conversation_id ||
        current.historical.length ||
        current_source !== null
      )
        return cleanup_result(false, 'The Worker source changed during cleanup inspection');
      return cleanup_result(
        ticket.fresh && current.fresh,
        ticket.fresh && current.fresh
          ? 'No retained Worker source needs cleanup'
          : 'Fresh executor cleanup evidence is unavailable',
      );
    }
    if (host.worker_cleanup_recheck === null)
      return cleanup_result(false, 'The executor cleanup checker is unavailable');
    for (const recorded of sources.values()) {
      const result = await host.worker_cleanup_recheck(recorded, ticket_id);
      if (!result.complete) return result;
    }
    const current = read();
    const current_source =
      state === null
        ? null
        : (host.retained_run(state)?.reference ?? state.retained_child_reference);
    const historical = new Set(ticket.historical.map(source_key));
    if (
      !current ||
      current.machine_id !== ticket.machine_id ||
      current.conversation_id !== ticket.conversation_id ||
      !isDeepStrictEqual(current_source, source) ||
      current.historical.some((item) => !historical.has(source_key(item)))
    )
      return cleanup_result(false, 'The Worker source changed during cleanup inspection');
    if (!current.fresh)
      return cleanup_result(false, 'Fresh executor cleanup evidence is unavailable');
    return cleanup_result(true, 'Recorded Worker cleanup is complete');
  }
  const ids = new Set(ticket.local_ids);
  if (ticket.conversation_id !== null) ids.add(ticket.conversation_id);
  for (const id of ids) {
    const state = host.conversation_state(id);
    if (state === null) return cleanup_result(false, 'The recorded conversation is unavailable');
    await host.ensure_local_custody(state);
    const result = await state.lock.run(async () => {
      if (host.options.store.blocked_local_conversations.has(id)) {
        const settlement = await host.options.store.settle_recorded_source(id, {
          inspect_only: true,
        });
        if (settlement === null)
          return cleanup_result(false, 'The recorded Worker cleanup is not proven');
        await take_recorded_settlement(host, state, settlement);
        return null;
      }
      if (state.running_turn !== null || state.reserved_turn !== null)
        return cleanup_result(false, 'The Worker still has an active turn');
      if (!(await host.options.store.has_recorded_source(id))) {
        return state.child !== null || state.child_is_quarantined
          ? cleanup_result(false, 'The retained Worker has no exact cleanup evidence')
          : null;
      }
      if (!(await host.options.store.retire_idle_recorded_source(id, { inspect_only: true })))
        return cleanup_result(
          false,
          'Worker cleanup is not proven. Stop the old process and recheck cleanup.',
        );
      if (state.child !== null) {
        await host.discard_child(state, state.child);
        if (state.child !== null || state.child_is_quarantined)
          return cleanup_result(false, 'The retained Worker cleanup is not proven');
      }
      state.local_child_retirement_proven = true;
      state.child_is_quarantined = false;
      return null;
    });
    if (result !== null) return result;
  }
  return cleanup_result(true, 'Recorded Worker cleanup is complete');
}

async function take_recorded_settlement(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  settlement: 'undelivered' | 'retired',
): Promise<void> {
  state.local_child_retirement_proven = settlement === 'retired';
  state.local_preparation_cancelled = settlement === 'undelivered';
  state.child_is_quarantined = false;
  const token = host.options.store.settled_recorded_turn(state.record.conversation_id);
  if (token !== null)
    await host.persist(state, () =>
      host.start_admission.reject(host.options.store.start_admission_database, token),
    );
}

export async function confirm_killed_source(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  restart?: { ticket_id: string; finish: Parameters<ConversationSystem['kill_for_restart']>[2] },
): Promise<void> {
  const id = state.record.conversation_id;
  if (host.options.store.blocked_local_conversations.has(id)) {
    const settlement = await host.options.store.settle_recorded_source(id);
    if (settlement === null)
      throw new PlannerError(
        'retry_later',
        `${await host.options.store.recorded_source_block_reason(id)} Recheck Worker cleanup.`,
      );
    await take_recorded_settlement(host, state, settlement);
  }
  if (
    state.child === null &&
    !state.local_preparation_cancelled &&
    !state.local_child_retirement_proven &&
    (await host.options.store.has_recorded_source(id))
  ) {
    if (
      !(await host.options.store.recorded_source_retirement_proven(id)) &&
      !(await host.options.store.retire_idle_recorded_source(id))
    )
      throw new PlannerError(
        'retry_later',
        'The recorded Worker cleanup is not proven. Recheck Worker cleanup.',
      );
    state.local_child_retirement_proven = true;
  }
  let check_cleanup: ((sql: TransactionSql) => void) | null = null;
  const inspected_remote = host.retained_run(state);
  let remote_inspected = false;
  if (restart) {
    const capture = (sql: ReadSql, require_fresh: boolean) => {
      const ticket = sql.get<{ machine_id: string; conversation_id: string | null }>(
        'SELECT machine_id,conversation_id FROM tickets WHERE id=?',
        [restart.ticket_id],
      );
      if (!ticket || ticket.conversation_id !== id)
        throw new PlannerError('retry_later', 'The Worker source changed');
      const presence = sql.get<{
        executor_incarnation: string;
        lease_json: string | null;
        last_lease_at_ms: number | null;
      }>(
        'SELECT executor_incarnation,lease_json,last_lease_at_ms FROM mac_executor_presence WHERE machine_id=?',
        [ticket.machine_id],
      );
      if (require_fresh) {
        let fresh = false;
        try {
          const lease =
            presence?.lease_json == null ? null : decode_json(presence.lease_json, 'LeaseRequest');
          fresh =
            lease !== null &&
            lease.cleanup_uncertainties !== undefined &&
            lease.identity.protocol_version === PROTOCOL_VERSION &&
            lease.identity.executor_incarnation === presence?.executor_incarnation &&
            presence.last_lease_at_ms !== null &&
            Math.floor(host.unix_time_now() * 1000) - presence.last_lease_at_ms <=
              LEASE_CONTINUITY_SECONDS * 1000 &&
            !(lease.cleanup_uncertainties ?? []).some(
              (item) => item.ticket_id === restart.ticket_id,
            );
        } catch (error) {
          // Invalid evidence cannot prove remote cleanup.
          console.warn('executor lease evidence is invalid; it cannot prove remote cleanup', error);
        }
        if (!fresh)
          throw new PlannerError('retry_later', 'Fresh executor cleanup evidence is unavailable');
      }
      return {
        machine_id: ticket.machine_id,
        incarnation: presence?.executor_incarnation ?? null,
        assignments: sql.all(
          "SELECT executor_incarnation,child_id,conversation_id,closed_at_ms FROM executor_child_assignments WHERE principal_kind='ticket' AND principal_id=? ORDER BY executor_incarnation,child_id",
          [restart.ticket_id],
        ),
        run:
          sql.get(
            'SELECT executor_incarnation,child_id,stop_state FROM mac_runs WHERE conversation_id=?',
            [id],
          ) ?? null,
      };
    };
    const before = host.options.store.start_admission_database.read((sql) => capture(sql, false));
    if (before.machine_id !== HOME_MACHINE_ID) {
      const result = await host.recheck_worker_cleanup(restart.ticket_id);
      if (!result.complete) throw new PlannerError('retry_later', result.reason);
      remote_inspected = true;
      const inspected = host.options.store.start_admission_database.read((sql) =>
        capture(sql, true),
      );
      if (!isDeepStrictEqual(before, inspected))
        throw new PlannerError(
          'retry_later',
          'The Worker source changed during cleanup inspection',
        );
      check_cleanup = (sql) => {
        if (!isDeepStrictEqual(capture(sql, true), inspected))
          throw new PlannerError(
            'retry_later',
            'The Worker source changed during cleanup inspection',
          );
      };
    }
  }
  await state.lock.run(async () => {
    const retained = host.retained_run(state);
    const remote_receipt = retained?.retirement_confirmed === true;
    if (
      state.child !== null ||
      state.retained_child_reference !== null ||
      state.child_is_quarantined ||
      (state.retirement_task !== null && state.retiring_child !== null) ||
      (retained !== null && retained !== undefined && !remote_receipt) ||
      (remote_inspected && retained !== inspected_remote) ||
      (restart !== undefined && state.held_prompts.length > 0) ||
      !(remote_receipt || state.local_child_retirement_proven || state.local_preparation_cancelled)
    )
      throw new PlannerError(
        'retry_later',
        'The old Worker is not proven ended. Recheck Worker cleanup.',
      );
    if (restart) {
      try {
        await restart.finish(check_cleanup);
      } catch (error) {
        const linked = host.options.store.start_admission_database.read(
          (sql) =>
            sql.get('SELECT 1 FROM tickets WHERE id=? AND conversation_id=?', [
              restart.ticket_id,
              id,
            ]) !== undefined,
        );
        if (!linked) state.restart_retired = true;
        throw error;
      }
      state.restart_retired = true;
    }
  });
}

export async function retire_local_workspace(
  host: SqliteProcessConversationSystem,
  ticket_id: string,
  identity: WorkspaceIdentity,
  operation: () => MaybePromise<RetirementTarget>,
): Promise<RetirementTarget> {
  if (host.retiring_workspace_tickets.has(ticket_id))
    return { reason: 'Ticket workspace retirement is already in progress' };
  host.retiring_workspace_tickets.add(ticket_id);
  let ids: readonly string[] = [];
  try {
    return await conversation_link_lock(`ticket:${ticket_id}`).run(async () =>
      host.workspace_admission.operation(async (admitted) => {
        if (!admitted) return { reason: 'Owner update in progress' };
        ids = await host.options.store.workspace_conversation_ids(ticket_id, identity);
        for (const id of ids) host.retiring_workspace_conversations.add(id);
        for (const id of ids) await host.kill(id);
        return await operation();
      }),
    );
  } catch (error) {
    console.error('Local workspace retirement failed', error);
    return { reason: 'Local child retirement failed' };
  } finally {
    for (const id of ids) host.retiring_workspace_conversations.delete(id);
    host.retiring_workspace_tickets.delete(ticket_id);
  }
}
