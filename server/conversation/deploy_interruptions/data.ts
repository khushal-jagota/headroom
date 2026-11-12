/** Deployment attribution and factual receipts use the caller's write door. */
import { randomUUID } from 'node:crypto';
import { manager_agent_key } from '../../managers/data.ts';
import { PlannerError } from '../../core/contracts.ts';
import { blocked_ticket_ids } from '../../core/ticket_blocks.ts';
import { can_execute_locale } from '../../tickets/locale_configuration.ts';
import { stage_definition, stage_index } from '../../tickets/logic/machine.ts';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type { DeploymentRecoveryReference } from '../contracts.ts';
import type { LifecycleFact } from '../record/lifecycle.ts';
import { retained_run_is_unresolved } from '../record/data/lifecycle.ts';
import * as tickets from '../../tickets/data/index.ts';
import { claim_matches, combine_capture_states, matches_occurrence } from './logic/rules.ts';
import { interruption_identity } from './identity.ts';
import type {
  CaptureManifest,
  InterruptionAccount,
  InterruptionSummary,
  OwnerKind,
  RecoveryState,
} from './contracts.ts';
interface InterruptionRow {
  interruption_id: string;
  capture_id: string;
  deployment_id: string;
  source_conversation_id: string;
  turn_number: number;
  occurrence_id: string | null;
  runtime_epoch: string | null;
  display_name: string;
  owner_kind: OwnerKind;
  owner_id: string | null;
  claim_revision: number | null;
  original_status: string;
  recovery_state: RecoveryState;
  detail: string;
  exact_match: number;
}
function runtime_epoch(sql: ReadSql): string | null {
  const row = sql.get<{ epoch: string }>(
    'SELECT epoch FROM conversation_runtime_epoch WHERE singleton=1',
  );
  if (row) return row.epoch;
  if (!sql.get("SELECT 1 FROM sqlite_master WHERE name='signal_publication_meta'")) return null;
  return (
    sql.get<{ runtime_epoch: string }>(
      'SELECT runtime_epoch FROM signal_publication_meta WHERE singleton=1',
    )?.runtime_epoch ?? null
  );
}
export function reconcile(
  tx: TransactionSql,
  manifests: readonly CaptureManifest[],
  now: number,
): void {
  for (const manifest of manifests) {
    import_captured_turns(tx, manifest, now, {
      claim_error_reason: 'interrupted by a deploy',
      ended_by: 'A deployment',
    });
  }
  tx.run(
    'INSERT INTO conversation_runtime_epoch VALUES (1,?) ON CONFLICT(singleton) DO ' +
      'UPDATE SET epoch=excluded.epoch',
    [randomUUID().replaceAll('-', '')],
  );
  for (const delivery of tx.all<{ conversation_id: string; turn_number: number }>(
    'SELECT DISTINCT conversation_id,turn_number FROM deploy_interruption_deliveries ' +
      "WHERE status='intent'",
  )) {
    fail_recovery_claims(tx, delivery.conversation_id, delivery.turn_number, {
      now,
      error_reason: 'recovery after a deploy has an unknown outcome',
      notify_supervisor: false,
    });
  }
  tx.run(
    "UPDATE deploy_interruptions SET recovery_state='unable_to_resume',detail=detail " +
      "|| ' Recovery delivery outcome is unknown. Inspect current work before another " +
      "action.' WHERE interruption_id IN (SELECT interruption_id FROM " +
      "deploy_interruption_deliveries WHERE status='intent') AND " +
      "recovery_state='awaiting_action'",
  );
  tx.run("UPDATE deploy_interruption_deliveries SET status='unknown' WHERE status='intent'");
}
export function import_captured_turns(
  tx: TransactionSql,
  manifest: CaptureManifest,
  now: number,
  options: {
    claim_error_reason: string;
    ended_by: string;
    turn_notes?: ReadonlyMap<string, string>;
  },
): void {
  const old_epoch = runtime_epoch(tx);
  tx.run('INSERT OR IGNORE INTO deploy_interruption_accounts VALUES (?,?,?,?,?)', [
    manifest.capture_id,
    manifest.deployment_id,
    manifest.captured_at,
    manifest.state,
    manifest.detail,
  ]);
  for (const turn of manifest.turns) {
    const id = interruption_identity(manifest, turn);
    if (tx.get('SELECT 1 FROM deploy_interruptions WHERE interruption_id=?', [id])) continue;
    const occurrence = tx.get<{ occurrence_id: string }>(
      'SELECT occurrence_id FROM conversation_turn_occurrences WHERE conversation_id=? ' +
        'AND turn_number=?',
      [turn.conversation_id, turn.turn_number],
    );
    const fact = tx.get<{ status: string }>(
      'SELECT status FROM conversation_lifecycle_facts WHERE conversation_id=? AND ' +
        "kind='turn' AND turn_number=?",
      [turn.conversation_id, turn.turn_number],
    );
    const exists =
      tx.get('SELECT 1 FROM conversations WHERE conversation_id=?', [turn.conversation_id]) !==
      undefined;
    const native_unresolved = retained_run_is_unresolved(tx, turn.conversation_id);
    const matched =
      !native_unresolved &&
      matches_occurrence(
        turn.occurrence_id,
        occurrence?.occurrence_id ?? null,
        turn.runtime_epoch,
        old_epoch,
        fact?.status ?? null,
      );
    let detail =
      turn.original_status === 'intent'
        ? `${options.ended_by} stopped this turn before its launch outcome was certain. External actions can remain uncertain.`
        : `${options.ended_by} ended this turn. External actions can remain uncertain.`;
    let state: RecoveryState = matched ? 'awaiting_action' : 'historical';
    if (native_unresolved)
      detail = 'The native executor owns this turn. A VPS stop does not prove termination.';
    if (
      tx.get(
        'SELECT 1 FROM deploy_interruption_deliveries WHERE conversation_id=? AND ' +
          "turn_number=? AND status IN ('intent','unknown') LIMIT 1",
        [turn.conversation_id, turn.turn_number],
      )
    ) {
      state = 'unable_to_resume';
      detail += ' Recovery delivery outcome is unknown. No automatic retry occurred.';
    }
    const note = options.turn_notes?.get(JSON.stringify([turn.conversation_id, turn.turn_number]));
    if (note !== undefined) detail += ' ' + note;
    if (!exists) detail += ' Its conversation is unavailable after restore.';
    else if (!matched)
      detail +=
        ' This historical account does not match an unresolved turn in the current database.';
    if (turn.owner_kind === 'ticket' && turn.owner_id !== null) {
      const ticket = tx.get<{
        stage: string;
        conversation_id: string | null;
        worker_step_claim: string;
        worker_step_claim_revision: number;
      }>(
        'SELECT stage,conversation_id,worker_step_claim,worker_step_claim_revision FROM ' +
          'tickets WHERE id=?',
        [turn.owner_id],
      );
      if (ticket?.stage === 'done') {
        state = 'closed';
        detail += ' The Ticket is complete.';
      } else if (
        ticket &&
        claim_matches({
          exact: matched,
          captured_conversation: turn.conversation_id,
          current_conversation: ticket.conversation_id,
          captured_revision: turn.claim_revision,
          current_revision: ticket.worker_step_claim_revision,
          captured_claim: turn.claim,
          current_claim: ticket.worker_step_claim,
          stage: ticket.stage,
        })
      ) {
        tickets.mark_worker_step_claim_errored(tx, turn.owner_id, {
          expected_claim: 'out',
          expected_claim_revision: turn.claim_revision!,
          now,
          error_reason: options.claim_error_reason,
          notify_supervisor: false,
        });
      }
    }
    tx.run('INSERT INTO deploy_interruptions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
      id,
      manifest.capture_id,
      manifest.deployment_id,
      turn.conversation_id,
      turn.turn_number,
      turn.occurrence_id,
      turn.runtime_epoch,
      turn.display_name,
      turn.owner_kind,
      turn.owner_id,
      turn.claim_revision,
      turn.original_status,
      state,
      detail,
      Number(matched),
    ]);
  }
}
export function read_summary(sql: ReadSql): InterruptionSummary {
  const accounts = new Map<string, InterruptionAccount>();
  for (const account of sql.all<{
    capture_id: string;
    deployment_id: string;
    capture_state: CaptureManifest['state'];
    detail: string | null;
  }>(
    'SELECT capture_id,deployment_id,capture_state,detail FROM ' +
      'deploy_interruption_accounts ORDER BY captured_at DESC,capture_id',
  )) {
    const entries = sql
      .all<InterruptionRow & { live_conversation_id: string | null; ticket_stage: string | null }>(
        'SELECT d.*,c.conversation_id AS live_conversation_id,t.stage AS ticket_stage FROM ' +
          'deploy_interruptions d LEFT JOIN conversations c ON ' +
          'c.conversation_id=d.source_conversation_id LEFT JOIN tickets t ON ' +
          "d.owner_kind='ticket' AND t.id=d.owner_id WHERE d.capture_id=? ORDER BY " +
          'd.display_name,d.interruption_id',
        [account.capture_id],
      )
      .map((row) => {
        const closed =
          row.ticket_stage === 'done' &&
          ['awaiting_action', 'unable_to_resume'].includes(row.recovery_state);
        return {
          interruption_id: row.interruption_id,
          deployment_id: row.deployment_id,
          conversation_id: row.live_conversation_id,
          display_name: row.display_name,
          owner_kind: row.owner_kind,
          owner_id: row.owner_id,
          original_status: row.original_status,
          recovery_state: closed ? ('closed' as const) : row.recovery_state,
          detail: row.detail + (closed ? ' The Ticket is complete.' : ''),
        };
      });
    const existing = accounts.get(account.deployment_id);
    if (!existing)
      accounts.set(account.deployment_id, {
        deployment_id: account.deployment_id,
        capture_state: account.capture_state,
        detail: account.detail,
        entries,
      });
    else {
      existing.capture_state = combine_capture_states(
        existing.capture_state,
        account.capture_state,
      );
      existing.entries.push(...entries);
      existing.detail =
        [...new Set([existing.detail, account.detail].filter(Boolean))].join(' ') || null;
    }
  }
  return { accounts: [...accounts.values()] };
}
export function bind_delivery(
  tx: TransactionSql,
  conversation_id: string,
  turn_number: number,
  references?: readonly DeploymentRecoveryReference[] | null,
  recovery_sender_message_ids?: ReadonlyMap<string, string>,
): string | null {
  let rows = tx.all<{ interruption_id: string; detail: string }>(
    'SELECT d.interruption_id,d.detail FROM deploy_interruptions d WHERE ' +
      "d.recovery_state IN ('awaiting_action','unable_to_resume') AND d.exact_match=1 " +
      "AND (d.source_conversation_id=? OR (d.owner_kind='ticket' AND EXISTS (SELECT 1 " +
      'FROM tickets t WHERE t.id=d.owner_id AND t.conversation_id=? AND ' +
      "t.stage!='done'))) ORDER BY d.interruption_id",
    [conversation_id, conversation_id],
  );
  if (references != null) {
    const selected = new Set(references.map((ref) => ref.interruption_id));
    rows = rows.filter((row) => selected.has(row.interruption_id));
  }
  if (!rows.length) return null;
  for (const row of rows) {
    tx.run(
      'INSERT OR IGNORE INTO deploy_interruption_deliveries ' +
        "(interruption_id,conversation_id,turn_number,status) VALUES (?,?,?,'intent')",
      [row.interruption_id, conversation_id, turn_number],
    );
    if (recovery_sender_message_ids)
      tx.run(
        'UPDATE deploy_interruption_deliveries SET recovery_sender_message_id=? WHERE ' +
          'interruption_id=? AND conversation_id=? AND turn_number=?',
        [
          recovery_sender_message_ids.get(row.interruption_id)!,
          row.interruption_id,
          conversation_id,
          turn_number,
        ],
      );
  }
  return (
    '[Panels deployment interruption context]\n' +
    rows.map((row) => row.detail).join('\n') +
    '\nThe previous work did not resume automatically. Inspect current work before repeating any external action. Do not replay unknown prompts or permission answers.'
  );
}
export function fail_recovery_claims(
  tx: TransactionSql,
  conversation_id: string,
  turn_number: number,
  options: { now: number; error_reason: string; notify_supervisor?: boolean },
): void {
  for (const row of tx.all<{
    owner_id: string;
    claim_revision: number;
    restored_claim_revision: number | null;
  }>(
    'SELECT d.owner_id,d.claim_revision,x.restored_claim_revision FROM ' +
      'deploy_interruption_deliveries x JOIN deploy_interruptions d ' +
      'USING(interruption_id) WHERE x.conversation_id=? AND x.turn_number=? AND ' +
      "x.status='intent' AND d.owner_kind='ticket' AND d.claim_revision IS NOT NULL",
    [conversation_id, turn_number],
  )) {
    if (row.restored_claim_revision === null && options.notify_supervisor === false) continue;
    tickets.mark_worker_step_claim_errored(tx, row.owner_id, {
      ...options,
      expected_claim: row.restored_claim_revision !== null ? 'out' : 'errored',
      expected_claim_revision: row.restored_claim_revision ?? row.claim_revision + 1,
    });
  }
}
export function settle_delivery(
  tx: TransactionSql,
  conversation_id: string,
  facts: readonly LifecycleFact[],
  options: { now: number; composed_content_delivered?: boolean },
): void {
  for (const fact of facts) {
    if (fact.kind !== 'attempt' || fact.operation !== 'prompt') continue;
    if (['suppressed', 'refused', 'unknown'].includes(fact.status))
      fail_recovery_claims(tx, conversation_id, fact.turn_number, {
        now: options.now,
        error_reason: `recovery after a deploy was ${fact.status}`,
        notify_supervisor: fact.status !== 'suppressed',
      });
    if (fact.status === 'suppressed') {
      tx.run(
        'DELETE FROM deploy_interruption_deliveries WHERE conversation_id=? AND ' +
          "turn_number=? AND status='intent'",
        [conversation_id, fact.turn_number],
      );
      continue;
    }
    if (!['started', 'refused', 'unknown'].includes(fact.status)) continue;
    const status =
      options.composed_content_delivered === false ? 'context_not_delivered' : fact.status;
    const state = status === 'started' ? 'resumed' : 'unable_to_resume';
    if (state === 'resumed') {
      for (const claim of tx.all<{ owner_id: string; restored_claim_revision: number }>(
        'SELECT d.owner_id,x.restored_claim_revision,t.stage FROM ' +
          'deploy_interruption_deliveries x JOIN deploy_interruptions d ' +
          'USING(interruption_id) JOIN tickets t ON t.id=d.owner_id JOIN ' +
          'ticket_paired_stage_openers p ON p.ticket_id=t.id AND p.stage=t.stage WHERE ' +
          "x.conversation_id=? AND x.turn_number=? AND x.status='intent' AND " +
          "d.owner_kind='ticket' AND x.restored_claim_revision IS NOT NULL",
        [conversation_id, fact.turn_number],
      ))
        tickets.release_worker_step_claim(tx, claim.owner_id, {
          expected_claim: 'out',
          expected_claim_revision: claim.restored_claim_revision,
          now: options.now,
        });
    } else
      fail_recovery_claims(tx, conversation_id, fact.turn_number, {
        now: options.now,
        error_reason: `recovery after a deploy could not resume (${status})`,
      });
    const detail =
      state === 'resumed'
        ? ' Recovery started with this notice in its backend prompt.'
        : ` Recovery delivery: ${status}. No automatic retry occurred.`;
    tx.run(
      'UPDATE deploy_interruptions SET recovery_state=?,detail=detail || ? WHERE ' +
        'interruption_id IN (SELECT interruption_id FROM deploy_interruption_deliveries ' +
        "WHERE conversation_id=? AND turn_number=? AND status='intent')",
      [state, detail, conversation_id, fact.turn_number],
    );
    tx.run(
      'UPDATE deploy_interruption_deliveries SET status=? WHERE conversation_id=? AND ' +
        "turn_number=? AND status='intent'",
      [status, conversation_id, fact.turn_number],
    );
  }
}
function owner_blocker(sql: ReadSql, row: InterruptionRow): string | null {
  const conversation_id = row.source_conversation_id;
  if (!sql.get('SELECT 1 FROM conversations WHERE conversation_id=?', [conversation_id]))
    return 'the captured conversation is unavailable';
  if (row.owner_kind === 'ticket') {
    const ticket = sql.get<{ conversation_id: string | null }>(
      'SELECT conversation_id FROM tickets WHERE id=?',
      [row.owner_id],
    );
    if (!ticket || ticket.conversation_id !== conversation_id)
      return 'the Ticket conversation link changed';
  } else if (row.owner_kind === 'chief' || row.owner_kind === 'manager') {
    let key: string;
    try {
      key = row.owner_kind === 'chief' ? 'chief_of_staff' : manager_agent_key(row.owner_id!);
    } catch (error) {
      if (!(error instanceof PlannerError)) throw error;
      return 'the Manager conversation identity conflicts';
    }
    const agent = sql.get<{ conversation_id: string | null }>(
      'SELECT conversation_id FROM agents WHERE agent_key=?',
      [key],
    );
    if (!agent || agent.conversation_id !== conversation_id)
      return 'the owner conversation link changed';
  } else if (
    sql.get(
      'SELECT 1 FROM tickets WHERE conversation_id=? UNION ALL SELECT 1 FROM agents ' +
        'WHERE conversation_id=?',
      [conversation_id, conversation_id],
    )
  )
    return 'the conversation now has a different owner';
  return null;
}
export function recovery_blocker(
  sql: ReadSql,
  conversation_id: string,
  reference: DeploymentRecoveryReference,
  options: { allow_restored?: boolean } = {},
): string | null {
  if (
    retained_run_is_unresolved(sql, conversation_id) &&
    !(
      options.allow_restored &&
      !sql.get(
        'SELECT 1 FROM mac_runs AS r LEFT JOIN mac_executor_presence AS p ON p.singleton=1 ' +
          "WHERE r.conversation_id=? AND r.stop_state!='closed' AND r.executor_incarnation " +
          'IS NOT p.executor_incarnation',
        [conversation_id],
      )
    )
  )
    return 'native executor reconciliation owns this turn';
  const row = sql.get<InterruptionRow>(
    'SELECT * FROM deploy_interruptions WHERE interruption_id=?',
    [reference.interruption_id],
  );
  if (!row || row.source_conversation_id !== conversation_id || !row.exact_match)
    return 'the interruption identity does not match';
  if (row.recovery_state !== 'awaiting_action') return 'the interruption is no longer eligible';
  const blocker = owner_blocker(sql, row);
  if (blocker !== null) return blocker;
  if (row.owner_kind !== 'ticket') return null;
  const ticket = tickets.read_ticket(sql, row.owner_id!);
  const definition = ticket.workflow;
  if (ticket.stage !== reference.expected_stage) return 'the Ticket Stage changed';
  if (ticket.is_backlogged) return 'the Ticket is backlogged';
  if (!can_execute_locale(sql, ticket.locale)) return "the Ticket's machine cannot run it";
  const stage = stage_definition(definition, ticket.stage);
  if (stage.is_terminal || stage.gating_field === null)
    return 'the Ticket has no eligible worker step';
  if (ticket.pending_proposal !== null) return 'the Ticket has a pending proposal';
  if (blocked_ticket_ids(sql).has(ticket.id)) return 'the Ticket has a live blocker';
  if (stage_index(definition, ticket.stage) > stage_index(definition, ticket.ceiling))
    return 'the Ticket Stage is beyond its ceiling';
  if (
    stage.ownership_mode === 'user' &&
    !sql.get('SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=? AND stage=?', [
      ticket.id,
      ticket.stage,
    ])
  )
    return 'the captured user Stage opener is unavailable';
  if (row.claim_revision === null) return 'the captured Ticket claim is unavailable';
  const restored = sql.get<{ restored_claim_revision: number }>(
    'SELECT restored_claim_revision FROM deploy_interruption_deliveries WHERE ' +
      "interruption_id=? AND conversation_id=? AND status='intent' AND " +
      'restored_claim_revision IS NOT NULL',
    [reference.interruption_id, conversation_id],
  );
  if (
    options.allow_restored &&
    restored &&
    ticket.worker_step_claim === 'out' &&
    ticket.worker_step_claim_revision === restored.restored_claim_revision
  )
    return null;
  if (
    ticket.worker_step_claim !== 'errored' ||
    ticket.worker_step_claim_revision !== row.claim_revision + 1
  )
    return 'the captured Ticket claim changed';
  return null;
}
export function select_recoveries(
  sql: ReadSql,
): ReadonlyMap<string, readonly DeploymentRecoveryReference[]> {
  const selected = new Map<string, DeploymentRecoveryReference[]>();
  for (const row of sql.all<InterruptionRow & { current_stage: string | null }>(
    'SELECT d.*,t.stage AS current_stage FROM deploy_interruptions d LEFT JOIN tickets ' +
      "t ON d.owner_kind='ticket' AND t.id=d.owner_id WHERE d.exact_match=1 AND " +
      "d.recovery_state='awaiting_action' ORDER BY d.interruption_id",
  )) {
    const reference = { interruption_id: row.interruption_id, expected_stage: row.current_stage };
    if (recovery_blocker(sql, row.source_conversation_id, reference) === null) {
      const group = selected.get(row.source_conversation_id) ?? [];
      group.push(reference);
      selected.set(row.source_conversation_id, group);
    }
  }
  return selected;
}
export function admit_recovery(
  tx: TransactionSql,
  conversation_id: string,
  references: readonly DeploymentRecoveryReference[],
  now: number,
): string | null {
  for (const reference of references) {
    const reason = recovery_blocker(tx, conversation_id, reference, { allow_restored: true });
    if (reason !== null) {
      tx.run('UPDATE deploy_interruptions SET detail=detail || ? WHERE interruption_id=?', [
        ' Recovery suppressed: ' + reason + '.',
        reference.interruption_id,
      ]);
      return reason;
    }
  }
  for (const reference of references) {
    const row = tx.get<InterruptionRow>(
      'SELECT * FROM deploy_interruptions WHERE interruption_id=?',
      [reference.interruption_id],
    )!;
    if (row.owner_kind !== 'ticket') continue;
    const restored = tx.get<{ restored_claim_revision: number | null }>(
      'SELECT restored_claim_revision FROM deploy_interruption_deliveries WHERE ' +
        "interruption_id=? AND conversation_id=? AND status='intent'",
      [reference.interruption_id, conversation_id],
    );
    if (!restored || restored.restored_claim_revision !== null) continue;
    let revision = tickets.restore_interrupted_worker_step_claim(tx, row.owner_id!, {
      conversation_id,
      expected_claim_revision: row.claim_revision! + 1,
      now,
    });
    if (revision === null) {
      const prior = tx.get<{ restored_claim_revision: number }>(
        'SELECT x.restored_claim_revision FROM deploy_interruption_deliveries x JOIN ' +
          'deploy_interruptions d USING(interruption_id) WHERE x.conversation_id=? AND ' +
          "x.status='intent' AND d.owner_id=? AND d.claim_revision=? AND " +
          'x.restored_claim_revision IS NOT NULL',
        [conversation_id, row.owner_id, row.claim_revision],
      );
      const current = tickets.read_ticket(tx, row.owner_id!);
      if (
        !prior ||
        current.worker_step_claim !== 'out' ||
        current.worker_step_claim_revision !== prior.restored_claim_revision
      )
        return 'the captured Ticket claim changed';
      revision = prior.restored_claim_revision;
    }
    tx.run(
      'UPDATE deploy_interruption_deliveries SET restored_claim_revision=? WHERE ' +
        "interruption_id=? AND conversation_id=? AND status='intent'",
      [revision, reference.interruption_id, conversation_id],
    );
  }
  return null;
}
export function recovery_claim_revisions(
  sql: ReadSql,
  conversation_id: string,
): readonly (readonly [string, number])[] {
  return sql
    .all<{ id: string; worker_step_claim_revision: number }>(
      'SELECT DISTINCT t.id,t.worker_step_claim_revision FROM tickets t JOIN ' +
        "deploy_interruptions d ON d.owner_kind='ticket' AND d.owner_id=t.id WHERE " +
        'd.source_conversation_id=? ORDER BY t.id',
      [conversation_id],
    )
    .map((row) => [row.id, row.worker_step_claim_revision] as const);
}
