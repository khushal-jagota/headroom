import type { AttentionEdge, NotificationIntent } from '../contracts.ts';
import { NOTIFICATION_TYPE_BY_ID } from '../contracts.ts';
import { notification_label } from './preview.ts';
const REASONS: Record<string, string> = {
  awaiting_reply: 'has a message for you',
  awaiting_answer: 'is waiting on your answer',
  awaiting_approval: 'needs your approval',
  assigned: 'is assigned to you',
  errored: 'has an error',
};
export function decide_notification(
  edge: AttentionEdge,
  options: { enabled: boolean },
): NotificationIntent | null {
  if (!NOTIFICATION_TYPE_BY_ID.has(edge.key.notification_type))
    throw new Error(`unknown notification type: ${edge.key.notification_type}`);
  const kind = edge.subject.kind;
  if (!['ticket', 'chief', 'manager'].includes(kind))
    throw new Error(`unknown notification subject kind: ${kind}`);
  if (!options.enabled) return null;
  const message = edge.key.notification_type === 'awaiting_reply' ? edge.message : null;
  const approval = edge.key.notification_type === 'awaiting_approval' && edge.stage_label != null;
  return {
    title: message
      ? message.sender_label
      : approval
        ? notification_label(edge.subject_label)
        : 'Panels',
    body: message
      ? message.preview
      : approval
        ? `${edge.stage_label} waits for your approval.`
        : `${notification_label(edge.subject_label)} ${REASONS[edge.key.notification_type]}.`,
    reply: message?.reply ?? null,
    route:
      kind === 'ticket'
        ? `/#/workspace/${edge.subject.id}`
        : kind === 'chief'
          ? '/#/agents/chief-of-staff'
          : `/#/workspace/item/${edge.subject.id}`,
    tag: kind === 'chief' ? 'panels-agent-chief_of_staff' : `panels-${kind}-${edge.subject.id}`,
  };
}
