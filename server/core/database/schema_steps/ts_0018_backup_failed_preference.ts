/** The owner can switch the Panels "Backup failed" alert. Keep every saved preference. */
import type { SchemaStep } from './contracts.ts';

export const backup_failed_preference_step: SchemaStep = {
  revision: 'ts_0018_backup_failed_preference',
  previous_revision: 'ts_0017_recipient_deliveries',
  apply(tx) {
    tx.run(
      "CREATE TABLE notification_preferences_next (subject_key TEXT NOT NULL CHECK (subject_key IN ('tickets','chief_of_staff','managers','goals','panels')),notification_type TEXT NOT NULL CHECK (notification_type IN ('awaiting_reply','awaiting_answer','awaiting_approval','assigned','errored','stall_alarm','backup_failed')),enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),updated_at INTEGER NOT NULL, PRIMARY KEY(subject_key, notification_type))",
    );
    tx.run(
      'INSERT INTO notification_preferences_next (subject_key,notification_type,enabled,updated_at) SELECT subject_key,notification_type,enabled,updated_at FROM notification_preferences',
    );
    tx.run('DROP TABLE notification_preferences');
    tx.run('ALTER TABLE notification_preferences_next RENAME TO notification_preferences');
  },
};
