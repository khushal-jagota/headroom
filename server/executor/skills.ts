/** Canonical snapshot admission shared by the VPS sender and Mac installation. */
import { BackendSpawnFailed } from '../conversation/backends/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import { is_sqlite_error } from '../core/database/index.ts';
import { ValueError } from '../core/value_error.ts';
import { read_all_skill_sources } from '../skills/data.ts';
import { skill_description } from '../skills/logic.ts';
import {
  RETIRED_PANELS_SKILL_NAMES,
  WORKER_ONLY_SKILL_NAMES,
} from '../skills/native_publication.ts';
import { workflow_from_row } from '../worker_types/store.ts';
import type { CanonicalSkillSnapshot } from './wire.generated.ts';
function invalid(message: string): never {
  throw new Error(message);
}

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
    if (row === undefined) throw new ValueError('assigned Ticket does not exist');
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
    if (is_sqlite_error(error) || error instanceof ValueError || error instanceof PlannerError)
      throw new BackendSpawnFailed(
        `cannot read canonical skill snapshot: ${error instanceof Error ? error.message : String(error)}`,
      );
    throw error;
  }
}
export function validate_skill_snapshot(snapshot: CanonicalSkillSnapshot): void {
  try {
    const names = new Set<string>(),
      folded = new Set<string>();
    for (const source of snapshot.sources) {
      const name = source.skill_name;
      if (!/^[A-Za-z0-9][A-Za-z0-9_-]*(?![\s\S])/u.test(name)) invalid('unsafe skill name');
      if (folded.has(name.toLowerCase())) invalid('duplicate skill name');
      names.add(name);
      folded.add(name.toLowerCase());
      if (/\p{Surrogate}/u.test(source.source_text)) {
        const points = [...source.source_text],
          index = points.findIndex((point) => /\p{Surrogate}/u.test(point));
        let end = index + 1;
        while (end < points.length && /\p{Surrogate}/u.test(points[end]!)) end++;
        const position =
          end === index + 1
            ? `character '\\u${points[index]!.charCodeAt(0).toString(16).padStart(4, '0')}' in position ${index}`
            : `characters in position ${index}-${end - 1}`;
        invalid(`'utf-8' codec can't encode ${position}: surrogates not allowed`);
      }
      skill_description(source.source_text, name);
    }
    const required = snapshot.required_skill_names;
    if (new Set(required).size !== required.length) invalid('duplicate required skill name');
    if (!required.includes('panels') || !required.includes('panels-worker'))
      invalid('base Worker skills are not required');
    if (required.some((name) => RETIRED_PANELS_SKILL_NAMES.has(name)))
      invalid('required canonical skill is retired');
    if (required.some((name) => !names.has(name))) invalid('required canonical skill is absent');
  } catch (error) {
    throw new BackendSpawnFailed(
      `invalid canonical skill snapshot: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
}
