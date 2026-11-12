import type {
  APNsAdapter,
  APNsDevice,
  NotificationIntent,
  PendingAPNsDelivery,
  PushSubscription,
  WebPushAdapter,
  WebPushIdentity,
} from '../notifications/contracts.ts';
import type { TrackedWaitKind } from './contracts.ts';

export const CHIEF_MESSAGE_TIMEOUT_SECONDS = 10;
export const CHIEF_MESSAGE_PREFIX = '[Panels watchdog]';
export const STALL_ALARM_PUSH_TAG = 'panels-stall-alarm';
export const KIND_SENTENCES: Readonly<Record<TrackedWaitKind, string>> = {
  mac_items_not_moving: 'Mac events are not reaching the VPS',
  mac_command_unfetched: 'the Mac has not fetched a command',
  mac_stop_unproven: 'a stop on the Mac is not confirmed',
  mac_replacement_pending: "the restarted Mac has not finished sending this conversation's events",
  mac_turn_silent: 'a Mac turn has gone silent',
  mac_lease_failing: "the Mac's leases are not accepted",
  mac_item_still_saving: 'a Mac event is still saving',
  vps_save_retrying: 'a conversation save keeps failing',
  mac_cancel_retirement: 'retiring a Mac agent after a failed cancel is stuck',
  vps_replacement_retiring:
    "ending the restarted Mac's interrupted turn and sending its recovery prompt is stuck",
};
export interface StallAlarm {
  alarm_key: string;
  text: string;
  ticket_id: string | null;
}
export interface PushTargets {
  web_push_identity: WebPushIdentity | null;
  subscriptions: readonly PushSubscription[];
  apns_devices: readonly APNsDevice[];
}
export function since_utc(at_ms: number): string {
  return new Date(at_ms).toISOString().slice(11, 16);
}
export function stall_text(value: {
  kind: TrackedWaitKind;
  conversation_id: string | null;
  ticket_title: string | null;
  detail: string;
  stalled_since_ms: number;
}): string {
  const subject =
    value.ticket_title !== null
      ? `Ticket "${value.ticket_title}" (conversation ${value.conversation_id})`
      : value.conversation_id !== null
        ? `Conversation ${value.conversation_id}`
        : 'Panels';
  return `${subject}: ${KIND_SENTENCES[value.kind]}. ${value.detail}, since ${since_utc(value.stalled_since_ms)} UTC.`;
}
export function push_intent(alarms: readonly StallAlarm[]): NotificationIntent {
  const first = alarms[0]!;
  let body = first.text;
  if (alarms.length > 1) body += ` And ${alarms.length - 1} more.`;
  const route =
    alarms.length === 1 && first.ticket_id !== null ? `/#/workspace/${first.ticket_id}` : '/#/';
  return { title: 'Panels: work stalled', body, route, tag: STALL_ALARM_PUSH_TAG };
}
export async function send_push(
  intent: NotificationIntent,
  targets: PushTargets,
  options: {
    web_push: WebPushAdapter;
    apns: APNsAdapter | null;
    origin: string | null;
    now_s: number;
  },
): Promise<number> {
  let delivered = 0;
  if (targets.web_push_identity) {
    for (const subscription of targets.subscriptions) {
      const result = await options.web_push.send(subscription, intent, targets.web_push_identity, {
        subject: options.origin ?? 'https://localhost',
      });
      if (result.delivered) delivered++;
      else console.warn('stall alarm web push failed: %s', result.error);
    }
  }
  if (options.apns && options.origin) {
    for (const device of targets.apns_devices) {
      const delivery: PendingAPNsDelivery = {
        edge: {
          subject_kind: 'panels',
          subject_id: 'stall',
          notification_type: 'stall_alarm',
          generation: 1,
        },
        device,
        intent,
        attempts: 0,
        created_at: options.now_s,
      };
      const result = await options.apns.send(delivery, {
        server_url: options.origin,
        now: options.now_s,
      });
      if (result.delivered) delivered++;
      else console.warn('stall alarm native push failed: %s', result.error);
    }
  }
  return delivered;
}
export async function message_chief(text: string, server_url: string): Promise<boolean> {
  try {
    const response = await fetch(server_url.replace(/\/$/, '') + '/api/messages/send', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        target: { kind: 'chief', id: 'chief' },
        message: `${CHIEF_MESSAGE_PREFIX} ${text}`,
        mode: 'steer',
      }),
      signal: AbortSignal.timeout(CHIEF_MESSAGE_TIMEOUT_SECONDS * 1000),
    });
    const body: unknown = response.ok ? await response.json() : null;
    if (
      !body ||
      typeof body !== 'object' ||
      Array.isArray(body) ||
      'error' in body ||
      ('fate' in body && body.fate === 'refused')
    )
      return false;
    return true;
  } catch (error) {
    console.warn('stall alarm could not reach the Chief', error);
    return false;
  }
}
