import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Principal } from '../core/contracts.ts';
import { JsonNumber, numeric_kind, numeric_value } from '../core/http/numeric_json.ts';
import { install_numeric_json_parser } from '../core/http/numeric_intake.ts';
import {
  dictionaryBody,
  integerQuery,
  QueryIntegerError,
  scalarQueries,
} from '../list_reads/http.ts';
import { admission as measure_admission } from '../measures/routes.ts';
import * as actions from './actions.ts';
import * as data from './data.ts';
import * as views from './views.ts';
import * as run_actions from './run_actions.ts';
function invalid(detail = 'invalid measure source request') {
  return new PlannerError('validation', detail);
}
function text(b: Record<string, unknown>, k: string, path = '$'): string {
  if (typeof b[k] !== 'string')
    throw invalid(`${path}.${k}: ${k in b ? 'expected text' : 'required'}`);
  return b[k];
}
function integer(b: Record<string, unknown>, k: string): number {
  if (numeric_kind(b[k]) !== 'integer') throw invalid();
  return numeric_value(b[k])!;
}
function keys(b: Record<string, unknown>, allowed: string[], path = '$') {
  const unknown = Object.keys(b)
    .filter((k) => !allowed.includes(k))
    .sort();
  if (unknown.length) {
    const k = unknown[0]!;
    throw invalid(
      `${k.length <= 64 && /^[A-Za-z_][A-Za-z_0-9]*$/.test(k) ? `${path}.${k}` : path}: unknown field`,
    );
  }
}
export function parse_source_responsible(raw: unknown): Principal {
  const p = '$.responsible';
  if (raw == null || typeof raw !== 'object' || Array.isArray(raw) || raw instanceof JsonNumber)
    throw invalid(`${p}: expected an object with kind and id`);
  const b = raw as Record<string, unknown>;
  keys(b, ['kind', 'id'], p);
  if (typeof b.kind !== 'string' || !['chief', 'manager', 'goal'].includes(b.kind))
    throw invalid(`${p}.kind: supported values: chief, manager, goal`);
  const id = text(b, 'id', p);
  if (b.kind === 'chief' && id !== 'chief')
    throw invalid(`${p}.id: chief responsibility requires chief`);
  return { kind: b.kind, id } as Principal;
}
export const admission = { ...measure_admission, parseSourceResponsible: parse_source_responsible };
export const routes: FastifyPluginAsync<AreaServices> = async (app, s) => {
  install_numeric_json_parser(app);
  const options = (req: Parameters<AreaServices['identity']>[0]) => ({
    principal: s.identity(req).principal,
    now: s.clock.nowUnix(),
  });
  function page(raw: unknown) {
    const q = scalarQueries(raw as Record<string, string | undefined>);
    let limit = 30,
      offset = 0;
    const failures: unknown[] = [];
    for (const k of ['limit', 'offset'] as const) {
      try {
        const v = integerQuery(q[k], k);
        if (v !== undefined) {
          if (k === 'limit') limit = v;
          else offset = v;
        }
      } catch (e) {
        if (e instanceof QueryIntegerError) failures.push(e.payload());
        else throw e;
      }
    }
    return { q, limit, offset, failures };
  }
  app.get('/measure-sources', async (req, reply) => {
    const { q, limit, offset, failures } = page(req.query);
    if (failures.length) return reply.code(422).send({ detail: failures });
    return s.database.read((sql) =>
      views.list_json(sql, {
        project_id: q.project_id ?? null,
        limit,
        offset,
        now: s.clock.nowUnix(),
      }),
    );
  });
  app.post('/measure-sources', async (req, reply) => {
    if (!dictionaryBody(req.body, reply)) return;
    const b = req.body;
    keys(b, ['name', 'project_id', 'account', 'responsible']);
    const value = {
      name: text(b, 'name'),
      scope: { project_id: text(b, 'project_id'), account: text(b, 'account') },
      responsible: parse_source_responsible(b.responsible),
    };
    return s.database.write((sql) => actions.create_source(sql, value, options(req)));
  });
  app.get<{ Params: { source_id: string } }>('/measure-sources/:source_id', async (req) =>
    s.database.read((sql) => data.read_source(sql, req.params.source_id)),
  );
  app.get<{ Params: { source_id: string } }>('/measure-sources/:source_id/status', async (req) =>
    s.database.read((sql) => views.status(sql, req.params.source_id, { now: s.clock.nowUnix() })),
  );
  app.delete<{ Params: { source_id: string } }>('/measure-sources/:source_id', async (req) => {
    s.database.write((sql) => actions.delete_source(sql, req.params.source_id, options(req)));
    return { deleted: true };
  });
  app.put<{ Params: { source_id: string } }>(
    '/measure-sources/:source_id/schedule',
    async (req, reply) => {
      if (!dictionaryBody(req.body, reply)) return;
      const b = req.body;
      keys(b, ['expected_revision', 'enabled', 'local_time']);
      if (
        typeof b.enabled !== 'boolean' ||
        (b.local_time != null && typeof b.local_time !== 'string')
      )
        throw invalid();
      const v = {
        expected_revision: integer(b, 'expected_revision'),
        enabled: b.enabled,
        local_time: (b.local_time ?? null) as string | null,
      };
      return s.database.write((sql) =>
        actions.set_schedule(sql, req.params.source_id, v, options(req)),
      );
    },
  );
  app.put<{ Params: { source_id: string } }>(
    '/measure-sources/:source_id/responsible',
    async (req, reply) => {
      if (!dictionaryBody(req.body, reply)) return;
      const b = req.body;
      keys(b, ['expected_responsibility_revision', 'responsible']);
      const responsible = parse_source_responsible(b.responsible);
      const o = {
        ...options(req),
        expected_responsibility_revision: integer(b, 'expected_responsibility_revision'),
      };
      return s.database.write((sql) =>
        actions.set_responsible(sql, req.params.source_id, responsible, o),
      );
    },
  );
  app.put<{ Params: { source_id: string } }>(
    '/measure-sources/:source_id/script',
    async (req, reply) => {
      if (!dictionaryBody(req.body, reply)) return;
      const b = req.body;
      keys(b, ['expected_revision', 'text']);
      const v = { expected_revision: integer(b, 'expected_revision'), text: text(b, 'text') };
      return s.database.write((sql) =>
        actions.submit_script(sql, req.params.source_id, v, options(req)),
      );
    },
  );
  app.get<{ Params: { source_id: string } }>(
    '/measure-sources/:source_id/runs',
    async (req, reply) => {
      const { limit, offset, failures } = page(req.query);
      if (failures.length) return reply.code(422).send({ detail: failures });
      return s.database.read((sql) =>
        views.runs_json(sql, req.params.source_id, { limit, offset }),
      );
    },
  );
  app.post<{ Params: { source_id: string } }>(
    '/measure-sources/:source_id/runs',
    async (req, reply) => {
      if (!dictionaryBody(req.body, reply)) return;
      const b = req.body;
      keys(b, ['mode', 'history', 'expected_script_revision']);
      let history: { first_date: string; last_date: string } | null = null;
      if (b.history != null) {
        const r = b.history;
        if (
          typeof r !== 'object' ||
          Array.isArray(r) ||
          r instanceof JsonNumber ||
          Object.keys(r).sort().join() !== 'first_date,last_date'
        )
          throw invalid();
        history = {
          first_date: text(r as Record<string, unknown>, 'first_date'),
          last_date: text(r as Record<string, unknown>, 'last_date'),
        };
      }
      const mode = text(b, 'mode');
      if (mode !== 'collection' && mode !== 'test') throw invalid();
      const o = {
        ...options(req),
        mode: mode as 'collection' | 'test',
        history,
        expected_script_revision: integer(b, 'expected_script_revision'),
      };
      return s.database.write((sql) => run_actions.request_run(sql, req.params.source_id, o));
    },
  );
  app.get<{ Params: { run_id: string } }>('/measure-source-runs/:run_id', async (req) =>
    s.database.read((sql) => data.read_run_detail(sql, req.params.run_id)),
  );
};
