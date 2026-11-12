import { PlannerError } from '../../core/contracts.ts';
import type {
  Coverage,
  Definitions,
  ExpressionValue,
  FormulaExpression,
  InputObservation,
  MeasureDate,
  MeasureValue,
  Observations,
  Status,
} from '../contracts.ts';
import { pair_key } from '../contracts.ts';
import { combine_coverage } from './coverage.ts';
import { calendar_date, date_range, invalid, offset_date, period_interval } from './dates.ts';
import { number } from './formulas.ts';
import { python_sum } from './sum.ts';
// Provisional until the fresh Python boundary proof selects the final limit.
export const EVALUATION_DEPTH_LIMIT = 240;
export class ArithmeticDepthLimit extends Error {}
export function expression_references(
  n: FormulaExpression,
  day: string,
  zone: string,
  now: number,
): MeasureDate[] {
  if (n.op === 'constant' || n.op === 'elapsed_days') return [];
  if (n.op === 'reference')
    return [{ measure_id: n.measure_id, date: offset_date(day, n.day_offset) }];
  if (n.op === 'reference_at') return [{ measure_id: n.measure_id, date: n.date }];
  if (n.op === 'if_before')
    return expression_references(day < n.date ? n.before : n.otherwise, day, zone, now);
  if (n.op === 'sum_daily') {
    const interval = period_interval(day, n.period);
    return date_range(interval.start_date, offset_date(interval.end_date_exclusive, -1)).map(
      (date) => ({ measure_id: n.measure_id, date }),
    );
  }
  return [
    ...expression_references(n.left, day, zone, now),
    ...expression_references(n.right, day, zone, now),
  ];
}
export function demanded_pairs(
  roots: MeasureDate[],
  definitions: Definitions,
  now: number,
  limit: number | null = null,
  missing_ok = false,
): Map<string, MeasureDate> {
  const result = new Map<string, MeasureDate>();
  const visit = (pair: MeasureDate, depth: number) => {
    const key = pair_key(pair);
    if (result.has(key)) return;
    if (depth > EVALUATION_DEPTH_LIMIT) throw new ArithmeticDepthLimit();
    if (limit !== null && result.size >= limit)
      throw invalid(`measure date demand exceeds ${limit} pairs; request a narrower range`);
    result.set(key, pair);
    const measure = definitions.get(pair.measure_id);
    if (!measure && missing_ok) return;
    if (!measure) throw invalid('input measure does not exist');
    if (!measure.formula) return;
    let children: MeasureDate[];
    try {
      children = expression_references(measure.formula, pair.date, measure.timezone, now);
    } catch (e) {
      if (e instanceof PlannerError) return;
      throw e;
    }
    for (const child of children) visit(child, depth + 1);
  };
  for (const root of roots) visit(root, 0);
  return result;
}
export class Evaluation {
  readonly definitions: Definitions;
  readonly observations: Observations;
  readonly now: number;
  readonly cache = new Map<string, MeasureValue>();
  constructor(definitions: Definitions, observations: Observations, now: number) {
    this.definitions = definitions;
    this.observations = observations;
    this.now = now;
  }
  _unavailable(pair: MeasureDate, status: Status): MeasureValue {
    const m = this.definitions.get(pair.measure_id);
    return {
      ...pair,
      definition_revision: m?.definition_revision ?? 0,
      status,
      value: null,
      inputs: [],
      missing_inputs: status === 'missing_input' ? [pair] : [],
      query_start_at: null,
      query_end_at: null,
      limitation: null,
      coverage_status: m?.kind === 'period_count' ? 'unknown' : 'not_applicable',
      coverage_reasons: [],
    };
  }
  measure(pair: MeasureDate): MeasureValue {
    try {
      return this._measure(pair);
    } catch (e) {
      if (e instanceof ArithmeticDepthLimit) return this._unavailable(pair, 'arithmetic_limit');
      throw e;
    }
  }
  _measure(pair: MeasureDate, depth = 0): MeasureValue {
    if (depth > EVALUATION_DEPTH_LIMIT) throw new ArithmeticDepthLimit();
    const key = pair_key(pair),
      cached = this.cache.get(key);
    if (cached) return cached;
    const m = this.definitions.get(pair.measure_id);
    let answer: MeasureValue;
    if (!m) answer = this._unavailable(pair, 'missing_input');
    else if (m.origin !== 'assembled') {
      const o = this.observations.get(key);
      answer = !o
        ? this._unavailable(pair, 'missing_input')
        : {
            ...pair,
            definition_revision: m.definition_revision,
            status: 'value',
            value: o.value,
            inputs: [],
            missing_inputs: [],
            query_start_at: o.query_start_at,
            query_end_at: o.query_end_at,
            limitation: o.limitation,
            coverage_status:
              m.kind === 'period_count' && o.coverage_status === 'not_applicable'
                ? 'unknown'
                : o.coverage_status,
            coverage_reasons:
              m.kind === 'period_count' && o.coverage_status === 'not_applicable'
                ? ['Period coverage is unavailable for this observation.']
                : o.coverage_reasons,
          };
    } else {
      const e = this._expression(m.formula!, pair.date, m.timezone, depth);
      if (m.kind === 'period_count' && e.coverage_status === 'not_applicable') {
        e.coverage_status = 'unknown';
        e.coverage_reasons = ['The expression supplies no period coverage evidence.'];
      }
      answer = {
        ...pair,
        definition_revision: m.definition_revision,
        ...e,
        query_start_at: null,
        query_end_at: null,
        limitation: null,
      };
    }
    this.cache.set(key, answer);
    return answer;
  }
  expression(formula: FormulaExpression, day: string, zone: string): ExpressionValue {
    try {
      return this._expression(formula, day, zone, 0);
    } catch (e) {
      if (e instanceof ArithmeticDepthLimit)
        return {
          status: 'arithmetic_limit',
          value: null,
          inputs: [],
          missing_inputs: [],
          coverage_status: 'not_applicable',
          coverage_reasons: [],
        };
      throw e;
    }
  }
  _expression(
    formula: FormulaExpression,
    day: string,
    zone: string,
    depth: number,
  ): ExpressionValue {
    let status: Status = 'value';
    const inputs = new Map<string, InputObservation>(),
      missing = new Map<string, MeasureDate>(),
      coverage: [Coverage, string[]][] = [];
    const reference = (child: MeasureDate): number | null => {
      const key = pair_key(child),
        resolved = this._measure(child, depth + 1);
      coverage.push([resolved.coverage_status, resolved.coverage_reasons]);
      const stored = this.observations.get(key);
      if (this.definitions.has(child.measure_id)) {
        inputs.set(key, {
          ...child,
          definition_revision: resolved.definition_revision,
          value: resolved.value,
          run_id: stored?.run_id ?? null,
          manual_write_id: stored?.manual_write_id ?? null,
          query_start_at: resolved.query_start_at,
          query_end_at: resolved.query_end_at,
          limitation: resolved.limitation,
          coverage_status: resolved.coverage_status,
          coverage_reasons: resolved.coverage_reasons,
        });
        for (const v of resolved.inputs) if (!inputs.has(pair_key(v))) inputs.set(pair_key(v), v);
      }
      for (const v of resolved.missing_inputs) missing.set(pair_key(v), v);
      if (resolved.status !== 'value' && status === 'value') status = resolved.status;
      return resolved.value;
    };
    const expression = (n: FormulaExpression, nesting = 0): number | null => {
      if (nesting > EVALUATION_DEPTH_LIMIT) throw new ArithmeticDepthLimit();
      if (n.op === 'constant') return number(n.value);
      if (n.op === 'elapsed_days')
        return (calendar_date(day).getTime() - calendar_date(n.start).getTime()) / 86400000;
      if (n.op === 'reference')
        return reference({ measure_id: n.measure_id, date: offset_date(day, n.day_offset) });
      if (n.op === 'reference_at') return reference({ measure_id: n.measure_id, date: n.date });
      if (n.op === 'if_before')
        return expression(day < n.date ? n.before : n.otherwise, nesting + 1);
      if (n.op === 'sum_daily') {
        const values = expression_references(n, day, zone, this.now).map(reference);
        if (values.some((v) => v === null)) return null;
        return number(python_sum(values as number[]));
      }
      const l = expression(n.left, nesting + 1),
        r = expression(n.right, nesting + 1);
      if (l === null || r === null) return null;
      if (n.op === 'divide' && r === 0) {
        status = 'division_by_zero';
        return null;
      }
      const v =
        n.op === 'add'
          ? l + r
          : n.op === 'subtract'
            ? l - r
            : n.op === 'multiply'
              ? l * r
              : n.op === 'divide'
                ? l / r
                : n.op === 'power'
                  ? Math.pow(l, r)
                  : n.op === 'min'
                    ? Math.min(l, r)
                    : Math.max(l, r);
      return number(v);
    };
    let result: number | null;
    try {
      result = expression(formula);
      if (result !== null) result = number(result);
    } catch (e) {
      if (!(e instanceof PlannerError)) throw e;
      status =
        e.message.includes('period') || e.message.includes('date')
          ? 'invalid_period'
          : 'invalid_value';
      result = null;
    }
    const [coverage_status, coverage_reasons] = combine_coverage(coverage);
    return {
      status,
      value: status === 'value' ? result : null,
      inputs: [...inputs.values()],
      missing_inputs: [...missing.values()].sort((a, b) =>
        a.measure_id < b.measure_id
          ? -1
          : a.measure_id > b.measure_id
            ? 1
            : a.date.localeCompare(b.date),
      ),
      coverage_status,
      coverage_reasons,
    };
  }
}
export function evaluate(
  roots: MeasureDate[],
  definitions: Definitions,
  observations: Observations,
  now: number,
): MeasureValue[] {
  const context = new Evaluation(definitions, observations, now);
  try {
    return roots.map((pair) => context._measure(pair));
  } catch (e) {
    if (e instanceof ArithmeticDepthLimit)
      return roots.map((pair) => context._unavailable(pair, 'arithmetic_limit'));
    throw e;
  }
}
export function phase_changed(
  expression: FormulaExpression,
  day: string,
  definitions: Definitions = new Map(),
): boolean {
  calendar_date(day);
  if (day === '0001-01-01') return false;
  const signatures = new Map<string, number>(),
    cache = new Map<FormulaExpression, Map<string, number>>(),
    identities = new Map<FormulaExpression, number>();
  const id = (n: FormulaExpression) => {
    if (!identities.has(n)) identities.set(n, identities.size + 1);
    return identities.get(n)!;
  };
  const children = (n: FormulaExpression, at: string): [FormulaExpression, string][] => {
    if (n.op === 'if_before') return [[at < n.date ? n.before : n.otherwise, at]];
    if ('left' in n)
      return [
        [n.left, at],
        [n.right, at],
      ];
    if ('measure_id' in n) {
      const m = definitions.get(n.measure_id);
      if (!m?.formula) return [];
      try {
        return expression_references(n, at, m.timezone, 0).map((p) => [m.formula!, p.date]);
      } catch (e) {
        if (e instanceof PlannerError) return [];
        throw e;
      }
    }
    return [];
  };
  const trace = (at: string): number => {
    const active = new Set<FormulaExpression>();
    const pending: [FormulaExpression, string, boolean][] = [[expression, at, false]];
    while (pending.length) {
      const [n, d, expanded] = pending.pop()!;
      if (cache.get(n)?.has(d)) continue;
      if (!expanded) {
        if (active.has(n)) continue;
        active.add(n);
        pending.push([n, d, true]);
        for (const [c, cd] of children(n, d)) pending.push([c, cd, false]);
        continue;
      }
      active.delete(n);
      const resolved = children(n, d).map(([c, cd]) => cache.get(c)?.get(cd) ?? 0);
      let value: number;
      if (n.op !== 'if_before' && !resolved.some(Boolean)) value = 0;
      else if (n.op === 'reference' || n.op === 'reference_at') value = resolved[0]!;
      else {
        const signature = JSON.stringify(
          n.op === 'if_before'
            ? ['phase', id(n), d < n.date, ...resolved]
            : n.op === 'sum_daily'
              ? ['sum', [...new Set(resolved)].sort((a, b) => a - b)]
              : ['children', ...resolved],
        );
        if (!signatures.has(signature)) signatures.set(signature, signatures.size + 1);
        value = signatures.get(signature)!;
      }
      if (!cache.has(n)) cache.set(n, new Map());
      cache.get(n)!.set(d, value);
    }
    return cache.get(expression)!.get(at)!;
  };
  return trace(offset_date(day, -1)) !== trace(day);
}
