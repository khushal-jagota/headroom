import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import {
  dictionaryBody,
  integerQuery,
  QueryIntegerError,
  scalarQueries,
} from '../list_reads/http.ts';
import { numeric_kind, numeric_value } from '../core/http/numeric_json.ts';
import { install_numeric_json_parser } from '../core/http/numeric_intake.ts';
import {
  list_datasets,
  read_dataset,
  read_values,
  create_dataset,
  update_dataset,
} from './data/index.ts';
import { parse_dimensions, parse_fields } from './logic/validation.ts';
import type { UpdateDatasetBody } from './contracts.ts';
function invalid(message: string): never {
  throw new PlannerError('validation', message);
}
function text(body: Record<string, unknown>, key: string): string {
  const value = body[key];
  if (typeof value !== 'string') invalid('invalid dataset request');
  return value;
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  install_numeric_json_parser(app);
  app.get<{
    Querystring: { project_id?: string; source_id?: string; limit?: string; offset?: string };
  }>('/datasets', async (request, reply) => {
    const q = scalarQueries(request.query);
    let limit: number | undefined;
    let offset: number | undefined;
    const failures: ReturnType<QueryIntegerError['payload']>[] = [];
    for (const key of ['limit', 'offset'] as const)
      try {
        const parsed = integerQuery(q[key], key);
        if (key === 'limit') limit = parsed;
        else offset = parsed;
      } catch (error) {
        if (error instanceof QueryIntegerError) failures.push(error.payload());
        else throw error;
      }
    if (failures.length) return reply.code(422).send({ detail: failures });
    return services.database.read((sql) =>
      list_datasets(sql, q.project_id ?? null, q.source_id ?? null, limit ?? 30, offset ?? 0),
    );
  });
  app.post('/datasets/read', async (request, reply) => {
    const raw = request.body;
    if (!dictionaryBody(raw, reply)) return;
    if (
      Object.keys(raw).some(
        (key) => !['dataset_ids', 'first_date', 'last_date', 'limit'].includes(key),
      )
    )
      invalid('invalid dataset read');
    const identifiers = raw.dataset_ids;
    if (
      !Array.isArray(identifiers) ||
      !identifiers.length ||
      !identifiers.every((item) => typeof item === 'string' && item.trim())
    )
      invalid('invalid dataset read identifiers');
    const body = {
      dataset_ids: identifiers as string[],
      first_date: text(raw, 'first_date'),
      last_date: text(raw, 'last_date'),
      ...('limit' in raw ? { limit: numeric_value(raw.limit) } : {}),
    };
    if ('limit' in raw && numeric_kind(raw.limit) !== 'integer')
      invalid('invalid dataset read limit');
    const result = services.database.read((sql) =>
      read_values(sql, body, services.clock.nowUnix()),
    );
    return result;
  });
  app.get<{ Params: { dataset_id: string } }>('/datasets/:dataset_id', async (request) =>
    services.database.read((sql) => read_dataset(sql, request.params.dataset_id)),
  );
  app.post('/datasets', async (request, reply) => {
    const raw = request.body;
    if (!dictionaryBody(raw, reply)) return;
    const keys = ['project_id', 'source_id', 'name', 'timezone', 'dimensions', 'fields'];
    if (Object.keys(raw).length !== keys.length || !keys.every((key) => key in raw))
      invalid('invalid dataset request');
    const body = {
      project_id: text(raw, 'project_id'),
      source_id: text(raw, 'source_id'),
      name: text(raw, 'name'),
      timezone: text(raw, 'timezone'),
      dimensions: parse_dimensions(raw.dimensions),
      fields: parse_fields(raw.fields),
    };
    return services.database.write((sql) => create_dataset(sql, body, services.clock.nowUnix()));
  });
  app.patch<{ Params: { dataset_id: string } }>('/datasets/:dataset_id', async (request, reply) => {
    const raw = request.body;
    if (!dictionaryBody(raw, reply)) return;
    if (
      Object.keys(raw).some(
        (key) => !['expected_revision', 'name', 'timezone', 'dimensions', 'fields'].includes(key),
      )
    )
      invalid('invalid dataset update');
    if (numeric_kind(raw.expected_revision) !== 'integer') invalid('dataset revision is required');
    const body: UpdateDatasetBody = { expected_revision: numeric_value(raw.expected_revision)! };
    if ('name' in raw) body.name = text(raw, 'name');
    if ('timezone' in raw) body.timezone = text(raw, 'timezone');
    if ('dimensions' in raw) body.dimensions = parse_dimensions(raw.dimensions);
    if ('fields' in raw) body.fields = parse_fields(raw.fields);
    return services.database.write((sql) =>
      update_dataset(sql, request.params.dataset_id, body, services.clock.nowUnix()),
    );
  });
};
