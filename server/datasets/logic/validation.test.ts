import { expect, test } from 'vitest';
import {
  parse_dimensions,
  parse_fields,
  parse_result,
  validate_definition,
  validate_result,
  validate_batch,
  dimension_key,
} from './validation.ts';
import type { CandidateDatasetResult, Dataset, OutputDataset } from '../contracts.ts';
const dimensions = [
  { name: 'country', label: 'Country' },
  { name: 'browser', label: 'Browser' },
];
const fields = [{ name: 'events', label: 'Events', unit: 'events', kind: 'count' as const }];
const output: OutputDataset = {
  dataset_id: 'events',
  definition_revision: 7,
  timezone: 'Europe/Berlin',
  dimensions,
  fields,
  required_date: '2026-10-29',
};
const row = { dimensions: { country: 'DE', browser: 'Firefox' }, values: { events: -2.5 } };
const result: CandidateDatasetResult = {
  dataset_id: 'events',
  date: '2026-10-29',
  rows: [row],
  query_end_at: 100.5,
  query_start_at: null,
  labels: {},
  unavailable_reason: null,
  limitation: null,
};
const definition: Dataset = {
  id: 'events',
  project_id: 'project',
  source_id: 'source',
  name: 'Events',
  timezone: 'Europe/Berlin',
  dimensions,
  fields,
  definition_revision: 7,
  created_at: 0,
  updated_at: 0,
};
test('definition declarations round trip without kind arithmetic', () => {
  validate_definition(definition);
  expect(parse_dimensions(dimensions)).toEqual(dimensions);
  for (const kind of ['count', 'amount', 'snapshot'] as const) {
    const current = [{ ...fields[0]!, kind, unit: '' }];
    expect(parse_fields(current)).toEqual(current);
    validate_definition({ ...definition, fields: current });
    validate_result({ ...output, fields: current }, result);
  }
});
test('invalid definitions never reach storage', () => {
  for (const changes of [
    { name: ' ' },
    { timezone: 'not/a/timezone' },
    { dimensions: [] },
    { dimensions: [dimensions[0]!, dimensions[0]!] },
    { dimensions: [{ name: '', label: 'Country' }] },
    { fields: [] },
    { fields: [fields[0]!, fields[0]!] },
  ])
    expect(() => validate_definition({ ...definition, ...changes })).toThrow();
});
test('dimension parser rejects noncanonical declarations', () => {
  for (const value of [
    null,
    {},
    [{ name: 'country' }],
    [{ name: 'country', label: 'Country', codes: [] }],
  ])
    expect(() => parse_dimensions(value)).toThrow();
});
test('field parser rejects noncanonical declarations', () => {
  for (const value of [
    null,
    [],
    [{ name: 'events', label: 'Events', unit: 'events', kind: 'ratio' }],
    [{ name: 'events', label: 'Events', kind: 'count' }],
  ])
    expect(() => parse_fields(value)).toThrow();
});
test('result parser uses contract names and optional defaults', () => {
  expect(parse_result(result)).toEqual(result);
  expect(
    parse_result({ dataset_id: 'events', date: '2026-10-29', rows: [], query_end_at: 123 }),
  ).toEqual({ ...result, rows: [], query_end_at: 123 });
});
test('result parser rejects missing cutoffs bad dates and shape', () => {
  const { query_end_at: _, ...missing } = result;
  for (const body of [
    missing,
    { ...result, query_end_at: null },
    { ...result, cutoff: 100 },
    ...['20261029', '2026-W44-4', '2026-02-30'].map((date) => ({ ...result, date })),
    { ...result, rows: [{ dimensions: row.dimensions }] },
    { ...result, rows: null },
  ])
    expect(() => parse_result(body)).toThrow();
});
test('all numeric boundaries reject nonfinite and boolean values', () => {
  for (const value of [true, '2', NaN, Infinity, Infinity])
    for (const candidate of [
      { ...result, query_end_at: value },
      { ...result, query_start_at: value },
      { ...result, rows: [{ ...row, values: { events: value } }] },
    ]) {
      expect(() => validate_result(output, candidate as CandidateDatasetResult)).toThrow();
      expect(() => parse_result(candidate)).toThrow();
    }
});
test('finite timestamps must support datetime interpretation', () => {
  for (const candidate of [
    { ...result, query_end_at: 1e300 },
    { ...result, query_start_at: -1e300 },
  ]) {
    expect(() => validate_result(output, candidate)).toThrow(/UTC datetime range/);
    expect(() => parse_result(candidate)).toThrow(/UTC datetime range/);
  }
  validate_result(output, { ...result, rows: [{ ...row, values: { events: 1e300 } }] });
});
test('dates and cutoffs are observations without extra admission gates', () => {
  validate_result(output, {
    ...result,
    date: '2000-01-01',
    query_start_at: -100,
    query_end_at: -100,
  });
  validate_result({ ...output, definition_revision: 99 }, result);
  validate_result(output, { ...result, rows: [], limitation: 'Provider retains seven days' });
  validate_result(output, { ...result, rows: [], unavailable_reason: 'Provider has no history' });
  expect(() => validate_result(output, { ...result, query_start_at: 101 })).toThrow(/interval/);
  expect(() =>
    validate_result(output, { ...result, unavailable_reason: 'Provider has no history' }),
  ).toThrow(/cannot contain rows/);
});
test('rows match the captured dimension and field names exactly', () => {
  for (const changed of [
    { ...row, dimensions: { country: 'DE' } },
    { ...row, dimensions: { ...row.dimensions, device: 'desktop' } },
    { ...row, dimensions: { country: 'DE', Browser: 'Firefox' } },
    { ...row, values: {} },
    { ...row, values: { events: 1, users: 2 } },
    { ...row, values: { Events: 1 } },
  ])
    expect(() => validate_result(output, { ...result, rows: [changed as typeof row] })).toThrow(
      /captured output/,
    );
});
test('tuple identity is order independent and preserves names and codes', () => {
  expect(dimension_key({ '\u{10000}': '\u007f', '\ue000': 'é' })).toBe(
    '[["\\ue000","\\u00e9"],["\\ud800\\udc00","\\u007f"]]',
  );
  const codes = { country: 'DE,|["', browser: 'Firefox' };
  expect(dimension_key(codes)).toBe('[["browser","Firefox"],["country","DE,|[\\\""]]');
  expect(dimension_key(codes)).toBe(
    dimension_key(Object.fromEntries(Object.entries(codes).reverse())),
  );
  expect(dimension_key({ a: 'b|c', d: 'e' })).not.toBe(dimension_key({ a: 'b', 'c|d': 'e' }));
  expect(() =>
    validate_result(output, {
      ...result,
      rows: [
        row,
        { ...row, dimensions: Object.fromEntries(Object.entries(row.dimensions).reverse()) },
      ],
    }),
  ).toThrow(/duplicate.*tuple/);
});
test('batch rejects foreign and duplicate dates but accepts distinct dates', () => {
  const previous = { ...result, date: '2026-10-28' };
  validate_batch([output], [result, previous]);
  expect(() => validate_batch([output], [result, result])).toThrow(/duplicate dataset date/);
  expect(() => validate_batch([output], [previous, { ...result, dataset_id: 'foreign' }])).toThrow(
    /foreign/,
  );
  expect(() => validate_result(output, { ...result, dataset_id: 'foreign' })).toThrow(/foreign/);
});
test('labels use declared dimensions and remain consistent within each dataset run', () => {
  const current = { ...result, labels: { country: { DE: 'Germany' } } };
  const previous = { ...current, date: '2026-10-28' };
  validate_batch([output], [current, previous]);
  validate_batch([output], [current, { ...previous, labels: {} }]);
  const renamed = { ...previous, labels: { country: { DE: 'Deutschland' } } };
  expect(() => validate_batch([output], [current, renamed])).toThrow(/conflict/);
  expect(() =>
    validate_result(output, { ...result, labels: { device: { desktop: 'Desktop' } } }),
  ).toThrow(/undeclared/);
  validate_batch(
    [output, { ...output, dataset_id: 'other' }],
    [current, { ...renamed, dataset_id: 'other' }],
  );
});
