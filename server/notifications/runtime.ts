/** Explicitly composed single-owner notification delivery. No constructor starts it. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { WorkspaceAdmission } from '../conversation/workspace_admission.ts';
import type { APNsAdapter, WebPushAdapter } from './contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import { canonical_server_origin } from './apns.ts';
import * as data from './data.ts';
import * as apns_data from './apns_data.ts';
import * as live_activity_data from './live_activity_data.ts';
import { LIVE_ACTIVITY_RETRY_SECONDS } from './config.ts';
import { live_activity_payload, plan_live_activity, read_review_cards } from './live_activity.ts';
import { task_alert_is_current } from './attention.ts';
import { desktop_notification_streams } from './desktop_streams.ts';
export interface NotificationLoopOptions {
  database: Database;
  clock: Clock;
  canonical_origin: string | null;
  adapter: WebPushAdapter;
  apns_adapter: APNsAdapter | null;
  workspace_admission?: WorkspaceAdmission;
}
export class NotificationLoop {
  private readonly options: NotificationLoopOptions;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private active: Promise<number> | null = null;
  private stopped = true;
  private interval = 60;
  private stop_requested = false;
  private expired = false;
  /** A refused Live Activity push waits before the next try, so a commit wake cannot repeat it. */
  private readonly live_activity_retry_at = new Map<string, number>();
  private readonly pending = new Set<Promise<number>>();
  private closing: Promise<void> | undefined;
  private shutdown_settlement: Promise<void> | undefined;
  private wake_pending = false;
  constructor(options: NotificationLoopOptions) {
    this.options = options;
  }
  reconcile_imported_state(): void {
    this.options.database.write((sql) => data.reconcile_attention(sql));
  }
  poll_once(): Promise<number> {
    if (this.stop_requested) return Promise.resolve(0);
    // Publish ownership before a queue write or adapter can synchronously request stop.
    const task = Promise.resolve().then(() => (this.stop_requested ? 0 : this.deliver_once()));
    this.pending.add(task);
    return task.finally(() => this.pending.delete(task));
  }
  private async deliver_once(): Promise<number> {
    const admission = this.options.workspace_admission?.operation();
    if (admission && !admission.admitted) {
      admission.release();
      return 0;
    }
    try {
      const { database, clock, adapter, apns_adapter } = this.options;
      const now = clock.nowUnix();
      const allowed: Parameters<typeof data.queue_deliveries>[2] = [];
      database.write((sql) => data.queue_deliveries(sql, now, allowed));
      // Desktop apps get each new notification once, after its decision commits.
      const desktop = desktop_notification_streams(database);
      if (desktop.listening)
        for (const { edge, intent } of allowed)
          if (database.read((sql) => data.delivery_is_current(sql, edge, now)))
            desktop.send(intent);
      const identity = database.read((sql) => data.read_web_push_identity(sql));
      const deliveries = database.write((sql) => data.pending_deliveries(sql, now));
      let sent = 0;
      for (const delivery of deliveries) {
        if (this.stop_requested) return sent;
        const send_now = clock.nowUnix();
        if (!database.read((sql) => data.delivery_is_current(sql, delivery.edge, send_now))) {
          database.write((sql) => data.expire_stale_deliveries(sql, send_now));
          continue;
        }
        if (!database.read((sql) => task_alert_is_current(sql, delivery.edge))) continue;
        sent++;
        const result = await adapter.send(delivery.subscription, delivery.intent, identity, {
          subject: this.options.canonical_origin || 'https://localhost',
        });
        if (this.expired) return sent;
        database.write((sql) =>
          data.record_delivery_result(sql, delivery, { ...result, now: send_now }),
        );
      }
      if (this.stop_requested) return sent;
      const native = database.write((sql) => apns_data.pending_deliveries(sql, now));
      const origin = canonical_server_origin(this.options.canonical_origin);
      if (apns_adapter && origin) {
        for (const delivery of native) {
          if (this.stop_requested) return sent;
          const send_now = clock.nowUnix();
          if (!database.read((sql) => data.delivery_is_current(sql, delivery.edge, send_now))) {
            database.write((sql) => data.expire_stale_deliveries(sql, send_now, true));
            continue;
          }
          if (!database.read((sql) => task_alert_is_current(sql, delivery.edge))) continue;
          sent++;
          const result = await apns_adapter.send(delivery, { server_url: origin, now: send_now });
          if (this.expired) return sent;
          database.write((sql) => apns_data.record_result(sql, delivery, result, send_now));
        }
      }
      if (apns_adapter && !this.stop_requested)
        sent += await this.push_live_activities(apns_adapter);
      return sent;
    } finally {
      admission?.release();
    }
  }
  /** Tell each phone's Review Live Activity what the queue holds. Writes only when it sends. */
  private async push_live_activities(apns_adapter: APNsAdapter): Promise<number> {
    const { database, clock } = this.options;
    const devices = database.read((sql) => live_activity_data.live_activity_devices(sql));
    if (!devices.length) return 0;
    const cards = database.read((sql) => read_review_cards(sql));
    if (cards === null) return 0;
    let sent = 0;
    for (const device of devices) {
      if (this.stop_requested) return sent;
      const now = clock.nowUnix();
      if ((this.live_activity_retry_at.get(device.device_id) ?? 0) > now) continue;
      const plan = plan_live_activity(device, cards, now);
      if (!plan) continue;
      sent++;
      const result = await apns_adapter.send_live_activity(
        {
          token: plan.token,
          bundle_id: device.bundle_id,
          environment: device.environment,
          payload: live_activity_payload(plan, now),
        },
        { now },
      );
      if (this.expired) return sent;
      if (result.delivered) {
        this.live_activity_retry_at.delete(device.device_id);
        database.write((sql) =>
          live_activity_data.record_live_activity_sent(sql, device.device_id, {
            kind: plan.kind,
            cards: cards.map((card) => card.key),
            now,
          }),
        );
      } else {
        this.live_activity_retry_at.set(device.device_id, now + LIVE_ACTIVITY_RETRY_SECONDS);
        if (result.dead_token)
          database.write((sql) =>
            live_activity_data.record_live_activity_dead_token(
              sql,
              device.device_id,
              plan.kind === 'start' ? 'push_to_start_token' : 'update_token',
              now,
            ),
          );
      }
    }
    return sent;
  }
  start(interval: number): void {
    if (this.timer || this.active || this.stop_requested)
      throw new Error('notification loop already started or stopped');
    this.stopped = false;
    this.interval = interval;
    this.run();
  }
  wake(): void {
    if (this.stopped) return;
    if (this.active) {
      this.wake_pending = true;
      return;
    }
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.run();
  }
  private run(): void {
    if (this.stopped) return;
    // Install the in-flight latch before a queue write can emit a commit wake.
    this.active = Promise.resolve().then(() => this.poll_once());
    void this.active
      .catch((error) => console.error('notification poll failed', error))
      .finally(() => {
        this.active = null;
        if (!this.stopped) {
          const delay = this.wake_pending ? 0 : this.interval * 1000;
          this.wake_pending = false;
          this.timer = setTimeout(() => {
            this.timer = null;
            this.run();
          }, delay);
        }
      });
  }
  settled(): Promise<void> {
    return (
      this.shutdown_settlement ??
      Promise.allSettled([...this.pending, ...(this.active ? [this.active] : [])]).then(() => {})
    );
  }
  stop(options: StopOptions): Promise<void> {
    if (this.closing) return this.closing;
    this.stop_requested = true;
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const deadline = options.deadline;
    const sends = Promise.allSettled([...this.pending, ...(this.active ? [this.active] : [])]);
    this.shutdown_settlement = Promise.resolve().then(async () => {
      await sends;
      // A live send still owns the adapter. Close only after every send actually ended.
      await this.options.apns_adapter?.close();
    });
    this.closing = observe_shutdown(
      this.shutdown_settlement,
      { deadline },
      'Shutdown deadline expired; ownership retained: notifications',
      ['notifications'],
    ).finally(() => {
      // A late transport result remains owned, but cannot use the database again.
      this.expired = true;
    });
    return this.closing;
  }
}
