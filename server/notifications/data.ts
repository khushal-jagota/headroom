import { createHash, generateKeyPairSync } from 'node:crypto';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import type {
  AttentionEdge,
  EdgeKey,
  PendingDelivery,
  PushSubscription,
  WebPushIdentity,
  WebPushResult,
} from './contracts.ts';
import {
  NOTIFICATION_SUBJECTS,
  NOTIFICATION_TYPE_BY_ID,
  notification_preference_is_valid,
} from './contracts.ts';
import {
  conversation_attention,
  NOTHING_WAITING,
  reconcile_attention as reconcile_source_attention,
} from './attention.ts';
import { workflow_from_row } from '../worker_types/store.ts';
import { decide_notification } from './logic/policy.ts';
import { queue_intent } from './apns_data.ts';
export function preference_key(subject: string, type: string): string {
  return `${subject}/${type}`;
}
export function resolved_preferences(sql: ReadSql): Map<string, boolean> {
  const stored = new Map(
    sql
      .all<{ subject_key: string; notification_type: string; enabled: number }>(
        'SELECT subject_key,notification_type,enabled FROM notification_preferences',
      )
      .map((row) => [preference_key(row.subject_key, row.notification_type), !!row.enabled]),
  );
  const result = new Map<string, boolean>();
  for (const subject of NOTIFICATION_SUBJECTS)
    for (const type of subject.notification_type_ids) {
      const key = preference_key(subject.key, type);
      result.set(key, stored.get(key) ?? NOTIFICATION_TYPE_BY_ID.get(type)!.default_enabled);
    }
  return result;
}
export function set_preference(
  sql: TransactionSql,
  subject: string,
  type: string,
  enabled: boolean,
  now: number,
): void {
  if (!notification_preference_is_valid(subject, type))
    throw new Error(`unknown notification preference: ${subject}/${type}`);
  sql.run(
    `INSERT INTO notification_preferences(subject_key,notification_type,enabled,updated_at)
    VALUES (?,?,?,?) ON CONFLICT(subject_key,notification_type)
    DO UPDATE SET enabled=excluded.enabled,updated_at=excluded.updated_at`,
    [subject, type, Number(enabled), now],
  );
}
export function seed_shipped_notification_preferences(sql: TransactionSql): void {
  if (sql.get('SELECT 1 FROM notification_preferences LIMIT 1')) return;
  const historical = !!sql.get("SELECT 1 FROM sqlite_master WHERE name='sprint_items'");
  sql.run(
    `INSERT INTO notification_preferences(subject_key,notification_type,enabled,updated_at)
    VALUES (?,'errored',0,0)`,
    [historical ? 'sprint_item_supervisors' : 'managers'],
  );
}
export function get_or_create_web_push_identity(sql: TransactionSql, now: number): WebPushIdentity {
  const existing = sql.get<WebPushIdentity>(
    'SELECT private_key,public_key FROM notification_web_push_identity WHERE singleton=1',
  );
  if (existing) return existing;
  const { privateKey, publicKey } = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const jwk = publicKey.export({ format: 'jwk' });
  const public_key = Buffer.concat([
    Buffer.from([4]),
    Buffer.from(jwk.x!, 'base64url'),
    Buffer.from(jwk.y!, 'base64url'),
  ]).toString('base64url');
  sql.run(
    `INSERT OR IGNORE INTO notification_web_push_identity(singleton,private_key,public_key,created_at)
    VALUES (1,?,?,?)`,
    [privateKey.export({ format: 'der', type: 'pkcs8' }).toString('base64url'), public_key, now],
  );
  return read_web_push_identity(sql);
}
export function initialize_notifications(sql: TransactionSql, now: number): void {
  seed_shipped_notification_preferences(sql);
  get_or_create_web_push_identity(sql, now);
}
export function read_web_push_identity(sql: ReadSql): WebPushIdentity {
  const row = sql.get<WebPushIdentity>(
    'SELECT private_key,public_key FROM notification_web_push_identity WHERE singleton=1',
  );
  if (!row) throw new Error('Web Push identity was not initialized');
  return row;
}
export function register_subscription(
  sql: TransactionSql,
  options: { endpoint: string; p256dh: string; auth: string; now: number },
): PushSubscription {
  const { endpoint, p256dh, auth, now } = options;
  const id = createHash('sha256').update(endpoint).digest('hex').slice(0, 24);
  sql.run(
    `INSERT INTO notification_push_subscriptions
    (subscription_id,endpoint,p256dh,auth,created_at,updated_at,disabled_at)
    VALUES (?,?,?,?,?,?,NULL) ON CONFLICT(endpoint) DO UPDATE SET
    p256dh=excluded.p256dh,auth=excluded.auth,updated_at=excluded.updated_at,disabled_at=NULL`,
    [id, endpoint, p256dh, auth, now, now],
  );
  return sql.get<PushSubscription>(
    'SELECT subscription_id,endpoint,p256dh,auth FROM notification_push_subscriptions WHERE endpoint=?',
    [endpoint],
  )!;
}
export function remove_subscription(sql: TransactionSql, id: string): boolean {
  return (
    sql.run('DELETE FROM notification_push_subscriptions WHERE subscription_id=?', [id]).changes > 0
  );
}
export function active_subscription_ids(sql: ReadSql): string[] {
  return sql
    .all<{ subscription_id: string }>(
      `SELECT subscription_id FROM notification_push_subscriptions
    WHERE disabled_at IS NULL ORDER BY created_at,subscription_id`,
    )
    .map((row) => row.subscription_id);
}
export function prune_missing_subjects(sql: TransactionSql): void {
  for (const table of [
    'notification_attention_edges',
    'notification_attention_state',
    'notification_deliveries',
  ])
    sql.run(
      `DELETE FROM ${table} WHERE
      (subject_kind='ticket' AND NOT EXISTS (SELECT 1 FROM tickets WHERE tickets.id=${table}.subject_id))
      OR (subject_kind='manager' AND NOT EXISTS (SELECT 1 FROM managers WHERE managers.id=${table}.subject_id))
      OR (subject_kind='agent' AND NOT EXISTS (SELECT 1 FROM agents WHERE agents.agent_key=${table}.subject_id))`,
    );
}
export function reconcile_attention(sql: TransactionSql): void {
  prune_missing_subjects(sql);
  const conversations = conversation_attention(sql);
  const desired: Parameters<typeof reconcile_source_attention>[1][number][] = [];
  for (const row of sql.all<{
    id: string;
    stage: string;
    worker_type: string | null;
    workflow_json: string;
    worker_step_claim: string;
    pending_proposal: string | null;
    ceiling_holder: string;
    conversation_id: string | null;
    updated_at: number;
  }>('SELECT * FROM tickets ORDER BY id')) {
    const conversation = conversations.get(row.conversation_id ?? '') ?? NOTHING_WAITING;
    const holder = JSON.parse(row.ceiling_holder);
    const parked = row.pending_proposal
      ? (JSON.parse(row.pending_proposal) as { created_at: number })
      : null;
    const flags = {
      awaiting_reply: conversation.unread_message,
      awaiting_answer: conversation.pending_ask,
      awaiting_approval:
        row.worker_step_claim !== 'errored' &&
        parked !== null &&
        holder.kind === 'owner' &&
        holder.id === 'owner',
      assigned:
        workflow_from_row(row).stages.find((s) => s.id === row.stage)?.ownership_mode === 'user',
      errored: row.worker_step_claim === 'errored' || conversation.errored,
    };
    for (const [notification_type, active] of Object.entries(flags))
      desired.push({
        subject_kind: 'ticket',
        subject_id: row.id,
        notification_type,
        active,
        occurred_at:
          ['awaiting_reply', 'awaiting_answer', 'errored'].includes(notification_type) &&
          conversation.occurred_at
            ? conversation.occurred_at
            : notification_type === 'awaiting_approval' && parked
              ? parked.created_at
              : row.updated_at,
      });
  }
  for (const row of sql.all<{
    agent_key: string;
    conversation_id: string | null;
    item_id: string | null;
  }>(
    `SELECT a.agent_key,a.conversation_id,i.id item_id FROM agents a
     LEFT JOIN managers i ON a.agent_key='manager_' || i.id
     WHERE a.agent_key='chief_of_staff' OR i.id IS NOT NULL ORDER BY a.agent_key`,
  )) {
    const conversation = conversations.get(row.conversation_id ?? '') ?? NOTHING_WAITING;
    for (const [notification_type, active] of [
      ['awaiting_reply', conversation.unread_message],
      ['awaiting_answer', conversation.pending_ask],
      ['errored', conversation.errored],
    ] as const)
      desired.push({
        subject_kind: row.item_id ? 'manager' : 'agent',
        subject_id: row.item_id ?? row.agent_key,
        notification_type,
        active,
        occurred_at: conversation.occurred_at,
      });
  }
  reconcile_source_attention(sql, desired);
}
export function queue_deliveries(sql: TransactionSql, now: number): number {
  prune_missing_subjects(sql);
  const preferences = resolved_preferences(sql);
  const subscriptions = active_subscription_ids(sql);
  const rows = sql.all<EdgeKey & { occurred_at: number }>(
    `SELECT subject_kind,subject_id,notification_type,generation,occurred_at
     FROM notification_attention_edges WHERE decided=0 AND subject_kind IN ('ticket','manager','agent')
     ORDER BY occurred_at,subject_kind,subject_id,notification_type,generation`,
  );
  let decided = 0;
  for (const edge of rows) {
    const subject =
      edge.subject_kind === 'agent' && edge.subject_id === 'chief_of_staff'
        ? { kind: 'chief' as const, id: 'chief' }
        : edge.subject_kind === 'ticket'
          ? { kind: 'ticket' as const, id: edge.subject_id }
          : edge.subject_kind === 'manager'
            ? { kind: 'manager' as const, id: edge.subject_id }
            : null;
    if (!subject)
      throw new Error(`unknown notification subject: ${edge.subject_kind}/${edge.subject_id}`);
    const label =
      subject.kind === 'chief'
        ? 'Chief of Staff'
        : sql.get<{ title: string }>(
            `SELECT title FROM ${subject.kind === 'ticket' ? 'tickets' : 'managers'} WHERE id=?`,
            [subject.id],
          )?.title;
    if (label === undefined) continue;
    const key =
      subject.kind === 'ticket'
        ? 'tickets'
        : subject.kind === 'manager'
          ? 'managers'
          : 'chief_of_staff';
    const attention: AttentionEdge = {
      key: edge,
      subject,
      subject_label: label,
      occurred_at: edge.occurred_at,
    };
    const intent = decide_notification(attention, {
      enabled: preferences.get(preference_key(key, edge.notification_type)) ?? false,
    });
    if (intent) {
      queue_intent(sql, edge, intent, now);
      for (const id of subscriptions)
        sql.run(
          `INSERT OR IGNORE INTO notification_deliveries
        (subject_kind,subject_id,notification_type,generation,subscription_id,title,body,route,tag,created_at,status,attempts,next_attempt_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,'pending',0,?)`,
          [
            edge.subject_kind,
            edge.subject_id,
            edge.notification_type,
            edge.generation,
            id,
            intent.title,
            intent.body,
            intent.route,
            intent.tag,
            now,
            now,
          ],
        );
    }
    sql.run(
      `UPDATE notification_attention_edges SET decided=1
      WHERE subject_kind=? AND subject_id=? AND notification_type=? AND generation=?`,
      [edge.subject_kind, edge.subject_id, edge.notification_type, edge.generation],
    );
    decided++;
  }
  return decided;
}
export const SUBJECT_STILL_EXISTS = `((d.subject_kind='ticket' AND EXISTS (SELECT 1 FROM tickets WHERE id=d.subject_id))
  OR (d.subject_kind='manager' AND EXISTS (SELECT 1 FROM managers WHERE id=d.subject_id))
  OR (d.subject_kind='agent' AND EXISTS (SELECT 1 FROM agents WHERE agent_key=d.subject_id)))`;
