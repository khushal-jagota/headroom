/** Fresh application prerequisites, without runtime Signal installation or a copied database. */
import type { TransactionSql } from '../core/database/contracts.ts';
import { randomUUID } from 'node:crypto';
import { createProject } from '../projects/data/index.ts';
import { seed_packaged_skills } from '../skills/data.ts';
import { seed_shipped_worker_types, seed_chief_settings } from '../worker_types/shipped.ts';
import {
  initialize_notifications,
  seed_shipped_notification_preferences,
} from '../notifications/data.ts';
import { createNavigationRegistry } from '../navigation/data/registry.ts';
import { prune } from '../navigation/delivery/retention.ts';

export function bootstrap_application(tx: TransactionSql, initialized_at: number): void {
  const epoch = randomUUID();
  tx.run('INSERT INTO chief_page(singleton) VALUES(1)');
  tx.run('INSERT INTO navigation_meta VALUES(1,?,7,?,0)', [randomUUID(), epoch]);
  tx.run('INSERT INTO navigation_delivery_retention VALUES(1,?,0)', [epoch]);
  tx.run("INSERT INTO manager_launch_defaults VALUES(1,'codex','gpt-6.1-sol','medium')");
  tx.run('INSERT INTO attention_history_metadata VALUES(1,?)', [initialized_at]);
  seed_shipped_notification_preferences(tx);
  const navigation = createNavigationRegistry();
  navigation.rebuild(tx);
  for (const name of ['Vylo', 'Tribe', 'Other', 'Personal'])
    createProject(tx, { name, summary: '' }, 0);
  navigation.finalize(tx);
  seed_packaged_skills(tx);
  seed_shipped_worker_types(tx);
  seed_chief_settings(tx);
  initialize_notifications(tx, 0);
  navigation.finalize(tx);
  prune(tx);
}
