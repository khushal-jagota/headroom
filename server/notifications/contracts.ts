import type { Principal } from '../core/contracts.ts';
export const TICKET_NOTIFICATION_SUBJECT_KEY = 'tickets';
export const MANAGER_NOTIFICATION_SUBJECT_KEY = 'managers';
export const PANELS_NOTIFICATION_SUBJECT_KEY = 'panels';
export const STALL_ALARM_NOTIFICATION_TYPE = 'stall_alarm';
export interface NotificationType {
  id: string;
  label: string;
  description: string;
  default_enabled: boolean;
}
export interface NotificationSubject {
  key: string;
  label: string;
  principal_kind: Principal['kind'] | null;
  notification_type_ids: readonly string[];
}
export const NOTIFICATION_TYPES: readonly NotificationType[] = [
  {
    id: 'awaiting_reply',
    label: 'Message',
    description: 'An unread message needs your reply.',
    default_enabled: true,
  },
  {
    id: 'awaiting_answer',
    label: 'Needs an answer',
    description: 'A worker is waiting on an answer only you can give.',
    default_enabled: true,
  },
  {
    id: 'awaiting_approval',
    label: 'Needs approval',
    description: 'A Ticket has a proposal waiting for your decision.',
    default_enabled: true,
  },
  {
    id: 'assigned',
    label: 'Assigned',
    description: 'A Ticket stage is assigned to you.',
    default_enabled: true,
  },
  {
    id: 'errored',
    label: 'Error',
    description: 'A worker turn or Ticket failed and needs attention.',
    default_enabled: true,
  },
  {
    id: 'stall_alarm',
    label: 'Stalled work',
    description: 'Panels work stopped moving and needs a look.',
    default_enabled: true,
  },
];
export const NOTIFICATION_TYPE_BY_ID = new Map(NOTIFICATION_TYPES.map((item) => [item.id, item]));
export const NOTIFICATION_SUBJECTS: readonly NotificationSubject[] = [
  {
    key: 'tickets',
    label: 'Tickets',
    principal_kind: 'ticket',
    notification_type_ids: [
      'awaiting_reply',
      'awaiting_answer',
      'awaiting_approval',
      'assigned',
      'errored',
    ],
  },
  {
    key: 'chief_of_staff',
    label: 'Chief of Staff',
    principal_kind: 'chief',
    notification_type_ids: ['awaiting_reply', 'awaiting_answer', 'errored'],
  },
  {
    key: 'managers',
    label: 'Managers',
    principal_kind: 'manager',
    notification_type_ids: ['awaiting_reply', 'awaiting_answer', 'errored'],
  },
  { key: 'panels', label: 'Panels', principal_kind: null, notification_type_ids: ['stall_alarm'] },
];
export function notification_preference_is_valid(
  subject_key: string,
  notification_type: string,
): boolean {
  return !!NOTIFICATION_SUBJECTS.find(
    (item) => item.key === subject_key,
  )?.notification_type_ids.includes(notification_type);
}
export interface EdgeKey {
  subject_kind: string;
  subject_id: string;
  notification_type: string;
  generation: number;
}
export interface AttentionEdge {
  key: EdgeKey;
  subject: Principal;
  subject_label: string;
  occurred_at: number;
}
export interface NotificationIntent {
  title: string;
  body: string;
  route: string;
  tag: string;
}
export interface PushSubscription {
  subscription_id: string;
  endpoint: string;
  p256dh: string;
  auth: string;
}
export interface PendingDelivery {
  edge: EdgeKey;
  subscription: PushSubscription;
  intent: NotificationIntent;
  attempts: number;
}
export interface WebPushIdentity {
  private_key: string;
  public_key: string;
}
export interface WebPushResult {
  delivered: boolean;
  expired?: boolean;
  error?: string | null;
}
export interface APNsConfig {
  key_path: string;
  key_id: string;
  team_id: string;
  bundle_ids: readonly string[];
}
export interface APNsDevice {
  device_id: string;
  device_token: string;
  bundle_id: string;
  environment: 'development' | 'production';
  registration_version: number;
  updated_at: number;
}
export interface PendingAPNsDelivery {
  edge: EdgeKey;
  device: APNsDevice;
  intent: NotificationIntent;
  attempts: number;
  created_at: number;
}
export interface APNsResult {
  delivered: boolean;
  dead_token?: boolean;
  retryable?: boolean;
  error?: string | null;
  invalidated_at_ms?: number | null;
}
export interface WebPushAdapter {
  send(
    subscription: PushSubscription,
    intent: NotificationIntent,
    identity: WebPushIdentity,
    options: { subject: string },
  ): Promise<WebPushResult>;
}
export interface APNsAdapter {
  send(
    delivery: PendingAPNsDelivery,
    options: { server_url: string; now: number },
  ): Promise<APNsResult>;
  close(): void | Promise<void>;
}
