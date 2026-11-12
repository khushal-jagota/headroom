import type { ReadSql } from '../core/database/contracts.ts';
import type { Definitions, MeasureDate } from '../measures/contracts.ts';
import { read_demanded } from '../measures/data.ts';
import { Evaluation } from '../measures/logic/evaluation.ts';
import { goal_demanded_pairs } from './logic/reading_assembly.ts';
export function read_goal_evaluation(
  sql: ReadSql,
  roots: MeasureDate[],
  definitions: Definitions,
  now: number,
  demand_limit: number,
): Evaluation {
  const pairs = goal_demanded_pairs(roots, definitions, now, demand_limit);
  return new Evaluation(definitions, read_demanded(sql, pairs, definitions), now);
}
