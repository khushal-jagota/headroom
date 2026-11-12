import type { Definitions, Measure, MeasureDate, MeasureValue } from '../../measures/contracts.ts';
import { pair_key } from '../../measures/contracts.ts';
import { invalid } from '../../measures/logic/dates.ts';
import {
  ArithmeticDepthLimit,
  demanded_pairs,
  Evaluation,
} from '../../measures/logic/evaluation.ts';
export function check_goal_demand_count(count: number, demand_limit: number): void {
  if (count > demand_limit)
    throw invalid(
      `goal projection exceeds ${demand_limit} demanded measure/date pairs; request a narrower date range`,
    );
}
export function goal_demanded_pairs(
  roots: MeasureDate[],
  definitions: Definitions,
  now: number,
  demand_limit: number,
): Map<string, MeasureDate> {
  try {
    return demanded_pairs(roots, definitions, now, demand_limit, true);
  } catch (e) {
    if (!(e instanceof ArithmeticDepthLimit)) throw e;
    const result = new Map<string, MeasureDate>();
    for (const root of roots) {
      try {
        for (const [key, pair] of demanded_pairs([root], definitions, now, demand_limit, true))
          result.set(key, pair);
      } catch (e) {
        if (!(e instanceof ArithmeticDepthLimit)) throw e;
      }
      check_goal_demand_count(result.size, demand_limit);
    }
    return result;
  }
}
export function goal_actual_value(
  definition: Measure,
  pair: MeasureDate,
  evaluation: Evaluation,
  today: string,
  has_retained_evidence: boolean,
): MeasureValue {
  if (pair.date > today && !has_retained_evidence)
    return {
      ...pair,
      definition_revision: definition.definition_revision,
      status: 'missing_input',
      value: null,
      inputs: [],
      missing_inputs: [pair],
      query_start_at: null,
      query_end_at: null,
      limitation: null,
      coverage_status: 'not_applicable',
      coverage_reasons: [],
    };
  return evaluation.measure(pair);
}
