/** Readonly candidate schema decisions, without an installer or migration writer. */
import { createReadAccess, is_sqlite_error } from '../../core/database/index.ts';
import { AppValidationError } from '../release/contracts.ts';
import { node_schema_steps_pending, read_node_schema_frontier } from '../release/migrations.ts';
import type { InstallerOperations } from './contracts.ts';

export function create_migration_status(): Pick<InstallerOperations, 'node_schema_steps_pending'> {
  return {
    async node_schema_steps_pending(database, staged_app) {
      const frontier = await read_node_schema_frontier(staged_app);
      try {
        const reader = createReadAccess(database);
        try {
          return reader.read((sql) => node_schema_steps_pending(sql, frontier));
        } finally {
          reader.close();
        }
      } catch (error) {
        if (is_sqlite_error(error)) throw new AppValidationError((error as Error).message);
        throw error;
      }
    },
  };
}
