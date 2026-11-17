/**
 * Test-only installer process. It runs the real install() with scripted effects up to one
 * point, says so in `<root>/stopped`, and blocks there until the parent SIGKILLs it. No
 * handler, catch or finally runs after that point.
 */
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { applySchemaSteps } from '../../../../core/database/index.ts';
import type { SchemaStep } from '../../../../core/database/schema_steps/contracts.ts';
import {
  BASELINE_SEED,
  probe_step,
  rebuild_step,
} from '../../../../core/database/schema_steps/testing/steps.ts';
import { install } from '../install.ts';

export const KILLED_FROM = 'a'.repeat(40),
  KILLED_TO = 'b'.repeat(40),
  KILLED_ATTEMPT = 'killed-install';

function stopped(root: string, point: string): never {
  writeFileSync(join(root, 'stopped'), point);
  // Bounded, so an orphan of a dead test run still ends.
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 60_000);
  throw new Error(`nobody killed the installer at ${point}`);
}
/** Each point's chain, the last step's revision its head. */
export function killed_install_steps(point: string, root = ''): readonly SchemaStep[] {
  const open = (step: SchemaStep): SchemaStep => ({
    ...step,
    apply(transaction) {
      step.apply(transaction);
      stopped(root, point);
    },
  });
  if (point === 'first_step_open') return [open(probe_step)];
  if (point === 'second_step_open') return [probe_step, open(rebuild_step)];
  return [probe_step];
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [root, point] = process.argv.slice(2) as [string, string];
  const data = join(root, 'data'),
    database = join(data, 'planner.db');
  await install(
    {
      current_root: root,
      database,
      data_directory: data,
      attempt_id: KILLED_ATTEMPT,
      node_executable: process.execPath,
      service_unit: 'never-start.service',
      health_url: 'http://127.0.0.1:0',
      health_timeout_seconds: 1,
      admitted_legacy_null_sha: null,
    },
    {
      async service() {},
      async capture() {
        if (point === 'before_swap') stopped(root, point);
        return { kind: 'returned', returncode: 0 };
      },
      async schema_steps(_descriptor, backup_taken) {
        const steps = killed_install_steps(point, root);
        applySchemaSteps(database, steps, {
          head_revision: steps.at(-1)!.revision,
          expected_statements: null,
          refuse_pending: !backup_taken,
          baseline: BASELINE_SEED,
        });
        stopped(root, point);
      },
      async restore() {
        throw new Error('a killed installer restores nothing');
      },
      async healthy() {
        throw new Error('a killed installer checks no health');
      },
    },
    { stderr() {} },
    new AbortController().signal,
  );
  throw new Error(`the installer passed ${point}`);
}
