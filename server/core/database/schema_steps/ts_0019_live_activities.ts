/** One Review Live Activity per registered iPhone: its tokens and what the server last sent. */
import type { SchemaStep } from './contracts.ts';

export const live_activities_step: SchemaStep = {
  revision: 'ts_0019_live_activities',
  previous_revision: 'ts_0018_backup_failed_preference',
  apply(tx) {
    tx.run(
      'CREATE TABLE notification_live_activities (device_id TEXT PRIMARY KEY REFERENCES notification_apns_devices(device_id) ON DELETE CASCADE, push_to_start_token TEXT, update_token TEXT, started_at INTEGER, last_cards_json TEXT, updated_at INTEGER NOT NULL)',
    );
  },
};
