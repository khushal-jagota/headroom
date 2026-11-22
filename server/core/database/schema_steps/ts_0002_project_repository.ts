/** Step 2: projects store their Git repository URL and default branch directly. */
import type { SchemaStep } from './contracts.ts';

export const project_repository_step: SchemaStep = {
  revision: 'ts_0002_project_repository',
  previous_revision: 'ts_0001_open_backend_keys',
  apply(tx) {
    tx.run('ALTER TABLE projects ADD COLUMN repository_url TEXT');
    tx.run('ALTER TABLE projects ADD COLUMN default_branch TEXT');
  },
};
