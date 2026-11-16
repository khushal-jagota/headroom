import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { dictionaryBody, scalarQueries } from '../list_reads/http.ts';
import { resolveProject } from '../projects/data/index.ts';
import { bodyString, optionalBodyString } from '../projects/logic/index.ts';
import { createIdea, listIdeas } from './data/index.ts';
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  app.post('/ideas', async (request, reply) => {
    const raw = request.body;
    if (!dictionaryBody(raw, reply)) return;
    const title = bodyString(raw, 'title'),
      body = bodyString(raw, 'body');
    const projectName = optionalBodyString(raw, 'project'),
      projectId = optionalBodyString(raw, 'project_id');
    return services.database.write((tx) => {
      const project = resolveProject(tx, projectId, projectName);
      return createIdea(
        tx,
        { title, body, project_id: project?.id ?? null },
        services.clock.nowUnix(),
      );
    });
  });
  app.get<{ Querystring: { project?: string; project_id?: string } }>('/ideas', async (request) =>
    services.database.read((sql) => {
      const query = scalarQueries(request.query);
      const project = resolveProject(sql, query.project_id ?? null, query.project ?? null);
      return { ideas: listIdeas(sql, project?.id ?? null) };
    }),
  );
};
