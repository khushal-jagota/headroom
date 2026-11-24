/** Startup recovery uses the same send door and immutable sender identities as ordinary work. */
import { setTimeout as pause } from 'node:timers/promises';
import {
  DEPLOY_RECOVERY_FIRST_RETRY_SECONDS,
  DEPLOY_RECOVERY_MAX_RETRY_SECONDS,
} from '../runtime/configuration.ts';
import type { Database } from '../../core/database/contracts.ts';
import type { ConversationSystem, DeploymentRecoveryReference } from '../contracts.ts';
import {
  PromptDeliveryMode,
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
} from '../contracts.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import { text_message_content } from '../message_content.ts';
import { PromptDeliveryUncertainEventPayload } from '../events.ts';
import { select_recoveries } from './data.ts';
import { recovery_message_identity } from './identity.ts';
import { configured_conversation_execution_refusal } from '../../runtime/conversation_start.ts';
// A conversation whose machine is away waits. Its machine's return runs the recovery again.
function execution_refusal(database: Database, id: string) {
  return database.read((sql) => configured_conversation_execution_refusal(database, sql, id));
}
export const RECOVERY_PROMPT =
  'Inspect the interrupted work and continue the current authorized step. ' +
  'For direct Panels API calls, use `panels api METHOD PATH --input-json - --json`. ' +
  "The command sends this conversation's credential automatically. " +
  'Do not use X-Plan-* identity headers. ' +
  'To write a recap, use `panels recap`.';
