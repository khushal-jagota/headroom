/** Installer-only entry, run from the new app after the swap and before it starts. */
import { applySchemaSteps } from '../../core/database/index.ts';
import { application_schema_seed } from '../../core/database/admission.ts';
import { PYTHON_BASELINE_SEED } from '../../core/database/schema_steps/baseline.ts';
import {
  SCHEMA_STEPS,
  validate_schema_step_chain,
} from '../../core/database/schema_steps/contracts.ts';
import { assert_test_database_path } from '../../core/test_paths.ts';
import { expand_path } from '../paths.ts';
const args = process.argv.slice(2);
if (args.length !== 2 || (args[1] !== 'backup' && args[1] !== 'no-backup')) {
  process.stderr.write('expected database and backup|no-backup\n');
  process.exitCode = 1;
} else {
  assert_test_database_path(args[0]!);
  const database = expand_path(args[0]!);
  assert_test_database_path(database);
  // CI checks both. A mis-built release must still never step a database it cannot start on.
  validate_schema_step_chain(SCHEMA_STEPS, PYTHON_BASELINE_SEED.alembicRevision);
  const head_revision = SCHEMA_STEPS.at(-1)?.revision ?? PYTHON_BASELINE_SEED.alembicRevision;
  if (head_revision !== application_schema_seed.alembicRevision)
    throw new Error(
      `The schema step chain ends at ${head_revision}, but this release's seed is at ${application_schema_seed.alembicRevision}`,
    );
  const outcome = applySchemaSteps(database, SCHEMA_STEPS, {
    head_revision,
    expected_statements: application_schema_seed.statements,
    refuse_pending: args[1] === 'no-backup',
    baseline: PYTHON_BASELINE_SEED,
  });
  process.stdout.write(JSON.stringify(outcome) + '\n');
}
