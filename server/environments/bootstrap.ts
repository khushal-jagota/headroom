/** Fresh application prerequisites, without runtime Signal installation or a copied database. */
import type { TransactionSql } from '../core/database/contracts.ts';
import { randomUUID } from 'node:crypto';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { seed_packaged_skills } from '../skills/data.ts';
import { seed_shipped_worker_types, seed_chief_settings } from '../worker_types/shipped.ts';
import {
  initialize_notifications,
  seed_shipped_notification_preferences,
} from '../notifications/data.ts';
import { createNavigationRegistry } from '../navigation/data/registry.ts';
import { prune } from '../navigation/delivery/retention.ts';
import { DEFAULT_LAUNCH } from '../drivers/built_in.ts';

export function seed_home_machine_row(tx: TransactionSql): void {
  tx.run(
    "INSERT INTO machines(machine_id,name,icon,color,is_home) VALUES(?, 'Home','server','jade',1)",
    [HOME_MACHINE_ID],
  );
}

export function bootstrap_application(
  tx: TransactionSql,
  initialized_at: number,
  owner_time_zone?: string,
): void {
  const epoch = randomUUID();
  seed_home_machine_row(tx);
  tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
  tx.run(
    'INSERT INTO navigation_meta(singleton,scope,schema_version,epoch,revision) VALUES(1,?,7,?,0)',
    [randomUUID(), epoch],
  );
  tx.run(
    'INSERT INTO navigation_delivery_retention(singleton,epoch,floor_revision) VALUES(1,?,0)',
    [epoch],
  );
  tx.run(
    'INSERT INTO manager_launch_defaults(singleton,employee_backend,employee_launch_model,employee_launch_reasoning_effort) VALUES(1,?,?,?)',
    [
      DEFAULT_LAUNCH.employee_backend,
      DEFAULT_LAUNCH.employee_launch_model,
      DEFAULT_LAUNCH.employee_launch_reasoning_effort,
    ],
  );
  tx.run(
    'INSERT INTO attention_history_metadata(singleton,started_at,owner_time_zone) VALUES(1,?,?)',
    [initialized_at, owner_time_zone ?? null],
  );
  seed_shipped_notification_preferences(tx);
  const navigation = createNavigationRegistry();
  navigation.rebuild(tx);
  seed_packaged_skills(tx);
  seed_shipped_worker_types(tx);
  seed_chief_settings(tx);
  initialize_notifications(tx, 0);
  navigation.finalize(tx);
  prune(tx);
}
