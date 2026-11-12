import { JsonNumber, numeric_kind, numeric_value } from '../../core/http/numeric_json.ts';
import type { Definitions, FormulaExpression, Measure } from '../contracts.ts';
import { calendar_date, invalid, timezone } from './dates.ts';
import {
  FORMULA_PARSE_DEPTH_LIMIT,
  EXPRESSION_EDGE_WEIGHT,
  FORMULA_GRAPH_DEPTH_LIMIT,
  FORMULA_NUMBER_PARSE_WEIGHT,
  FORMULA_DATE_PARSE_WEIGHT,
  FORMULA_PERIOD_PARSE_WEIGHT,
} from '../configuration.ts';
export function number(value: unknown): number {
  const result = numeric_value(value);
  if (result === undefined || !Number.isFinite(result))
    throw invalid('measure value must be a finite number');
  return result;
}
export function float_number(value: unknown): JsonNumber {
  const result = number(value);
  return new JsonNumber(
    Object.is(result, -0)
      ? '-0.0'
      : Number.isInteger(result)
        ? result.toExponential()
        : String(result),
  );
}
const binary = ['add', 'subtract', 'multiply', 'divide', 'power', 'min', 'max'];
export function parse_formula(raw: unknown, entry_depth = 0): FormulaExpression {
  const require_depth = (depth: number): void => {
    if (depth > FORMULA_PARSE_DEPTH_LIMIT)
      throw invalid('measure expression exceeds interpreter nesting limit');
  };
  const parse = (raw: unknown, depth: number): FormulaExpression => {
    require_depth(depth);
    if (raw === null || typeof raw !== 'object' || Array.isArray(raw))
      throw invalid('invalid measure expression');
    const v = raw as Record<string, unknown>;
    const keys = (...k: string[]) => Object.keys(v).sort().join('|') === k.sort().join('|');
    const op = v.op;
    if (op === 'constant' && keys('op', 'value')) {
      require_depth(depth + FORMULA_NUMBER_PARSE_WEIGHT);
      return { op, value: float_number(v.value) };
    }
    if (op === 'elapsed_days' && keys('op', 'start')) {
      require_depth(depth + FORMULA_DATE_PARSE_WEIGHT);
      calendar_date(v.start);
      return { op, start: v.start as string };
    }
    if (op === 'if_before' && keys('op', 'date', 'before', 'otherwise')) {
      require_depth(depth + FORMULA_DATE_PARSE_WEIGHT);
      calendar_date(v.date);
      return {
        op,
        date: v.date as string,
        before: parse(v.before, depth + EXPRESSION_EDGE_WEIGHT),
        otherwise: parse(v.otherwise, depth + EXPRESSION_EDGE_WEIGHT),
      };
    }
    if (
      (op === 'reference' || op === 'reference_at' || op === 'sum_daily') &&
      keys(
        'op',
        'measure_id',
        op === 'reference' ? 'day_offset' : op === 'reference_at' ? 'date' : 'period',
      )
    ) {
      if (op === 'reference' && numeric_kind(v.day_offset) !== 'integer')
        throw invalid('measure offset must be an integer');
      if (op === 'reference_at') {
        require_depth(depth + FORMULA_DATE_PARSE_WEIGHT);
        calendar_date(v.date);
      }
      if (op === 'sum_daily') require_depth(depth + FORMULA_PERIOD_PARSE_WEIGHT);
      if (
        op === 'sum_daily' &&
        (typeof v.period !== 'string' || !['day', 'week', 'month'].includes(v.period))
      )
        throw invalid('invalid sum period');
      if (typeof v.measure_id !== 'string' || !v.measure_id) throw invalid('invalid input measure');
      if (op === 'reference')
        return { op, measure_id: v.measure_id, day_offset: v.day_offset as number | JsonNumber };
      if (op === 'reference_at') return { op, measure_id: v.measure_id, date: v.date as string };
      return { op, measure_id: v.measure_id, period: v.period as 'day' | 'week' | 'month' };
    }
    if (typeof op === 'string' && binary.includes(op) && keys('op', 'left', 'right'))
      return {
        op: op as 'add',
        left: parse(v.left, depth + EXPRESSION_EDGE_WEIGHT),
        right: parse(v.right, depth + EXPRESSION_EDGE_WEIGHT),
      };
    throw invalid('invalid measure expression');
  };
  return parse(raw, entry_depth);
}
export function dependencies(formula: FormulaExpression): Set<string> {
  const found = new Set<string>();
  const pending = [formula];
  while (pending.length) {
    const n = pending.pop()!;
    if ('measure_id' in n) found.add(n.measure_id);
    else if (n.op === 'if_before') pending.push(n.before, n.otherwise);
    else if ('left' in n) pending.push(n.left, n.right);
  }
  return found;
}
export function validate_definition(m: Measure, definitions: Definitions, entry_depth = 0): void {
  timezone(m.timezone);
  if (m.origin === 'taken') {
    if (m.source_id === null || m.formula !== null)
      throw invalid('taken measure requires a source and no formula');
    return;
  }
  if (m.origin === 'manual') {
    if (m.source_id !== null || m.formula !== null)
      throw invalid('manual measure requires no source or formula');
    return;
  }
  if (m.source_id !== null || m.formula === null)
    throw invalid('assembled measure requires a formula and no source');
  for (const id of dependencies(parse_formula(m.formula, entry_depth)))
    if (!definitions.has(id)) throw invalid('input measure does not exist');
}
export function validate_graph(root: string, definitions: Definitions): void {
  const visited = new Set<string>(),
    active = new Set<string>();
  const visit = (id: string, depth: number) => {
    if (active.has(id)) throw invalid('measure dependency cycle');
    if (visited.has(id)) return;
    if (depth > FORMULA_GRAPH_DEPTH_LIMIT)
      throw invalid('measure dependency graph exceeds interpreter nesting limit');
    const m = definitions.get(id);
    if (!m) throw invalid('input measure does not exist');
    visited.add(id);
    active.add(id);
    validate_definition(m, definitions, depth);
    if (m.formula) for (const child of dependencies(m.formula)) visit(child, depth + 1);
    active.delete(id);
  };
  visit(root, 0);
}
