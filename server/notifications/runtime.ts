/** Explicitly composed single-owner notification delivery. No constructor starts it. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { WorkspaceAdmission } from '../conversation/workspace_admission.ts';
import type { APNsAdapter, WebPushAdapter } from './contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { SHUTDOWN_GRACE_SECONDS } from '../core/config.ts';
import { observe_shutdown } from '../lifecycle/deadline.ts';
import { canonical_server_origin } from './apns.ts';
import * as data from './data.ts';
import * as apns_data from './apns_data.ts';
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
      database.write((sql) => data.queue_deliveries(sql, now));
      const identity = database.read((sql) => data.read_web_push_identity(sql));
      const deliveries = database.read((sql) => data.pending_deliveries(sql, now));
      for (const delivery of deliveries) {
        if (this.stop_requested) return 0;
        const result = await adapter.send(delivery.subscription, delivery.intent, identity, {
          subject: this.options.canonical_origin || 'https://localhost',
        });
        if (this.expired) return 0;
        database.write((sql) => data.record_delivery_result(sql, delivery, { ...result, now }));
      }
      if (this.stop_requested) return deliveries.length;
      const native = database.write((sql) => apns_data.pending_deliveries(sql, now));
      const origin = canonical_server_origin(this.options.canonical_origin);
      if (apns_adapter && origin) {
        for (const delivery of native) {
          if (this.stop_requested) return deliveries.length;
          const result = await apns_adapter.send(delivery, { server_url: origin, now });
          if (this.expired) return deliveries.length;
          database.write((sql) => apns_data.record_result(sql, delivery, result, now));
        }
        return deliveries.length + native.length;
      }
      return deliveries.length;
    } finally {
      admission?.release();
    }
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
  stop(options: Partial<StopOptions> = {}): Promise<void> {
    if (this.closing) return this.closing;
    this.stop_requested = true;
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    const deadline = options.deadline ?? performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS;
    const sends = Promise.allSettled([...this.pending, ...(this.active ? [this.active] : [])]);
    this.shutdown_settlement = Promise.resolve().then(async () => {
      await sends;
      // A live send still owns the adapter. Close only after every send actually ended.
      await this.options.apns_adapter?.close();
    });
    this.closing = observe_shutdown(
      this.shutdown_settlement,
      { deadline },
      'notifications',
    ).finally(() => {
      // A late transport result remains owned, but cannot use the database again.
      this.expired = true;
    });
    return this.closing;
  }
}
