import type { TransactionSql, ReadSql } from '../core/database/contracts.ts';
import type { APNsDevice } from './contracts.ts';
import type { ActivityDeviceState } from './live_activity.ts';

export interface LiveActivityDevice extends ActivityDeviceState {
  device_id: string;
  bundle_id: string;
  environment: APNsDevice['environment'];
}
/** What the app reports. A field left out keeps its value. Null clears it. */
export interface LiveActivityReport {
  push_to_start_token?: string | null;
  update_token?: string | null;
}

/** False when the device is not registered for notifications. */
export function register_live_activity(
  sql: TransactionSql,
  device: { device_token: string; bundle_id: string; environment: APNsDevice['environment'] },
  report: LiveActivityReport,
  now: number,
): boolean {
  const row = sql.get<{ device_id: string }>(
    'SELECT device_id FROM notification_apns_devices WHERE owner_id=? AND device_token=? AND bundle_id=? AND environment=?',
    ['owner', device.device_token, device.bundle_id, device.environment],
  );
  if (!row) return false;
  sql.run(
    'INSERT INTO notification_live_activities (device_id,updated_at) VALUES (?,?) ON CONFLICT(device_id) DO NOTHING',
    [row.device_id, now],
  );
  if (report.push_to_start_token !== undefined)
    sql.run(
      'UPDATE notification_live_activities SET push_to_start_token=?,updated_at=? WHERE device_id=?',
      [report.push_to_start_token, now, row.device_id],
    );
  if (report.update_token !== undefined) {
    // A new activity knows nothing the server sent: forget the last push so the next one is whole.
    sql.run(
      'UPDATE notification_live_activities SET update_token=?,started_at=?,last_cards_json=NULL,updated_at=? WHERE device_id=? AND update_token IS NOT ?',
      [
        report.update_token,
        report.update_token === null ? null : now,
        now,
        row.device_id,
        report.update_token,
      ],
    );
  }
  return true;
}

export function live_activity_devices(sql: ReadSql): LiveActivityDevice[] {
  return sql
    .all<{
      device_id: string;
      bundle_id: string;
      environment: APNsDevice['environment'];
      push_to_start_token: string | null;
      update_token: string | null;
      started_at: number | null;
      last_cards_json: string | null;
    }>(
      `SELECT a.device_id,d.bundle_id,d.environment,a.push_to_start_token,a.update_token,a.started_at,a.last_cards_json
       FROM notification_live_activities a JOIN notification_apns_devices d USING(device_id)
       WHERE a.push_to_start_token IS NOT NULL OR a.update_token IS NOT NULL ORDER BY a.device_id`,
    )
    .map(({ last_cards_json, ...row }) => ({
      ...row,
      last_cards: last_cards_json === null ? null : (JSON.parse(last_cards_json) as string[]),
    }));
}

export function record_live_activity_sent(
  sql: TransactionSql,
  device_id: string,
  sent: { kind: 'update' | 'start'; cards: string[]; now: number },
): void {
  const cards = JSON.stringify(sent.cards);
  // A start has no update token yet: the app reports one when the activity exists.
  if (sent.kind === 'start')
    sql.run(
      'UPDATE notification_live_activities SET update_token=NULL,started_at=?,last_cards_json=?,updated_at=? WHERE device_id=?',
      [sent.now, cards, sent.now, device_id],
    );
  else
    sql.run(
      'UPDATE notification_live_activities SET last_cards_json=?,updated_at=? WHERE device_id=?',
      [cards, sent.now, device_id],
    );
}

/** Apple refused a token for good: forget that token only. The device stays registered. */
export function record_live_activity_dead_token(
  sql: TransactionSql,
  device_id: string,
  token: 'push_to_start_token' | 'update_token',
  now: number,
): void {
  sql.run(
    token === 'update_token'
      ? 'UPDATE notification_live_activities SET update_token=NULL,started_at=NULL,last_cards_json=NULL,updated_at=? WHERE device_id=?'
      : 'UPDATE notification_live_activities SET push_to_start_token=NULL,updated_at=? WHERE device_id=?',
    [now, device_id],
  );
}
