/** Explicitly composed single-owner notification delivery. No constructor starts it. */
import type { Database } from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import type { WorkspaceAdmission } from '../conversation/workspace_admission.ts';
import type { APNsAdapter, WebPushAdapter } from './contracts.ts';
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
  private wake_pending = false;
  constructor(options: NotificationLoopOptions) {
    this.options = options;
  }
  reconcile_imported_state(): void {
    this.options.database.write((sql) => data.reconcile_attention(sql));
  }
  async poll_once(): Promise<number> {
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
        const result = await adapter.send(delivery.subscription, delivery.intent, identity, {
          subject: this.options.canonical_origin || 'https://localhost',
        });
        database.write((sql) => data.record_delivery_result(sql, delivery, { ...result, now }));
      }
      const native = database.write((sql) => apns_data.pending_deliveries(sql, now));
      const origin = canonical_server_origin(this.options.canonical_origin);
      if (apns_adapter && origin) {
        for (const delivery of native) {
          const result = await apns_adapter.send(delivery, { server_url: origin, now });
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
    if (this.timer || this.active) throw new Error('notification loop already started');
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
    this.active = this.poll_once();
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
  async stop(): Promise<void> {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    await this.active?.catch(() => {});
    await this.options.apns_adapter?.close();
  }
}
