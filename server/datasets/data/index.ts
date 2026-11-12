import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import { newId } from '../../core/ids.ts';
import { parse_numeric_json, stringify_python_json } from '../../core/http/numeric_json.ts';
import { pageFacts } from '../../list_reads/logic/index.ts';
import { date_range, local_date } from '../../measures/logic/dates.ts';
import { read_health, read_source, bump_source } from '../../measures/data.ts';
import { parse_dimensions, parse_fields, validate_definition } from '../logic/validation.ts';
import type {
  CreateDatasetBody,
  Dataset,
  DatasetResult,
  DatasetRow,
  OutputDataset,
  ReadDatasetsBody,
  UpdateDatasetBody,
} from '../contracts.ts';
type DatasetRecord = Omit<Dataset, 'dimensions' | 'fields'> & {
  dimensions: string;
  fields: string;
};
function dataset(row: DatasetRecord): Dataset {
  return {
    ...row,
    dimensions: parse_dimensions(JSON.parse(row.dimensions)),
    fields: parse_fields(JSON.parse(row.fields)),
  };
}
export function read_dataset(sql: ReadSql, id: string): Dataset {
  const row = sql.get<DatasetRecord>('SELECT * FROM datasets WHERE id=?', [id]);
  if (!row) throw new PlannerError('not_found', 'dataset not found');
  return dataset(row);
}
export function list_datasets(
  sql: ReadSql,
  project_id: string | null,
  source_id: string | null,
  limit: number,
  offset: number,
) {
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 100 ||
    !Number.isInteger(offset) ||
    offset < 0
  )
    throw new PlannerError('validation', 'invalid bounded list request');
  const conditions: string[] = [];
  const args: (string | number)[] = [];
  for (const [key, value] of [
    ['project_id', project_id],
    ['source_id', source_id],
  ])
    if (value !== null) {
      conditions.push(`${key}=?`);
      args.push(value!);
    }
  const clause = conditions.length ? ` WHERE ${conditions.join(' AND ')}` : '';
  const count = sql.get<{ count: number }>(
    `SELECT COUNT(*) AS count FROM datasets${clause}`,
    args,
  )!.count;
  const rows = sql
    .all<DatasetRecord>(`SELECT * FROM datasets${clause} ORDER BY id LIMIT ? OFFSET ?`, [
      ...args,
      limit,
      offset,
    ])
    .map(dataset);
  return { datasets: rows, page: pageFacts(count, rows.length, { limit, offset }) };
}
export function create_dataset(sql: TransactionSql, body: CreateDatasetBody, now: number): Dataset {
  const value: Dataset = {
    id: newId('dataset'),
    ...body,
    definition_revision: 1,
    created_at: now,
    updated_at: now,
  };
  validate_definition(value);
  const source = read_source(sql, value.source_id);
  if (source.scope.project_id !== value.project_id)
    throw new PlannerError('validation', 'dataset and source Projects differ');
  sql.run(
    `INSERT INTO datasets(id,project_id,source_id,name,timezone,dimensions,fields,
    definition_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)`,
    [
      value.id,
      value.project_id,
      value.source_id,
      value.name,
      value.timezone,
      stringify_python_json(value.dimensions, { ensure_ascii: true }),
      stringify_python_json(value.fields, { ensure_ascii: true }),
      1,
      now,
      now,
    ],
  );
  bump_source(sql, value.source_id, now);
  return value;
}
export function update_dataset(
  sql: TransactionSql,
  id: string,
  body: UpdateDatasetBody,
  now: number,
): Dataset {
  const current = read_dataset(sql, id);
  if (
    !Number.isInteger(body.expected_revision) ||
    body.expected_revision !== current.definition_revision
  )
    throw new PlannerError('stale_claim', 'dataset definition changed');
  const { expected_revision: _, ...changes } = body;
  const edited = {
    ...current,
    ...changes,
    definition_revision: current.definition_revision + 1,
    updated_at: now,
  };
  validate_definition(edited);
  sql.run(
    `UPDATE datasets SET name=?,timezone=?,dimensions=?,fields=?,definition_revision=?,
    updated_at=? WHERE id=?`,
    [
      edited.name,
      edited.timezone,
      stringify_python_json(edited.dimensions, { ensure_ascii: true }),
      stringify_python_json(edited.fields, { ensure_ascii: true }),
      edited.definition_revision,
      now,
      id,
    ],
  );
  bump_source(sql, current.source_id, now);
  return edited;
}
export function earliest_retained_date(sql: ReadSql, id: string): string | null {
  return sql.get<{ date: string | null }>(
    'SELECT MIN(date) AS date FROM dataset_results WHERE dataset_id=?',
    [id],
  )!.date;
}
export function prepare_outputs(
  sql: ReadSql,
  source_id: string,
  frozen_at: number,
): OutputDataset[] {
  return sql
    .all<DatasetRecord>('SELECT * FROM datasets WHERE source_id=? ORDER BY id', [source_id])
    .map(dataset)
    .map((item) => ({
      dataset_id: item.id,
      definition_revision: item.definition_revision,
      timezone: item.timezone,
      dimensions: item.dimensions,
      fields: item.fields,
      required_date: local_date(frozen_at, item.timezone),
    }));
}
interface Header {
  dataset_id: string;
  date: string;
  run_id: string;
  definition_revision: number;
  output: string;
  collected_at: number;
  query_end_at: number;
  query_start_at: number | null;
  unavailable_reason: string | null;
  limitation: string | null;
  source_id: string;
  source_revision: number;
  script_revision: number;
  script_sha256: string;
}
export function read_values(sql: ReadSql, request: ReadDatasetsBody, now: number) {
  const identifiers = request.dataset_ids;
  if (!identifiers.length || new Set(identifiers).size !== identifiers.length)
    throw new PlannerError('validation', 'dataset read requires distinct dataset IDs');
  const maximum = request.limit;
  if (maximum !== undefined && (!Number.isInteger(maximum) || maximum < 1))
    throw new PlannerError('validation', 'invalid dataset read limit');
  const days = date_range(request.first_date, request.last_date);
  const definitions = identifiers.map((id) => read_dataset(sql, id));
  const results: DatasetResult[] = [];
  const labels: { dataset_id: string; dimension: string; code: string; label: string }[] = [];
  const missing: { dataset_id: string; date: string }[] = [];
  let returned = 0;
  let matching = 0;
  for (const definition of definitions) {
    const headers = sql.all<Header>(
      `SELECT r.*,s.source_id,s.source_revision,s.script_revision,s.script_sha256
      FROM dataset_results r JOIN measure_source_runs s ON s.id=r.run_id
      WHERE r.dataset_id=? AND r.date>=? AND r.date<=? ORDER BY r.date`,
      [definition.id, request.first_date, request.last_date],
    );
    const present = new Set(headers.map((header) => header.date));
    missing.push(
      ...days
        .filter((day) => !present.has(day))
        .map((date) => ({ dataset_id: definition.id, date })),
    );
    for (const header of headers) {
      matching += sql.get<{ count: number }>(
        'SELECT COUNT(*) AS count FROM dataset_rows WHERE dataset_id=? AND date=?',
        [definition.id, header.date],
      )!.count;
      const rows = sql
        .all<{ dimensions: string; field_values: string }>(
          `SELECT dimensions,field_values FROM dataset_rows
        WHERE dataset_id=? AND date=? ORDER BY dimension_key LIMIT ?`,
          [definition.id, header.date, maximum === undefined ? -1 : maximum - returned],
        )
        .map(
          (row) =>
            ({
              dimensions: JSON.parse(row.dimensions),
              values: parse_numeric_json(row.field_values),
            }) as DatasetRow,
        );
      returned += rows.length;
      results.push({
        dataset_id: definition.id,
        date: header.date,
        rows,
        query_end_at: header.query_end_at,
        query_start_at: header.query_start_at,
        unavailable_reason: header.unavailable_reason,
        limitation: header.limitation,
        provenance: {
          run_id: header.run_id,
          source_id: header.source_id,
          source_revision: header.source_revision,
          script_revision: header.script_revision,
          script_sha256: header.script_sha256,
          definition_revision: header.definition_revision,
          collected_at: header.collected_at,
        },
        output: JSON.parse(header.output) as OutputDataset,
      });
    }
    labels.push(
      ...sql
        .all<{ dimension: string; code: string; label: string }>(
          `SELECT dimension,code,label FROM dataset_category_labels
      WHERE dataset_id=? ORDER BY dimension,code`,
          [definition.id],
        )
        .map((row) => ({ dataset_id: definition.id, ...row })),
    );
  }
  return {
    first_date: request.first_date,
    last_date: request.last_date,
    datasets: definitions,
    results,
    labels,
    missing_dates: missing,
    sources: read_health(sql, [...new Set(definitions.map((item) => item.source_id))].sort(), now),
    returned_rows: returned,
    matching_rows: matching,
    truncated: returned < matching,
  };
}
