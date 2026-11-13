import { is_record } from '../../core/records.ts';
import { calendar_date, timezone } from '../../measures/logic/dates.ts';
import { PlannerError } from '../../core/contracts.ts';
import { stringify_python_json, numeric_value } from '../../core/http/numeric_json.ts';
import type {
  CandidateDatasetResult,
  Dataset,
  DatasetDimension,
  DatasetField,
  DatasetRow,
  OutputDataset,
} from '../contracts.ts';
function invalid(message: string): never {
  throw new PlannerError('validation', message);
}
function text(value: unknown, name: string, allow_empty = false): string {
  if (typeof value !== 'string' || (!allow_empty && !value.trim()))
    invalid(`invalid dataset ${name}`);
  return value;
}
function object(value: unknown, name: string): Record<string, unknown> {
  if (!is_record(value)) invalid(`invalid dataset ${name}`);
  return value;
}
function sequence(value: unknown, name: string): unknown[] {
  if (!Array.isArray(value)) invalid(`invalid dataset ${name}`);
  return value;
}
function exact(body: Record<string, unknown>, names: string[]): boolean {
  return Object.keys(body).length === names.length && names.every((key) => key in body);
}
function number(value: unknown): number {
  const result = numeric_value(value);
  if (result === undefined || !Number.isFinite(result))
    invalid('measure value must be a finite number');
  return result;
}
export function parse_dimensions(value: unknown): DatasetDimension[] {
  const dimensions = sequence(value, 'dimensions').map((raw) => {
    const body = object(raw, 'dimension');
    if (!exact(body, ['name', 'label'])) invalid('invalid dataset dimension declaration');
    return { name: text(body.name, 'dimension name'), label: text(body.label, 'dimension label') };
  });
  if (!dimensions.length || new Set(dimensions.map((item) => item.name)).size !== dimensions.length)
    invalid('dataset dimensions must be nonempty and unique');
  return dimensions;
}
export function parse_fields(value: unknown): DatasetField[] {
  const fields = sequence(value, 'fields').map((raw) => {
    const body = object(raw, 'field');
    if (!exact(body, ['name', 'label', 'unit', 'kind']))
      invalid('invalid dataset field declaration');
    const kind = text(body.kind, 'field kind');
    if (!['count', 'amount', 'snapshot'].includes(kind)) invalid('invalid dataset field kind');
    return {
      name: text(body.name, 'field name'),
      label: text(body.label, 'field label'),
      unit: text(body.unit, 'field unit', true),
      kind,
    } as DatasetField;
  });
  if (!fields.length || new Set(fields.map((item) => item.name)).size !== fields.length)
    invalid('dataset fields must be nonempty and unique');
  return fields;
}
export function validate_definition(dataset: Dataset): void {
  text(dataset.id, 'id');
  text(dataset.project_id, 'Project');
  text(dataset.source_id, 'source');
  text(dataset.name, 'name');
  timezone(dataset.timezone);
  parse_dimensions(dataset.dimensions);
  parse_fields(dataset.fields);
}
export function dimension_key(dimensions: Record<string, string>): string {
  const entries = Object.entries(dimensions).sort(([a], [b]) => {
    const left = Array.from(a, (character) => character.codePointAt(0)!);
    const right = Array.from(b, (character) => character.codePointAt(0)!);
    for (let index = 0; index < Math.min(left.length, right.length); index++) {
      if (left[index] !== right[index]) return left[index]! - right[index]!;
    }
    return left.length - right.length;
  });
  return stringify_python_json(entries, { compact: true, ensure_ascii: true });
}
function parse_row(value: unknown): DatasetRow {
  const body = object(value, 'row');
  if (!exact(body, ['dimensions', 'values'])) invalid('invalid dataset row');
  return {
    dimensions: Object.fromEntries(
      Object.entries(object(body.dimensions, 'row dimensions')).map(([name, code]) => [
        name,
        text(code, 'category code', true),
      ]),
    ),
    values: Object.fromEntries(
      Object.entries(object(body.values, 'row values')).map(([name, value]) => [
        name,
        number(value),
      ]),
    ),
  };
}
function labels(value: unknown): Record<string, Record<string, string>> {
  return Object.fromEntries(
    Object.entries(object(value, 'labels')).map(([dimension, items]) => [
      dimension,
      Object.fromEntries(
        Object.entries(object(items, 'category labels')).map(([code, label]) => [
          code,
          text(label, 'category label'),
        ]),
      ),
    ]),
  );
}
function optional(value: unknown, name: string): string | null {
  return value == null ? null : text(value, name);
}
function timestamp(value: unknown): number {
  const result = number(value);
  if (result < -62135596800 || result >= 253402300800)
    invalid('dataset query timestamp is outside the UTC datetime range');
  return result;
}
export function parse_result(value: unknown): CandidateDatasetResult {
  const body = object(value, 'result');
  const required = ['dataset_id', 'date', 'rows', 'query_end_at'];
  const allowed = [...required, 'query_start_at', 'labels', 'unavailable_reason', 'limitation'];
  if (
    !required.every((key) => key in body) ||
    Object.keys(body).some((key) => !allowed.includes(key))
  )
    invalid('invalid dataset result fields');
  const candidate = {
    dataset_id: text(body.dataset_id, 'id'),
    date: text(body.date, 'date'),
    rows: sequence(body.rows, 'rows').map(parse_row),
    query_end_at: number(body.query_end_at),
    query_start_at: body.query_start_at == null ? null : number(body.query_start_at),
    labels: labels(body.labels ?? {}),
    unavailable_reason: optional(body.unavailable_reason, 'unavailable reason'),
    limitation: optional(body.limitation, 'limitation'),
  };
  validate_candidate(candidate);
  return candidate;
}
function validate_candidate(candidate: CandidateDatasetResult): void {
  text(candidate.dataset_id, 'id');
  calendar_date(candidate.date);
  const end = timestamp(candidate.query_end_at);
  if (candidate.query_start_at !== null && timestamp(candidate.query_start_at) > end)
    invalid('dataset query interval is reversed');
  optional(candidate.unavailable_reason, 'unavailable reason');
  optional(candidate.limitation, 'limitation');
  if (candidate.unavailable_reason !== null && candidate.rows.length)
    invalid('unavailable dataset result cannot contain rows');
  labels(candidate.labels);
  const seen = new Set<string>();
  for (const row of candidate.rows) {
    for (const code of Object.values(object(row.dimensions, 'row dimensions')))
      text(code, 'category code', true);
    for (const value of Object.values(object(row.values, 'row values'))) number(value);
    const key = dimension_key(row.dimensions);
    if (seen.has(key)) invalid('duplicate dataset dimension tuple');
    seen.add(key);
  }
}
export function validate_result(output: OutputDataset, candidate: CandidateDatasetResult): void {
  validate_candidate(candidate);
  if (candidate.dataset_id !== output.dataset_id) invalid('foreign dataset output');
  const dimensions = output.dimensions.map((item) => item.name);
  const fields = output.fields.map((item) => item.name);
  if (
    candidate.rows.some((row) => !exact(row.dimensions, dimensions) || !exact(row.values, fields))
  )
    invalid('dataset row names differ from captured output');
  if (Object.keys(candidate.labels).some((key) => !dimensions.includes(key)))
    invalid('dataset labels name an undeclared dimension');
}
export function validate_batch(
  outputs: OutputDataset[],
  candidates: CandidateDatasetResult[],
): void {
  const by_id = new Map(outputs.map((item) => [item.dataset_id, item]));
  if (by_id.size !== outputs.length) invalid('duplicate captured dataset output');
  const seen = new Set<string>();
  const prior_labels = new Map<string, string>();
  for (const candidate of candidates) {
    const output = by_id.get(candidate.dataset_id);
    if (!output) invalid('foreign dataset output');
    const pair = JSON.stringify([candidate.dataset_id, candidate.date]);
    if (seen.has(pair)) invalid('duplicate dataset date result');
    seen.add(pair);
    validate_result(output, candidate);
    for (const [dimension, items] of Object.entries(candidate.labels))
      for (const [code, label] of Object.entries(items)) {
        const key = JSON.stringify([candidate.dataset_id, dimension, code]);
        if (prior_labels.has(key) && prior_labels.get(key) !== label)
          invalid('dataset category labels conflict within the run');
        prior_labels.set(key, label);
      }
  }
}
