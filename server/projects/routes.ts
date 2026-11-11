import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import { DEFAULT_LIST_LIMIT } from '../list_reads/configuration.ts';
import { checkPage, pageFacts, parseReadDetail, rejectParameters, requireFullForOne } from '../list_reads/logic/index.ts';
import { dictionaryBody, integerQuery, QueryIntegerError, scalarQueries } from '../list_reads/http.ts';
import { createProject, listProjects, readProject, updateProject } from './data/index.ts';
import { bodyString, nullableFolderPath } from './logic/index.ts';
import type { CreateProjectBody, UpdateProjectBody } from './contracts.ts';

export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.get<{ Querystring: { detail?: string; id?: string; limit?: string; offset?: string } }>('/projects', async (request, reply) => {
    const q = scalarQueries(request.query);
    let limit: number | undefined, offset: number | undefined;
    const failures: ReturnType<QueryIntegerError['payload']>[] = [];
    for (const key of ['limit', 'offset'] as const) {
      try {
        const value = integerQuery(q[key], key);
        if (key === 'limit') limit = value; else offset = value;
      } catch (error) {
        if (error instanceof QueryIntegerError) failures.push(error.payload()); else throw error;
      }
    }
    if (failures.length) { reply.code(422).send({ detail: failures }); return; }
    const level = parseReadDetail(q.detail);
    return services.database.read(sql => {
      if (q.id !== undefined) {
        requireFullForOne(level);
        rejectParameters('id', { limit, offset });
        return readProject(sql, q.id);
      }
      if (level === 'full') {
        rejectParameters('detail=full', { limit, offset });
        return { projects: listProjects(sql) };
      }
      const page = checkPage({ limit: limit ?? DEFAULT_LIST_LIMIT, offset: offset ?? 0 });
      const rows = sql.all<{id: string; name: string}>('SELECT id, name FROM projects ORDER BY lower(name), id');
      const projects = rows.slice(page.offset, page.offset + page.limit);
      return { projects, page: pageFacts(rows.length, projects.length, page) };
    });
  });
  app.post('/projects', async (request, reply) => {
    const raw = request.body;
    if (!dictionaryBody(raw, reply)) return;
    const body: CreateProjectBody = { name: bodyString(raw, 'name'), summary: bodyString(raw, 'summary') };
    if ('folder_path' in raw) body.folder_path = nullableFolderPath(raw.folder_path);
    return services.database.write(tx => createProject(tx, body, services.clock.nowUnix()));
  });
  app.patch<{ Params: { project_id: string } }>('/projects/:project_id', async (request, reply) => {
    const raw = request.body;
    if (!dictionaryBody(raw, reply)) return;
    const body: UpdateProjectBody = {};
    if ('name' in raw) body.name = bodyString(raw, 'name');
    if ('summary' in raw) body.summary = bodyString(raw, 'summary');
    if ('folder_path' in raw) body.folder_path = nullableFolderPath(raw.folder_path);
    if (!Object.keys(body).length) throw new PlannerError('validation', 'no project fields to update');
    return services.database.write(tx => updateProject(tx, request.params.project_id, body, services.clock.nowUnix()));
  });
};
