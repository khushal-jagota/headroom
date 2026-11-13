/** Startup recovery uses the same send door and immutable sender identities as ordinary work. */
import type { Database } from '../../core/database/contracts.ts';
import type { ConversationSystem, DeploymentRecoveryReference } from '../contracts.ts';
import { PromptDeliveryMode } from '../contracts.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';
import { text_message_content } from '../message_content.ts';
import { PromptDeliveryUncertainEventPayload } from '../events.ts';
import { select_recoveries } from './data.ts';
import { recovery_message_identity } from './identity.ts';
import { conversation_execution_refusal } from '../../tickets/execution_admission.ts';
import { configured_ticket_execution_refusal } from '../../runtime/conversation_start.ts';
function execution_refusal(database: Database, id: string) {
  return database.read((sql) =>
    conversation_execution_refusal(sql, id, (current, ticket) =>
      configured_ticket_execution_refusal(database, current, ticket),
    ),
  );
}
export const RECOVERY_PROMPT =
  'Inspect the interrupted work and continue the current authorized step.';
export async function recover_conversations(
  system: ConversationSystem,
  database: Database,
  options: {
    conversation_ids?: readonly string[];
    mode?: PromptDeliveryMode;
  } = {},
): Promise<void> {
  const selected = database.read(select_recoveries);
  await Promise.all(
    [...selected]
      .filter(
        ([id]) => options.conversation_ids === undefined || options.conversation_ids.includes(id),
      )
      .map(async ([conversation_id, references]) => {
        if (execution_refusal(database, conversation_id) !== null) return;
        if (await system.is_running(conversation_id)) return;
        try {
          await system.send(conversation_id, text_message_content(RECOVERY_PROMPT), {
            sender_label: 'Panels deployment recovery',
            mode: options.mode ?? PromptDeliveryMode.queue,
            sender_message_id: recovery_message_identity(
              references.map((ref) => ref.interruption_id),
            ),
            record_refusal: true,
            recovery_references: references,
          });
        } catch (error) {
          if (error instanceof DOMException && error.name === 'AbortError') throw error;
          console.error(`Automatic recovery failed for conversation ${conversation_id}`, error);
        }
      }),
  );
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
  await record_unknown_recovery_deliveries(system, database);
  await recover_conversations(system, database);
}
