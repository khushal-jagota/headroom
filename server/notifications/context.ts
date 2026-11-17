/** Snapshot optional notification copy while the source write owns the transaction. */
import type { ReadSql } from '../core/database/contracts.ts';
import { workflow_from_row } from '../worker_types/store.ts';
import { ConversationEventKind, MessageToOwnerEventPayload } from '../conversation/events.ts';
import { conversation_event_payload_from_canonical_json } from '../conversation/record/codec.ts';
import { message_content_text } from '../conversation/message_content.ts';
import type { NotificationMessage } from './contracts.ts';
import { message_preview, notification_label } from './logic/preview.ts';

function edge_context_json(sql: ReadSql, kind: string, id: string, type: string): string | null {
  if (!['awaiting_reply', 'awaiting_approval'].includes(type)) return null;
  const row = sql.get<{
    title: string;
    conversation_id: string | null;
    stage: string;
    workflow_json: string;
  }>(
    kind === 'ticket'
      ? 'SELECT title,conversation_id,stage,workflow_json FROM tickets WHERE id=?'
      : kind === 'manager'
        ? "SELECT i.title,a.conversation_id FROM managers i LEFT JOIN agents a ON a.agent_key='manager_'||i.id WHERE i.id=?"
        : "SELECT 'Chief of Staff' AS title,conversation_id FROM agents WHERE agent_key=?",
    [id],
  );
  if (!row) return null;
  const context: { subject_label: string; stage_label?: string; message?: NotificationMessage } = {
    subject_label: notification_label(row.title),
  };
  if (type === 'awaiting_approval') {
    const stage = workflow_from_row(row).stages.find((stage) => stage.id === row.stage);
    if (!stage) throw new Error('notification stage is unavailable');
    context.stage_label = notification_label(stage.label);
  } else if (row.conversation_id !== null) {
    const message = sql.get<{ sequence: number; payload: string }>(
      "SELECT sequence,payload FROM conversation_events WHERE conversation_id=? AND kind='message_to_owner' ORDER BY sequence DESC LIMIT 1",
      [row.conversation_id],
    );
    if (message) {
      const payload = conversation_event_payload_from_canonical_json(
        ConversationEventKind.message_to_owner,
        message.payload,
      );
      if (!(payload instanceof MessageToOwnerEventPayload))
        throw new Error('notification message is unavailable');
      context.message = {
        sender_label: notification_label(payload.sender_label),
        preview: message_preview(message_content_text(payload.content)),
        reply: ['chief', 'manager', 'ticket'].includes(payload.sender.kind)
          ? {
              conversation_id: row.conversation_id,
              message_id: `${row.conversation_id}:${message.sequence}`,
              recipient_kind: payload.sender.kind,
              recipient_id: payload.sender.id,
            }
          : null,
      };
    }
  }
  return JSON.stringify(context);
}
export function optional_edge_context_json(
  sql: ReadSql,
  kind: string,
  id: string,
  type: string,
): string | null {
  try {
    return edge_context_json(sql, kind, id, type);
  } catch (error) {
    console.warn(`Notification context unavailable; queued generic alert (${type})`, error);
    return null;
  }
}
