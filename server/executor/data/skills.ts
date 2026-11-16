/** Read canonical sources in the caller's database scope, without bootstrap writes. */
import { BackendSpawnFailed } from '../../conversation/backends/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { is_sqlite_error } from '../../core/database/index.ts';
import { ExecutorValidationError } from '../../executor/protocol_errors.ts';
import { read_all_skill_sources } from '../../skills/data.ts';
import { WORKER_ONLY_SKILL_NAMES } from '../../skills/native_publication.ts';
import { workflow_from_row } from '../../worker_types/store.ts';
import { validate_skill_snapshot } from '../skills.ts';
import type { CanonicalSkillSnapshot } from '../wire.generated.ts';
/** Read the canonical rows in the caller's read or transaction scope, without seeding. */
export function read_canonical_skill_snapshot(
  sql: ReadSql,
  ticket_id: string,
): CanonicalSkillSnapshot {
  try {
    const row = sql.get<{ workflow_json: string }>(
      'SELECT workflow_json FROM tickets WHERE id = ?',
      [ticket_id],
    );
    if (row === undefined) throw new ExecutorValidationError('assigned Ticket does not exist');
    const definition = workflow_from_row(row);
    const sources = read_all_skill_sources(sql);
    const required_skill_names = [
      ...new Set([
        'panels',
        'panels-worker',
        ...(definition.specialist_skill !== null &&
        Object.hasOwn(sources, definition.specialist_skill)
          ? [definition.specialist_skill]
          : []),
      ]),
    ];
    const snapshot: CanonicalSkillSnapshot = {
      sources: Object.entries(sources)
        .filter(
          ([name]) => !WORKER_ONLY_SKILL_NAMES.has(name) || required_skill_names.includes(name),
        )
        .map(([skill_name, source_text]) => ({ skill_name, source_text })),
      required_skill_names,
    };
    validate_skill_snapshot(snapshot);
    return snapshot;
  } catch (error) {
    if (
      is_sqlite_error(error) ||
      error instanceof ExecutorValidationError ||
      error instanceof PlannerError
    )
      throw new BackendSpawnFailed(
        `cannot read canonical skill snapshot: ${error instanceof Error ? error.message : String(error)}`,
      );
    throw error;
  }
}
