import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import {
  dictionaryBody,
  integerQuery,
  QueryIntegerError,
  scalarQueries,
} from '../list_reads/http.ts';
import { numeric_kind } from '../core/http/numeric_json.ts';
import { install_numeric_json_parser } from '../core/http/numeric_intake.ts';
import type { CountPeriod, Measure } from './contracts.ts';
import * as data from './data.ts';
import * as actions from './actions.ts';
import { invalid } from './logic/dates.ts';
import { parse_formula } from './logic/formulas.ts';
import { parse_write } from './logic/manual.ts';
import { read_state, write_value, manager_manual_measure } from './manual.ts';
export const admission = {
  sourceResponsible: data.source_responsible,
  measureSource: data.measure_source,
  managerManualMeasure: manager_manual_measure,
};
function text(b: Record<string, unknown>, key: string): string {
  if (typeof b[key] !== 'string')
    throw invalid(`$.${key}: ${key in b ? 'expected text' : 'required'}`);
  return b[key];
}
function period(value: unknown): CountPeriod | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== 'string' || !['day', 'week', 'month'].includes(value))
    throw invalid('$.period: supported values: day, week, month');
  return value as CountPeriod;
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, s) => {
  install_numeric_json_parser(app);
  app.get('/measures', async (req, reply) => {
    const q = scalarQueries(req.query as Record<string, string | undefined>);
    let limit = 25,
      offset = 0;
    const failures: unknown[] = [];
    for (const key of ['limit', 'offset'] as const) {
      try {
        const v = integerQuery(q[key], key);
        if (v !== undefined) {
          if (key === 'limit') limit = v;
          else offset = v;
        }
      } catch (e) {
        if (e instanceof QueryIntegerError) failures.push(e.payload());
        else throw e;
      }
    }
    if (failures.length) return reply.code(422).send({ detail: failures });
    return s.database.read((sql) =>
      data.list_measures(sql, q.project_id ?? null, q.source_id ?? null, limit, offset),
    );
  });
  app.get<{ Params: { measure_id: string } }>('/measures/:measure_id', async (req) =>
    s.database.read((sql) => data.read_measure(sql, req.params.measure_id)),
  );
  app.post('/measures/read', async (req, reply) => {
    if (!dictionaryBody(req.body, reply)) return;
    const b = req.body;
    if (
      Object.keys(b).some(
        (k) => !['measure_ids', 'first_date', 'last_date', 'limit'].includes(k),
      ) ||
      !Array.isArray(b.measure_ids) ||
      !b.measure_ids.every((v) => typeof v === 'string')
    )
      throw invalid('invalid measure read');
    const body = {
      measure_ids: b.measure_ids as string[],
      first_date: text(b, 'first_date'),
      last_date: text(b, 'last_date'),
      ...('limit' in b ? { limit: b.limit } : {}),
    };
    return s.database.read((sql) => actions.read_values(sql, body, s.clock.nowUnix()));
  });
  app.post('/measures', async (req, reply) => {
    if (!dictionaryBody(req.body, reply)) return;
    const b = req.body,
      allowed = [
        'project_id',
        'name',
        'unit',
        'origin',
        'kind',
        'timezone',
        'period',
        'source_id',
        'formula',
      ],
      unknown = Object.keys(b)
        .filter((k) => !allowed.includes(k))
        .sort();
    if (unknown.length) {
      const field = unknown[0]!;
      const path =
        field.length <= 64 && /^[A-Za-z_][A-Za-z_0-9]*$/.test(field) ? `$.${field}` : '$';
      throw invalid(`${path}: unknown field`);
    }
    const origin = text(b, 'origin'),
      kind = text(b, 'kind');
    if (!['taken', 'assembled', 'manual'].includes(origin))
      throw invalid('$.origin: supported values: taken, assembled, manual');
    if (!['reading', 'running_total', 'period_count'].includes(kind))
      throw invalid('$.kind: supported values: reading, running_total, period_count');
    const source = b.source_id ?? null;
    if (source !== null && typeof source !== 'string')
      throw invalid('$.source_id: expected text or null');
    const body = {
      project_id: text(b, 'project_id'),
      name: text(b, 'name'),
      unit: text(b, 'unit'),
      origin: origin as Measure['origin'],
      kind: kind as Measure['kind'],
      timezone: text(b, 'timezone'),
      period: period(b.period),
      source_id: source as string | null,
      formula: b.formula == null ? null : parse_formula(b.formula),
    };
    return s.database.write((sql) => actions.create_measure(sql, body, s.clock.nowUnix()));
  });
  app.patch<{ Params: { measure_id: string } }>('/measures/:measure_id', async (req, reply) => {
    if (!dictionaryBody(req.body, reply)) return;
    const b = { ...req.body };
    if (
      Object.keys(b).some(
        (k) =>
          !['expected_revision', 'name', 'unit', 'kind', 'timezone', 'period', 'formula'].includes(
            k,
          ),
      )
    )
      throw invalid('invalid measure update');
    if (numeric_kind(b.expected_revision) !== 'integer')
      throw invalid('measure revision is required');
    for (const key of ['name', 'unit', 'timezone']) if (key in b) text(b, key);
    if (
      'kind' in b &&
      (typeof b.kind !== 'string' || !['reading', 'running_total', 'period_count'].includes(b.kind))
    )
      throw invalid('invalid measure kind');
    if ('period' in b) b.period = period(b.period);
    if ('formula' in b) b.formula = parse_formula(b.formula);
    return s.database.write((sql) =>
      actions.update_measure(sql, req.params.measure_id, b, s.clock.nowUnix()),
    );
  });
  app.delete<{ Params: { measure_id: string } }>('/measures/:measure_id', async (req) => {
    s.database.write((sql) =>
      actions.delete_measure(sql, req.params.measure_id, s.clock.nowUnix()),
    );
    return { deleted: true };
  });
  app.get<{ Params: { measure_id: string }; Querystring: { date?: string } }>(
    '/measures/:measure_id/manual-state',
    async (req) =>
      s.database.read((sql) =>
        read_state(
          sql,
          req.params.measure_id,
          scalarQueries(req.query).date ?? null,
          s.clock.nowUnix(),
        ),
      ),
  );
  app.post<{ Params: { measure_id: string } }>(
    '/measures/:measure_id/manual-writes',
    async (req, reply) => {
      if (!dictionaryBody(req.body, reply)) return;
      const body = parse_write(req.body);
      return s.database.write((sql) =>
        write_value(sql, req.params.measure_id, body, s.identity(req).principal, s.clock.nowUnix()),
      );
    },
  );
};
