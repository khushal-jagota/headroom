/** TypeScript schema steps: an ordered chain on top of the frozen Python baseline. */
import { open_backend_keys_step } from './ts_0001_open_backend_keys.ts';
import { project_repository_step } from './ts_0002_project_repository.ts';
import { ticket_worker_setups_step } from './ts_0003_ticket_worker_setups.ts';
import { drop_ideas_step } from './ts_0004_drop_ideas.ts';
import { client_versions_step } from './ts_0005_client_versions.ts';
import { machine_backend_state_step } from './ts_0007_machine_backend_state.ts';
import { server_link_step } from './ts_0006_server_link.ts';
import { conversation_machine_step } from './ts_0008_conversation_machine.ts';
import { work_moves_step } from './ts_0009_work_moves.ts';
import { answer_receipt_index_step } from './ts_0010_answer_receipt_index.ts';

/** One statement at a time, through the same statement allowlist as the write door. */
export interface SchemaTransaction {
  run(sql: string): void;
}

export interface SchemaStep {
  /** The new alembic_version value, e.g. 'ts_0001_open_backend_keys'. */
  readonly revision: string;
  /** Must equal the database's alembic_version when this step starts. */
  readonly previous_revision: string;
  /** Literal DDL only. Never derived at run time. */
  apply(transaction: SchemaTransaction): void;
}

export interface SchemaStepOutcome {
  readonly from_revision: string;
  readonly to_revision: string;
  readonly applied: readonly string[];
}

/** The release's chain, oldest first. */
export const SCHEMA_STEPS: readonly SchemaStep[] = [
  open_backend_keys_step,
  project_repository_step,
  ticket_worker_setups_step,
  drop_ideas_step,
  client_versions_step,
  server_link_step,
  machine_backend_state_step,
  conversation_machine_step,
  work_moves_step,
  answer_receipt_index_step,
];

/** The steps a database at `current` still needs to reach `head`. */
export function select_schema_steps(
  steps: readonly SchemaStep[],
  current: string,
  head: string,
): readonly SchemaStep[] {
  if (steps.length > 0 && steps.at(-1)!.revision !== head)
    throw new Error(`The schema step chain does not end at ${head}`);
  if (current === head) return [];
  const start = steps.findIndex((step) => step.previous_revision === current);
  if (start < 0)
    throw new Error(
      `The database's schema revision ${current} is not on this release's schema step chain`,
    );
  return steps.slice(start);
}

/** Revisions are unique, each step follows the one before, and the chain starts at the baseline. */
export function validate_schema_step_chain(
  steps: readonly SchemaStep[],
  baseline_revision: string,
): void {
  const seen = new Set<string>([baseline_revision]);
  let previous = baseline_revision;
  for (const step of steps) {
    if (step.previous_revision !== previous)
      throw new Error(
        `Schema step ${step.revision} follows ${step.previous_revision}, not ${previous}`,
      );
    if (seen.has(step.revision))
      throw new Error(`Schema step revision ${step.revision} is not unique`);
    seen.add(step.revision);
    previous = step.revision;
  }
}
