import type { ReadSql } from '../core/database/contracts.ts';
import { read_definitions, read_demanded } from '../measures/data.ts';
import type { MeasureDate } from '../measures/contracts.ts';
import { pair_key } from '../measures/contracts.ts';
import { calendar_date, date_range, invalid } from '../measures/logic/dates.ts';
import {
  ArithmeticDepthLimit,
  demanded_pairs,
  Evaluation,
  expression_references,
} from '../measures/logic/evaluation.ts';
import { dependencies, parse_formula, validate_graph } from '../measures/logic/formulas.ts';
import type { GoalCompletionRule } from './contracts.ts';
import * as limits from './configuration.ts';
import { server_date } from './calendar.ts';
import { rule_satisfied } from './logic/rule_evaluation.ts';
export function parse_completion_rule(sql: ReadSql, raw: unknown): GoalCompletionRule {
  if (
    !raw ||
    typeof raw !== 'object' ||
    Array.isArray(raw) ||
    Object.keys(raw).sort().join('|') !==
      ['actual', 'target', 'direction', 'first_date'].sort().join('|')
  )
    throw invalid('completion_rule requires actual, target, direction, and first_date');
  const v = raw as Record<string, unknown>;
  if (v.direction !== 'atleast' && v.direction !== 'atmost')
    throw invalid('completion_rule direction must be atleast or atmost');
  calendar_date(v.first_date);
  const actual = parse_formula(v.actual),
    target = parse_formula(v.target),
    actual_ids = dependencies(actual);
  if (!actual_ids.size)
    throw invalid('completion_rule actual requires a measure with retained evidence');
  const ids = new Set([...actual_ids, ...dependencies(target)]),
    definitions = read_definitions(sql, [...ids]);
  for (const id of ids) validate_graph(id, definitions);
  return { actual, target, direction: v.direction, first_date: v.first_date as string };
}
export function evaluate_completion_date(
  sql: ReadSql,
  rule: GoalCompletionRule,
  now: number,
): string | null {
  const first = calendar_date(rule.first_date),
    last = calendar_date(server_date(now));
  if (first > last) return null;
  const length = (last.getTime() - first.getTime()) / 86400000 + 1;
  if (length > limits.MAX_PROJECTED_CHART_DATES)
    throw invalid('completion evaluation exceeds the goal date budget; choose a later first_date');
  const ids = new Set([...dependencies(rule.actual), ...dependencies(rule.target)]),
    definitions = read_definitions(sql, [...ids], null, true),
    seen = new Set<string>();
  const all_days = date_range(rule.first_date, server_date(now));
  for (let start = 0; start < length; start += limits.DEFAULT_PROJECTION_DATES) {
    const days = all_days.slice(start, start + limits.DEFAULT_PROJECTION_DATES),
      pairs = new Map<string, MeasureDate>();
    let demanded: Map<string, MeasureDate>;
    try {
      for (const day of days)
        for (const expr of [rule.actual, rule.target]) {
          for (const pair of expression_references(expr, day, 'UTC', now))
            pairs.set(pair_key(pair), pair);
          if (pairs.size > limits.MAX_DEMANDED_MEASURE_DATES)
            throw invalid('completion evaluation exceeds the goal measure/date budget');
        }
      demanded = demanded_pairs(
        [...pairs.values()],
        definitions,
        now,
        limits.MAX_DEMANDED_MEASURE_DATES,
        true,
      );
    } catch (e) {
      if (e instanceof ArithmeticDepthLimit)
        throw invalid('completion evaluation exceeds the interpreter nesting limit');
      throw e;
    }
    for (const key of demanded.keys()) seen.add(key);
    if (seen.size > limits.MAX_DEMANDED_MEASURE_DATES)
      throw invalid('completion evaluation exceeds the goal measure/date budget');
    const evaluation = new Evaluation(definitions, read_demanded(sql, demanded, definitions), now);
    for (const day of days) {
      const actual = evaluation.expression(rule.actual, day, 'UTC'),
        target = evaluation.expression(rule.target, day, 'UTC');
      if (actual.status === 'arithmetic_limit' || target.status === 'arithmetic_limit')
        throw invalid('completion evaluation exceeds the interpreter nesting limit');
      if (rule_satisfied(rule, actual, target, day)) return day;
    }
  }
  return null;
}
