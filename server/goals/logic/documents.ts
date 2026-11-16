/** Full chart validation extends the shared page-kit preparer without flattening numbers. */
import {
  is_json_record as object,
  numeric_kind,
  numeric_value,
} from '../../core/http/numeric_json.ts';
import { prepare_plain_document } from '../../managers/logic/documents.ts';
import { validate_iso_calendar_date } from '../calendar.ts';
import { dependencies, parse_formula } from '../../measures/logic/formulas.ts';
import type { FormulaExpression } from '../../measures/contracts.ts';
import type { ChartBlock, PageDocument, Block } from '../contracts.ts';
import { validate_category_chart } from './categories.ts';
import { numeric_projection, text_order } from '../category_contracts.ts';
function fail(message: string): never {
  throw new TypeError(message);
}
type Schema = string | { [key: string]: Schema } | readonly Schema[] | { union: readonly Schema[] };
const point: Schema = { label: 'string', value: { union: ['number', 'null'] }, 'axis?': 'string' };
const series: Schema = {
  'key?': 'string',
  'color?': 'blue|blue-muted|blue-dark',
  name: 'string',
  points: [point],
};
const metric: Schema = {
  union: [{ field: 'string' }, { numerator: 'string', denominator: 'string', 'scale?': 'number' }],
};
const binding: Schema = {
  union: [
    {
      kind: '=dataset',
      dataset_id: 'string',
      group_by: 'string',
      filters: [{ dimension: 'string', codes: ['string'] }],
      aggregation: 'sum|latest',
      metric,
    },
    {
      kind: '=scalar_inputs',
      inputs: [{ key: 'string', label: 'string', measure_id: 'string', 'role?': 'spent|planned' }],
      aggregation: 'sum|latest',
    },
  ],
};
const chart_schema: Schema = {
  type: '=chart',
  id: 'string',
  kind: 'line|bar|gauge|number|binary|progress|time-budget|allocation',
  placement: 'primary|supporting',
  'title?': 'string',
  'caption?': 'string',
  'period?': 'string',
  'unit?': 'string',
  'measure_id?': 'string',
  'binding?': binding,
  'target?': 'number',
  'domain?': ['pair-number'],
  'direction?': 'atleast|atmost',
  'reference?': [{ date: 'string', value: 'number' }],
  'expectation?': 'formula',
  'format?': { 'prefix?': 'string', 'suffix?': 'string', 'decimals?': 'int' },
  'series?': [series],
  'ranges?': [
    {
      'target?': 'number',
      'domain?': ['pair-number'],
      id: 'string',
      label: 'string',
      series: [series],
      'caption?': 'string',
      'period?': 'string',
    },
  ],
  'stacked?': 'boolean',
  'aggregate?': '=sum',
  'below_target_tone?': 'attention|bad',
  'target_label?': 'target|floor|limit',
  'difference_unit?': '=pp',
  'allocation_mode?': 'value|percentage',
  'total_label?': 'string',
  'plot_height?': 'int',
  'scale_label?': 'string',
  'shortfall_label?': 'below target|to target',
  'comparison_decimals?': 'int',
  'within_limit_label?': 'under limit|remaining',
  'emphasis?': '=hero',
  'manual?': {
    model: 'daily_quota|cumulative_target|individual_readings',
    'increments?': ['number'],
  },
};
function validate(value: unknown, schema: Schema, path: string): void {
  if (Array.isArray(schema)) {
    if (schema[0] === 'pair-number') {
      if (!Array.isArray(value) || value.length !== 2) fail(`${path}: expected a pair of numbers`);
      value.forEach((item, index) => validate(item, 'number', `${path}[${index}]`));
    } else {
      if (!Array.isArray(value)) fail(`${path}: expected a list`);
      value.forEach((item, index) => validate(item, schema[0]!, `${path}[${index}]`));
    }
    return;
  }
  if (typeof schema !== 'string') {
    if ('union' in schema && Array.isArray(schema.union)) {
      let matching: unknown;
      for (const option of schema.union) {
        try {
          validate(value, option, path);
          return;
        } catch (error) {
          if (object(value) && typeof option === 'object' && !Array.isArray(option))
            for (const key of ['type', 'kind']) {
              const discriminator = (option as Record<string, Schema>)[key];
              if (
                typeof discriminator === 'string' &&
                discriminator.startsWith('=') &&
                value[key] === discriminator.slice(1)
              )
                matching = error;
            }
        }
      }
      if (matching) throw matching;
      fail(`${path}: invalid value or block shape`);
    }
    if (!object(value)) fail(`${path}: expected an object`);
    const fields = schema as Record<string, Schema>,
      allowed = Object.keys(fields).map((key) => key.replace(/\?$/, ''));
    const unknown = Object.keys(value)
      .filter((key) => !allowed.includes(key))
      .sort(text_order)[0];
    if (unknown !== undefined)
      fail(
        /^[A-Za-z_][A-Za-z0-9_]{0,63}$/.test(unknown)
          ? `${path}.${unknown}: unknown field`
          : `${path}: unknown field`,
      );
    for (const [raw, kind] of Object.entries(fields)) {
      const key = raw.replace(/\?$/, '');
      if (!(key in value)) {
        if (raw.endsWith('?')) continue;
        fail(`${path}.${key}: required`);
      }
      validate(value[key], kind, `${path}.${key}`);
    }
    return;
  }
  if (schema === 'formula') {
    parse_formula(value);
    return;
  }
  if (schema === 'null') {
    if (value !== null) fail(`${path}: unexpected value type`);
    return;
  }
  if (schema === 'number') {
    const n = numeric_value(value);
    if (n === undefined || !Number.isFinite(n)) fail(`${path}: expected a finite number`);
    return;
  }
  if (schema === 'int') {
    if (numeric_kind(value) !== 'integer') fail(`${path}: unexpected value type`);
    return;
  }
  if (schema.startsWith('=') || schema.includes('|')) {
    const options = schema.startsWith('=') ? [schema.slice(1)] : schema.split('|');
    if (!options.includes(value as string))
      fail(
        `${path}: unsupported value; supported values: ${options.map((option) => `'${option}'`).join(', ')}`,
      );
    return;
  }
  if (typeof value !== schema) fail(`${path}: unexpected value type`);
}
export function blocks(page: PageDocument): Block[] {
  const found: Block[] = [];
  const visit = (items: Block[]): void => {
    for (const block of items) {
      found.push(block);
      const body = block as unknown as { blocks?: Block[]; views?: { blocks: Block[] }[] };
      if (body.blocks) visit(body.blocks);
      for (const view of body.views ?? []) visit(view.blocks);
    }
  };
  visit(page.blocks);
  return found;
}
export function prepare_document(value: unknown, new_identity: () => string): PageDocument {
  return prepare_plain_document(value, new_identity, {
    shape: (chart, path) => validate(chart, chart_schema, path),
    semantics: (chart) => validate_chart(chart as unknown as ChartBlock),
  });
}
export function validate_chart(chart: ChartBlock, path = '$'): void {
  validate(chart, chart_schema, path);
  if ('binding' in chart) validate_category_chart(chart);
  if ('expectation' in chart) {
    if (
      !['line', 'number'].includes(chart.kind) ||
      !chart.measure_id?.trim() ||
      chart.stacked ||
      'aggregate' in chart ||
      'manual' in chart
    )
      fail(
        'expectations require a measure-bound, nonstacked line or number readout without aggregates or manual controls',
      );
    chart.expectation = parse_formula(chart.expectation);
  }
  const projected =
    numeric_projection<import('../../../web/src/lib/goals/contracts.ts').ChartBlock>(chart);
  if (projected.manual) {
    const model = projected.manual.model,
      kind = { daily_quota: 'bar', cumulative_target: 'gauge', individual_readings: 'line' }[model];
    if (projected.kind !== kind || !projected.measure_id?.trim())
      fail('manual controls require a measure binding and matching chart kind');
    if ((model === 'daily_quota' || model === 'cumulative_target') && !('target' in projected))
      fail('manual quota and cumulative controls require a target');
    if (model === 'cumulative_target' && projected.target! <= 0)
      fail('cumulative target geometry requires a positive target');
  }
  if (!chart.id.trim()) fail('chart ID must be nonempty');
  if ('measure_id' in chart && !chart.measure_id!.trim()) fail('measure ID must be nonempty');
  if ((projected.format?.decimals ?? 0) < 0) fail('decimal places must be nonnegative');
  if ((projected.comparison_decimals ?? 0) < 0)
    fail('comparison decimal places must be nonnegative');
  if (projected.domain && projected.domain[0] >= projected.domain[1])
    fail('chart domain minimum must be less than its maximum');
  if (projected.ranges?.some((group) => group.domain && group.domain[0] >= group.domain[1]))
    fail('chart range domain minimum must be less than its maximum');
  if ((projected.plot_height ?? 1) <= 0) fail('chart plot height must be positive');
  const live_budget = projected.kind === 'time-budget' && 'binding' in projected;
  if (
    (projected.kind === 'progress' || projected.kind === 'time-budget') &&
    !live_budget &&
    ((projected.target ?? 0) <= 0 ||
      projected.ranges?.some((group) => (group.target ?? projected.target!) <= 0))
  )
    fail('progress and time-budget charts need a positive target');
  if (['progress', 'time-budget', 'allocation'].includes(projected.kind)) {
    const series = [
      ...(projected.series ?? []),
      ...(projected.ranges ?? []).flatMap((group) => group.series),
    ];
    if (series.some((item) => item.points.some((point) => point.value !== null && point.value < 0)))
      fail('progress and ring readings must be nonnegative');
  }
  for (const point of projected.reference ?? [])
    validate_iso_calendar_date(point.date, 'reference dates must be ISO calendar dates');
}
export function charts(primary: ChartBlock | null | undefined, page: PageDocument): ChartBlock[] {
  return [
    ...(primary ? [primary] : []),
    ...blocks(page).filter((block): block is ChartBlock => block.type === 'chart'),
  ];
}
export function measure_ids(primary: ChartBlock | null | undefined, page: PageDocument): string[] {
  const ids = new Set<string>();
  for (const chart of charts(primary, page)) {
    if ('measure_id' in chart) ids.add(chart.measure_id!);
    if (chart.binding?.kind === 'scalar_inputs')
      for (const input of chart.binding.inputs) ids.add(input.measure_id);
  }
  return [...ids].sort(text_order);
}
export function dataset_ids(primary: ChartBlock | null | undefined, page: PageDocument): string[] {
  return [
    ...new Set(
      charts(primary, page).flatMap((chart) =>
        chart.binding?.kind === 'dataset' ? [chart.binding.dataset_id] : [],
      ),
    ),
  ].sort(text_order);
}
export function expectation_measure_ids(
  primary: ChartBlock | null | undefined,
  page: PageDocument,
): string[] {
  return [
    ...new Set(
      charts(primary, page).flatMap((chart) =>
        'expectation' in chart ? [...dependencies(chart.expectation as FormulaExpression)] : [],
      ),
    ),
  ].sort(text_order);
}