export async function recover_conversations(
  system: ConversationSystem,
  database: Database,
  options: {
    conversation_ids?: readonly string[];
    mode?: PromptDeliveryMode;
    startup?: boolean;
    signal?: AbortSignal;
  } = {},
): Promise<void> {
  const admission = options.startup ? system.workspace_admission : null;
  const selection = admission?.operation({ starts: true });
  let selected: ReturnType<typeof select_recoveries>;
  try {
    if (selection && !selection.admitted) return;
    selected = database.read(select_recoveries);
  } finally {
    selection?.release();
  }
  let pending = [...selected.keys()].filter(
    (id) => options.conversation_ids === undefined || options.conversation_ids.includes(id),
  );
  let delay = DEPLOY_RECOVERY_FIRST_RETRY_SECONDS;
  async function resume(conversation_id: string): Promise<boolean> {
    const starts = system.start_admission;
    const references = selected.get(conversation_id)!;
    const identity = recovery_message_identity(references.map((ref) => ref.interruption_id));
    if (execution_refusal(database, conversation_id) !== null) {
      starts?.finish_unbound(identity);
      return true;
    }
    if (await system.is_running(conversation_id)) {
      starts?.finish_unbound(identity);
      return true;
    }
    if (starts) {
      if (!system.automatic_start_request)
        throw new Error('Automatic start request factory is required');
      if (!starts.try_start(await system.automatic_start_request(identity, conversation_id)))
        return false;
    }
    let offered = false;
    try {
      const fate = await system.send(conversation_id, text_message_content(RECOVERY_PROMPT), {
        sender_label: 'Panels deployment recovery',
        mode: options.mode ?? PromptDeliveryMode.queue,
        sender_message_id: identity,
        record_refusal: true,
        recovery_references: references,
      });
      offered = true;
      if (fate instanceof PromptDeliveryRefused || fate instanceof PromptDeliverySuppressed)
        starts?.finish(identity);
      else if (fate instanceof PromptDeliveryUncertain) starts?.uncertain(identity);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') throw error;
      if (options.startup && system.workspace_admission.starts_closed) return false;
      console.error(`Automatic recovery failed for conversation ${conversation_id}`, error);
    } finally {
      if (!offered) starts?.finish_unbound(identity);
    }
    return true;
  }
  while (pending.length) {
    // Only a pass owns admission. A denied pass leaves canonical recovery rows untouched.
    const pass = admission?.operation({ starts: true });
    try {
      if (pass && !pass.admitted) {
        for (const id of pending)
          system.start_admission?.finish_unbound(
            recovery_message_identity(selected.get(id)!.map((ref) => ref.interruption_id)),
          );
        return;
      }
      if (system.start_admission) {
        if (!system.automatic_start_request)
          throw new Error('Automatic start request factory is required');
        const requests = new Map(
          await Promise.all(
            pending.map(
              async (id) =>
                [
                  id,
                  await system.automatic_start_request!(
                    recovery_message_identity(selected.get(id)!.map((ref) => ref.interruption_id)),
                    id,
                  ),
                ] as const,
            ),
          ),
        );
        pending.sort(
          (left, right) =>
            Number(requests.get(left)!.work_class !== 'supervisor') -
            Number(requests.get(right)!.work_class !== 'supervisor'),
        );
      }
      const remaining: string[] = [];
      for (const conversation_id of pending) {
        // A canceled conversation cannot cancel another conversation's recovery.
        const [result] = await Promise.allSettled([resume(conversation_id)]);
        if (result.status === 'fulfilled' && result.value === false)
          remaining.push(conversation_id);
      }
      pending = remaining;
    } finally {
      pass?.release();
    }
    if (pending.length) {
      try {
        await pause(delay * 1000, undefined, { signal: options.signal });
      } catch (error) {
        if (options.signal?.aborted)
          throw new DOMException('The operation was aborted', 'AbortError');
        throw error;
      }
      delay = Math.min(delay * 2, DEPLOY_RECOVERY_MAX_RETRY_SECONDS);
    }
  }
}
export async function record_unknown_recovery_deliveries(
  system: SqliteProcessConversationSystem,
  database: Database,
): Promise<void> {
  const groups = database.read((sql) => {
    const result = new Map<
      string,
      { conversation_id: string; identity: string; references: DeploymentRecoveryReference[] }
    >();
    for (const row of sql.all<{
      conversation_id: string;
      turn_number: number;
      interruption_id: string;
      stage: string | null;
      recovery_sender_message_id: string;
    }>(
      'SELECT x.conversation_id,x.turn_number,x.interruption_id,t.stage,x.recovery_sender_message_id ' +
        'FROM deploy_interruption_deliveries x JOIN deploy_interruptions d USING(interruption_id) ' +
        "LEFT JOIN tickets t ON d.owner_kind='ticket' AND t.id=d.owner_id " +
        "JOIN conversations c ON c.conversation_id=x.conversation_id WHERE x.status='unknown' " +
        'AND x.recovery_sender_message_id IS NOT NULL ORDER BY x.interruption_id',
    )) {
      const key = JSON.stringify([
        row.conversation_id,
        row.turn_number,
        row.recovery_sender_message_id,
      ]);
      const group = result.get(key) ?? {
        conversation_id: row.conversation_id,
        identity: row.recovery_sender_message_id,
        references: [],
      };
      group.references.push({ interruption_id: row.interruption_id, expected_stage: row.stage });
      result.set(key, group);
    }
    return [...result.values()];
  });
  for (const group of groups) {
    if (execution_refusal(database, group.conversation_id) !== null) continue;
    if (system.options.store.sender_message_outcome(group.conversation_id, group.identity) !== null)
      continue;
    await system.record_events_transaction(group.conversation_id, () => ({
      value: undefined,
      payloads: [
        new PromptDeliveryUncertainEventPayload({
          content: text_message_content(RECOVERY_PROMPT),
          sender_label: 'Panels deployment recovery',
          mode: PromptDeliveryMode.queue,
          sender_message_id: group.identity,
          recovery_references: group.references,
        }),
      ],
    }));
  }
}
export async function run_automatic_recovery(
  system: SqliteProcessConversationSystem,
  database: Database,
): Promise<void> {
  await system.workspace_admission.operation(
    async (admitted) => {
      if (admitted) await record_unknown_recovery_deliveries(system, database);
    },
    { starts: true },
  );
  await recover_conversations(system, database, { startup: true, signal: system.shutdown_signal });
}
