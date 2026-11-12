import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { createReadAccess } from '../core/database/index.ts';
import type {
  APNsAdapter,
  APNsDevice,
  PushSubscription,
  WebPushAdapter,
  WebPushIdentity,
} from '../notifications/contracts.ts';
import { preference_key, resolved_preferences } from '../notifications/data.ts';
import { CHECKER_HEARTBEAT_STALE_SECONDS } from './configuration.ts';
import { read_open_findings } from './checker.ts';
import type { StallFinding } from './contracts.ts';
import { push_intent, send_push, since_utc, stall_text } from './alarms.ts';
import type { PushTargets, StallAlarm } from './alarms.ts';

export const WATCHDOG_STATE_FILE_NAME = 'stall-watchdog-state.json';
export interface WatchdogReading {
  findings: readonly StallFinding[];
  heartbeat_at_ms: number | null;
  push_enabled: boolean;
  push_targets: PushTargets;
}
export interface WatchdogRun {
  due: readonly StallAlarm[];
  raised: readonly StallAlarm[];
  pushes_delivered: number;
  chief_accepted: boolean;
}
/** The standalone command and direct comparison share the same summary text. */
export function watchdog_summary(run: WatchdogRun): string {
  const chief = run.chief_accepted ? 'accepted' : run.raised.length ? 'not reached' : 'not needed';
  return `${run.due.length} due, ${run.raised.length} raised, ${run.pushes_delivered} pushes delivered, Chief ${chief}`;
}
export function read_database(db_path: string): WatchdogReading {
  if (!db_path) throw new Error('watchdog requires an explicit database path');
  const database = createReadAccess(db_path);
  try {
    return database.read((sql) => {
      const heartbeat = sql.get<{ checked_at_ms: number }>(`
        SELECT checked_at_ms FROM stall_checker_heartbeat WHERE singleton=1
      `);
      const preferences = resolved_preferences(sql);
      const identity =
        sql.get<WebPushIdentity>(`
        SELECT private_key,public_key FROM notification_web_push_identity WHERE singleton=1
      `) ?? null;
      const subscriptions = sql.all<PushSubscription>(`
        SELECT subscription_id,endpoint,p256dh,auth FROM notification_push_subscriptions
        WHERE disabled_at IS NULL ORDER BY created_at,subscription_id
      `);
      const apns_devices = sql.all<APNsDevice>(`
        SELECT device_id,device_token,bundle_id,environment,registration_version,updated_at
        FROM notification_apns_devices ORDER BY created_at,device_id
      `);
      return {
        findings: read_open_findings(sql),
        heartbeat_at_ms: heartbeat?.checked_at_ms ?? null,
        push_enabled: preferences.get(preference_key('panels', 'stall_alarm'))!,
        push_targets: { web_push_identity: identity, subscriptions, apns_devices },
      };
    });
  } finally {
    database.close();
  }
}
export function due_alarms(reading: WatchdogReading, now_ms: number): readonly StallAlarm[] {
  const alarms: StallAlarm[] = [];
  const heartbeat = reading.heartbeat_at_ms;
  if (heartbeat === null || now_ms - heartbeat > CHECKER_HEARTBEAT_STALE_SECONDS * 1000) {
    const stopped =
      heartbeat === null ? 'has never run' : `has not run since ${since_utc(heartbeat)} UTC`;
    alarms.push({
      alarm_key: `checker_heartbeat_stale:${heartbeat === null ? 'None' : heartbeat}`,
      text: `Panels: the stall checker ${stopped}, so nothing is checked for stalls. The Panels server may be stuck or stopped.`,
      ticket_id: null,
    });
  }
  for (const finding of reading.findings)
    alarms.push({
      alarm_key: finding.finding_key,
      text: stall_text(finding),
      ticket_id: finding.ticket_id,
    });
  return alarms;
}
export async function run_watchdog(
  db_path: string,
  state_dir: string,
  options: {
    now_ms: number;
    web_push: WebPushAdapter;
    apns: APNsAdapter | null;
    push_origin: string | null;
    message_chief: (text: string) => boolean | Promise<boolean>;
  },
): Promise<WatchdogRun> {
  const reading = read_database(db_path);
  const due = due_alarms(reading, options.now_ms);
  const state_path = join(state_dir, WATCHDOG_STATE_FILE_NAME);
  const already_alarmed = read_state(state_path);
  const due_keys = new Set(due.map((alarm) => alarm.alarm_key));
  const raised = due.filter((alarm) => !already_alarmed.has(alarm.alarm_key));
  const recorded = new Set([...already_alarmed].filter((key) => due_keys.has(key)));
  let pushes_delivered = 0;
  let chief_accepted = false;
  if (raised.length) {
    if (reading.push_enabled) {
      try {
        pushes_delivered = await send_push(push_intent(raised), reading.push_targets, {
          web_push: options.web_push,
          apns: options.apns,
          origin: options.push_origin,
          now_s: Math.floor(options.now_ms / 1000),
        });
      } catch (error) {
        console.error('stall alarm push failed', error);
      }
    }
    try {
      chief_accepted = await options.message_chief(raised.map((alarm) => alarm.text).join('\n'));
    } catch (error) {
      console.error('stall alarm Chief message failed', error);
    }
    if (pushes_delivered || chief_accepted) {
      for (const alarm of raised) recorded.add(alarm.alarm_key);
    }
  }
  if (
    recorded.size !== already_alarmed.size ||
    [...recorded].some((key) => !already_alarmed.has(key))
  )
    write_state(state_path, recorded);
  return { due, raised, pushes_delivered, chief_accepted };
}
function read_state(path: string): Set<string> {
  try {
    const stored: unknown = JSON.parse(readFileSync(path, 'utf8'));
    if (!stored || typeof stored !== 'object' || !('alarmed' in stored)) return new Set();
    const keys = stored.alarmed;
    if (!Array.isArray(keys)) return new Set();
    return new Set(keys.filter((key): key is string => typeof key === 'string'));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT')
      console.error('stall watchdog state unreadable; starting again', error);
    return new Set();
  }
}
function write_state(path: string, alarmed: ReadonlySet<string>): void {
  mkdirSync(dirname(path), { recursive: true });
  const temporary = path + '.tmp';
  writeFileSync(temporary, JSON.stringify({ alarmed: [...alarmed].sort() }), 'utf8');
  renameSync(temporary, path);
}