export function pending_deliveries(sql: ReadSql, now: number, limit = 50): PendingDelivery[] {
  return sql
    .all<
      EdgeKey &
        PushSubscription & {
          title: string;
          body: string;
          route: string;
          tag: string;
          attempts: number;
        }
    >(
      `SELECT d.subject_kind,d.subject_id,d.notification_type,d.generation,d.attempts,d.title,d.body,d.route,d.tag,
       s.subscription_id,s.endpoint,s.p256dh,s.auth
     FROM notification_deliveries d JOIN notification_push_subscriptions s ON s.subscription_id=d.subscription_id
     WHERE d.status IN ('pending','retry') AND d.next_attempt_at<=? AND s.disabled_at IS NULL
       AND ${SUBJECT_STILL_EXISTS}
     ORDER BY d.next_attempt_at,d.subject_kind,d.subject_id,d.notification_type,d.generation LIMIT ?`,
      [now, limit],
    )
    .map((row) => ({
      edge: {
        subject_kind: row.subject_kind,
        subject_id: row.subject_id,
        notification_type: row.notification_type,
        generation: row.generation,
      },
      subscription: {
        subscription_id: row.subscription_id,
        endpoint: row.endpoint,
        p256dh: row.p256dh,
        auth: row.auth,
      },
      intent: { title: row.title, body: row.body, route: row.route, tag: row.tag },
      attempts: row.attempts,
    }));
}
export function record_delivery_result(
  sql: TransactionSql,
  delivery: PendingDelivery,
  options: { now: number } & WebPushResult,
): void {
  const attempts = delivery.attempts + 1;
  const key = [
    delivery.edge.subject_kind,
    delivery.edge.subject_id,
    delivery.edge.notification_type,
    delivery.edge.generation,
    delivery.subscription.subscription_id,
  ];
  const where =
    ' WHERE subject_kind=? AND subject_id=? AND notification_type=? AND generation=? AND subscription_id=?';
  if (options.delivered) {
    sql.run(
      `UPDATE notification_deliveries SET status='delivered',attempts=?,last_error=NULL,delivered_at=?${where}`,
      [attempts, options.now, ...key],
    );
  } else if (options.expired) {
    sql.run('UPDATE notification_push_subscriptions SET disabled_at=? WHERE subscription_id=?', [
      options.now,
      delivery.subscription.subscription_id,
    ]);
    sql.run(`UPDATE notification_deliveries SET status='expired',attempts=?,last_error=?${where}`, [
      attempts,
      options.error ?? null,
      ...key,
    ]);
  } else {
    const next = options.now + Math.min(3600, 30 * 2 ** Math.min(attempts - 1, 7));
    sql.run(
      `UPDATE notification_deliveries SET status='retry',attempts=?,next_attempt_at=?,last_error=?${where}`,
      [attempts, next, options.error ?? null, ...key],
    );
  }
}
