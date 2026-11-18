/**
 * Step 2: projects store their Git repository URL and default branch directly.
 * Existing projects with GitHub repositories are backfilled when repository_url is NULL.
 */
import type { SchemaStep } from './contracts.ts';

export const project_repository_step: SchemaStep = {
  revision: 'ts_0002_project_repository',
  previous_revision: 'ts_0001_open_backend_keys',
  apply(tx) {
    tx.run('ALTER TABLE projects ADD COLUMN repository_url TEXT');
    tx.run('ALTER TABLE projects ADD COLUMN default_branch TEXT');
    tx.run(
      "UPDATE projects SET repository_url = 'https://github.com/khushal-jagota/panels.git', default_branch = 'staging' WHERE id = 'project_panels' AND repository_url IS NULL",
    );
    tx.run(
      "UPDATE projects SET repository_url = 'https://github.com/khushal-jagota/Wren.git', default_branch = 'main' WHERE id = 'project_wren' AND repository_url IS NULL",
    );
    tx.run(
      "UPDATE projects SET repository_url = 'https://github.com/khushal-jagota/rewire.git', default_branch = 'main' WHERE id = 'project_rewire' AND repository_url IS NULL",
    );
    tx.run(
      "UPDATE projects SET repository_url = 'https://github.com/khushal-jagota/vylo-convex.git', default_branch = 'main' WHERE id = 'project_vylo' AND repository_url IS NULL",
    );
  },
};
