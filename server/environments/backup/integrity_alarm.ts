/**
 * A backup whose copy fails SQLite's integrity check tells the owner, like the stall
 * watchdog: a push under the Panels "Backup failed" switch, and a message to the Chief.
 * The nightly backup and the update snapshot both run outside the server, so neither
 * can raise an attention edge. The alarm never throws: the backup fails either way.
 */
import { createReadAccess } from '../../core/database/index.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import {
  BACKUP_FAILED_NOTIFICATION_TYPE,
  PANELS_NOTIFICATION_SUBJECT_KEY,
} from '../../notifications/contracts.ts';
import type { NotificationIntent } from '../../notifications/contracts.ts';
import { preference_key, resolved_preferences } from '../../notifications/data.ts';
import { read_push_targets, send_push } from '../../stalls/alarms.ts';
import type { PushTargets } from '../../stalls/alarms.ts';
import { open_alarm_transport } from '../../stalls/cli.ts';
import type { AlarmTransport } from '../../stalls/cli.ts';

export const BACKUP_ALARM_PUSH_TAG = 'panels-backup-failed';
export const BACKUP_ALARM_TEXT =
  'A backup of the Panels database failed its integrity check, so no new snapshot was kept. ' +
  'Earlier snapshots are unchanged. Check the database before the next update.';
export const BACKUP_ALARM_INTENT: NotificationIntent = {
  title: 'Panels: backup failed',
  body: BACKUP_ALARM_TEXT,
  route: '/#/',
  tag: BACKUP_ALARM_PUSH_TAG,
  reply: null,
};
export interface BackupAlarmOutcome {
  pushes_delivered: number;
  chief_accepted: boolean;
}

export async function raise_backup_integrity_alarm(
  database: string,
  transport: Omit<AlarmTransport, 'close'>,
  now_ms: number,
): Promise<BackupAlarmOutcome> {
  let reading: { enabled: boolean; targets: PushTargets } | null = null;
  try {
    const reader = createReadAccess(database);
    try {
      reading = reader.read((sql) => ({
        enabled: resolved_preferences(sql).get(
          preference_key(PANELS_NOTIFICATION_SUBJECT_KEY, BACKUP_FAILED_NOTIFICATION_TYPE),
        )!,
        targets: read_push_targets(sql),
      }));
    } finally {
      reader.close();
    }
  } catch (error) {
    console.error('backup alarm could not read its push devices', error);
  }
  let pushes_delivered = 0;
  if (reading?.enabled)
    try {
      pushes_delivered = await send_push(BACKUP_ALARM_INTENT, reading.targets, {
        web_push: transport.web_push,
        apns: transport.apns,
        origin: transport.push_origin,
        now_s: Math.floor(now_ms / 1000),
        source: {
          label: 'backup alarm',
          subject_id: 'backup',
          notification_type: BACKUP_FAILED_NOTIFICATION_TYPE,
        },
      });
    } catch (error) {
      console.error('backup alarm push failed', error);
    }
  let chief_accepted = false;
  try {
    chief_accepted = await transport.message_chief(`[Panels backup] ${BACKUP_ALARM_TEXT}`);
  } catch (error) {
    console.error('backup alarm Chief message failed', error);
  }
  return { pushes_delivered, chief_accepted };
}

/** The production alarm, with the same provider transport as the stall watchdog. */
export async function alarm_backup_integrity_failure(
  database: string,
  env: NodeJS.ProcessEnv = process.env,
): Promise<void> {
  let transport: AlarmTransport;
  try {
    transport = await open_alarm_transport(env, (path) => assert_test_database_path(path, env));
  } catch (error) {
    console.error('backup alarm has no provider transport', error);
    return;
  }
  try {
    const outcome = await raise_backup_integrity_alarm(database, transport, Date.now());
    console.error(
      `Backup alarm: ${outcome.pushes_delivered} pushes delivered, Chief ${outcome.chief_accepted ? 'accepted' : 'not reached'}`,
    );
  } finally {
    await transport.close().catch((error: unknown) => {
      console.warn('backup alarm transport did not close', error);
    });
  }
}
