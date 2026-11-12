import { randomUUID } from 'node:crypto';
import type { TransactionSql } from '../core/database/contracts.ts';
import type {
  APNsDevice,
  APNsResult,
  EdgeKey,
  NotificationIntent,
  PendingAPNsDelivery,
} from './contracts.ts';
import {
  APNS_BATCH_SIZE,
  APNS_DELIVERY_TTL_SECONDS,
  APNS_MAX_ATTEMPTS,
  APNS_RETRY_BASE_SECONDS,
} from './config.ts';
export function register_device(
  sql: TransactionSql,
  options: {
    device_token: string;
    bundle_id: string;
    environment: APNsDevice['environment'];
    now: number;
  },
): void {
  sql.run(
    `INSERT INTO notification_apns_devices
    (device_id,owner_id,device_token,bundle_id,environment,registration_version,created_at,updated_at)
    VALUES (?,?,?,?,?,1,?,?) ON CONFLICT(owner_id,device_token,bundle_id,environment)
    DO UPDATE SET updated_at=excluded.updated_at,registration_version=registration_version+1`,
    [
      randomUUID().replaceAll('-', ''),
      'owner',
      options.device_token,
      options.bundle_id,
      options.environment,
      options.now,
      options.now,
    ],
  );
}
export function remove_device(
  sql: TransactionSql,
  options: { device_token: string; bundle_id: string; environment: APNsDevice['environment'] },
): boolean {
  return (
    sql.run(
      `DELETE FROM notification_apns_devices
    WHERE owner_id=? AND device_token=? AND bundle_id=? AND environment=?`,
      ['owner', options.device_token, options.bundle_id, options.environment],
    ).changes > 0
  );
}
export function queue_intent(
  sql: TransactionSql,
  edge: EdgeKey,
  intent: NotificationIntent,
  now: number,
): void {
  sql.run(
    `INSERT OR IGNORE INTO notification_apns_deliveries
    (subject_kind,subject_id,notification_type,generation,device_id,title,body,route,tag,created_at,status,attempts,next_attempt_at)
    SELECT ?,?,?,?,device_id,?,?,?,?,?,'pending',0,? FROM notification_apns_devices WHERE owner_id=?`,
    [
      edge.subject_kind,
      edge.subject_id,
      edge.notification_type,
      edge.generation,
      intent.title,
      intent.body,
      intent.route,
      intent.tag,
      now,
      now,
      'owner',
    ],
  );
}
export function pending_deliveries(sql: TransactionSql, now: number): PendingAPNsDelivery[] {
  sql.run(
    `UPDATE notification_apns_deliveries SET status='expired'
    WHERE status IN ('pending','retry') AND created_at<=?`,
    [now - APNS_DELIVERY_TTL_SECONDS],
  );
  return sql
    .all<EdgeKey & APNsDevice & NotificationIntent & { attempts: number; created_at: number }>(
      `SELECT d.*,s.device_token,s.bundle_id,s.environment,s.registration_version,s.updated_at
     FROM notification_apns_deliveries d JOIN notification_apns_devices s USING(device_id)
     WHERE d.status IN ('pending','retry') AND d.next_attempt_at<=?
       AND ((d.subject_kind='ticket' AND EXISTS (SELECT 1 FROM tickets WHERE id=d.subject_id))
         OR (d.subject_kind='manager' AND EXISTS (SELECT 1 FROM managers WHERE id=d.subject_id))
         OR (d.subject_kind='agent' AND EXISTS (SELECT 1 FROM agents WHERE agent_key=d.subject_id)))
     ORDER BY d.next_attempt_at,d.device_id LIMIT ?`,
      [now, APNS_BATCH_SIZE],
    )
    .map((row) => ({
      edge: {
        subject_kind: row.subject_kind,
        subject_id: row.subject_id,
        notification_type: row.notification_type,
        generation: row.generation,
      },
      device: {
        device_id: row.device_id,
        device_token: row.device_token,
        bundle_id: row.bundle_id,
        environment: row.environment,
        registration_version: row.registration_version,
        updated_at: row.updated_at,
      },
      intent: { title: row.title, body: row.body, route: row.route, tag: row.tag },
      attempts: row.attempts,
      created_at: row.created_at,
    }));
}
export function record_result(
  sql: TransactionSql,
  delivery: PendingAPNsDelivery,
  result: APNsResult,
  now: number,
): void {
  const device = delivery.device;
  if (result.dead_token)
    sql.run(
      `DELETE FROM notification_apns_devices
    WHERE device_id=? AND registration_version=? AND (? IS NULL OR updated_at*1000<=?)`,
      [
        device.device_id,
        device.registration_version,
        result.invalidated_at_ms ?? null,
        result.invalidated_at_ms ?? null,
      ],
    );
  const attempts = delivery.attempts + 1;
  const next = now + APNS_RETRY_BASE_SECONDS * 2 ** (attempts - 1);
  const status = result.delivered
    ? 'delivered'
    : result.retryable &&
        attempts < APNS_MAX_ATTEMPTS &&
        next < delivery.created_at + APNS_DELIVERY_TTL_SECONDS
      ? 'retry'
      : 'failed';
  sql.run(
    `UPDATE notification_apns_deliveries SET status=?,attempts=?,next_attempt_at=?,delivered_at=?,last_error=?
    WHERE subject_kind=? AND subject_id=? AND notification_type=? AND generation=? AND device_id=?`,
    [
      status,
      attempts,
      next,
      result.delivered ? now : null,
      result.error ?? null,
      delivery.edge.subject_kind,
      delivery.edge.subject_id,
      delivery.edge.notification_type,
      delivery.edge.generation,
      device.device_id,
    ],
  );
}
