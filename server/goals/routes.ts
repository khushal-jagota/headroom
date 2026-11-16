import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { MAX_PYTHON_ANY_DEPTH } from '../core/http/configuration.ts';
import { install_numeric_json_parser } from '../core/http/numeric_intake.ts';
import { dictionaryBody, scalarQueries } from '../list_reads/http.ts';
import { create_goal, update_goal, make_manager, delete_goal } from './actions.ts';
import { calendar_now } from './calendar.ts';
import { read_goal } from './data.ts';
import { goal_json, list_json, readings_json } from './views.ts';
export const routes: FastifyPluginAsync<AreaServices> = async (app, s) => {
  install_numeric_json_parser(app, MAX_PYTHON_ANY_DEPTH);
  app.get('/goals', async (req, reply) => {
    const q = scalarQueries(req.query as Record<string, string | undefined>);
    let active: boolean | null = null;
    if (q.active !== undefined) {
      const key = q.active.toLowerCase();
      if (['true', '1', 'yes', 'on', 't', 'y'].includes(key)) active = true;
      else if (['false', '0', 'no', 'off', 'f', 'n'].includes(key)) active = false;
      else
        return reply.code(422).send({
          detail: [
            {
              type: 'bool_parsing',
              loc: ['query', 'active'],
              msg: 'Input should be a valid boolean, unable to interpret input',
              input: q.active,
            },
          ],
        });
    }
    return s.database.read((sql) =>
      list_json(sql, calendar_now(s.clock), { active, project_id: q.project_id ?? null }),
    );
  });
  app.post('/goals', async (req, reply) => {
    if (!dictionaryBody(req.body, reply)) return;
    return s.database.write((sql) =>
      goal_json(
        sql,
        create_goal(sql, req.body as Record<string, unknown>, s.clock.nowUnix()),
        calendar_now(s.clock),
      ),
    );
  });
  app.get<{ Params: { goal_id: string } }>('/goals/:goal_id', async (req) =>
    s.database.read((sql) =>
      goal_json(sql, read_goal(sql, req.params.goal_id), calendar_now(s.clock)),
    ),
  );
  app.patch<{ Params: { goal_id: string } }>('/goals/:goal_id', async (req, reply) => {
    if (!dictionaryBody(req.body, reply)) return;
    return s.database.write((sql) =>
      goal_json(
        sql,
        update_goal(
          sql,
          req.params.goal_id,
          req.body as Record<string, unknown>,
          s.clock.nowUnix(),
        ),
        calendar_now(s.clock),
      ),
    );
  });
  app.get<{
    Params: { goal_id: string };
    Querystring: { first_date?: string; last_date?: string };
  }>('/goals/:goal_id/readings', async (req) =>
    s.database.read((sql) =>
      readings_json(sql, req.params.goal_id, calendar_now(s.clock), scalarQueries(req.query)),
    ),
  );
  app.post<{ Params: { goal_id: string } }>('/goals/:goal_id/make-manager', async (req, reply) => {
    if (!dictionaryBody(req.body, reply)) return;
    return s.database.write((sql) => {
      const result = make_manager(sql, req.params.goal_id, s.clock);
      return {
        manager_id: result.manager_id,
        goal: goal_json(sql, result.goal, calendar_now(s.clock)),
      };
    });
  });
  app.delete<{ Params: { goal_id: string } }>('/goals/:goal_id', async (req) =>
    delete_goal(
      s.database,
      s.config.db_path,
      req.params.goal_id,
      s.identity(req).principal,
      s.clock.nowUnix(),
    ),
  );
};
