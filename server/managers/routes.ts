import { JsonNumber, parse_numeric_json } from '../core/http/numeric_json.ts';
/** Manager and Chief document HTTP surface. */
import { randomUUID } from 'node:crypto';
import type { FastifyPluginAsync, FastifyRequest } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { requireOwnerOrChief } from '../core/authority/identity.ts';
import { TestClock } from '../core/clock.ts';
import { PlannerError } from '../core/contracts.ts';
import { dictionaryBody, integerQuery, QueryIntegerError } from '../list_reads/http.ts';
import { parseReadDetail, rejectParameters, requireFullForOne } from '../list_reads/logic/index.ts';
import { DEFAULT_LIST_LIMIT } from '../list_reads/configuration.ts';
import { body_str, body_opt_str } from '../tickets/routes.ts';
import { resolveProject } from '../projects/data/index.ts';
import * as data from './data.ts';
import * as views from './views.ts';
import {
  parse_launch_configuration,
  launch_configuration_json,
  validate_launch_configuration,
} from './launch_settings.ts';
import { manager_lifecycle_lock, delete_manager } from './service.ts';
import * as conversation_start from '../runtime/conversation_start.ts';
import { add_work_attention } from '../work_attention.ts';
import { list_artifact_entries } from '../files/manager_files.ts';
import {
  parse_owner_send_body,
  conversation_message_content,
  delivery_fate_json,
} from '../conversation/http.ts';
import { require_conversation_backend_key } from '../conversation/contracts.ts';
import { video_send_admission } from '../conversation/video_send_admission.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { send_message } from '../message_delivery/service.ts';
import { read_page, write_page, page_readings } from './pages.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
function conversations(services: AreaServices): ConversationSystem {
  if (!services.conversationSystem) throw new Error('conversation system is unavailable');
  return services.conversationSystem;
}
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  const { database, clock } = services;
  const files = new ConversationMessageFiles(services.config.db_path);
  app.get('/managers/launch-defaults', async (request) => {
    requireOwnerOrChief(services.identity(request));
    return database.read((sql) => launch_configuration_json(data.read_launch_defaults(sql)));
  });
  app.put('/managers/launch-defaults', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    requireOwnerOrChief(services.identity(request));
    const configuration = parse_launch_configuration(request.body);
    return database.write((tx) =>
      launch_configuration_json(
        data.update_launch_defaults(tx, configuration, services.identity(request).principal),
      ),
    );
  });
  app.get<{ Params: { item_id: string } }>('/managers/:item_id/launch-defaults', async (request) =>
    database.read((sql) =>
      launch_configuration_json(
        data.read_manager(sql, request.params.item_id).item.manager_launch_configuration,
      ),
    ),
  );
  app.put<{ Params: { item_id: string } }>(
    '/managers/:item_id/launch-defaults',
    async (request, reply) => {
      if (!dictionaryBody(request.body, reply)) return;
      const configuration = parse_launch_configuration(request.body);
      return manager_lifecycle_lock(request.params.item_id).run(async () =>
        database.write((tx) => {
          validate_launch_configuration(tx, configuration);
          const item = data.read_manager(tx, request.params.item_id).item;
          const current = tx.get<{ conversation_id: string | null }>(
            'SELECT conversation_id FROM agents WHERE agent_key=?',
            [item.manager_agent_key],
          )?.conversation_id;
          if (
            current != null &&
            configuration.employee_backend !== item.manager_launch_configuration.employee_backend
          )
            throw new PlannerError(
              'validation',
              'An existing Manager conversation cannot change backend. Start a new conversation before you choose another backend.',
            );
          return launch_configuration_json(
            data.update_manager_launch_configuration(tx, item.id, configuration, {
              principal: services.identity(request).principal,
              clock,
              explicit_choice: true,
            }).manager_launch_configuration,
          );
        }),
      );
    },
  );
  app.post('/managers', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const body = request.body;
    if ('sprint_id' in body) throw new PlannerError('validation', 'Sprint placement is removed');
    const title = body_str(body, 'title');
    const project = body_opt_str(body, 'project');
    const project_id = body_opt_str(body, 'project_id');
    const goal = body_str(body, 'goal');
    const item = database.write((tx) => {
      const resolved = resolveProject(tx, project_id, project, true)!;
      return data.create_manager(tx, {
        title,
        goal,
        project_id: resolved.id,
        clock,
      });
    });
    let startup: Record<string, unknown> | null = null;
    if (item.goal.trim()) {
      try {
        const delivered = await send_message(
          conversations(services),
          database,
          clock,
          services.identity(request),
          { kind: 'manager', id: item.id },
          item.goal,
          'queue',
          {
            sender_message_id: randomUUID(),
            retain_created_conversation: true,
            system_message: true,
          },
        );
        startup = {
          conversation_id: delivered.conversation_id,
          ...delivery_fate_json(delivered.fate),
        };
      } catch (error) {
        console.error('Saved manager but its initial goal failed to start', item.id, error);
        startup = {
          conversation_id: conversation_start.read_agent_conversation(
            database,
            item.manager_agent_key,
          ),
          fate: 'failed',
          error:
            'The manager was saved, but startup did not complete. Inspect its conversation before you retry the goal.',
        };
      }
    }
    return { ...database.read((sql) => views.manager_detail(sql, item.id)), startup };
  });
  app.get('/managers', async (request, reply) => {
    const query = new URL(request.url, 'http://panels.invalid').searchParams;
    const get = (key: string) => query.getAll(key).at(-1);
    let limit: number | undefined, offset: number | undefined;
    try {
      limit = integerQuery(get('limit'), 'limit');
      offset = integerQuery(get('offset'), 'offset');
    } catch (error) {
      if (error instanceof QueryIntegerError)
        return reply.code(422).send({ detail: [error.payload()] });
      throw error;
    }
    let settled: boolean | null = null;
    const raw = get('settled');
    if (raw !== undefined) {
      if (['true', 't', 'yes', 'y', 'on', '1'].includes(raw.toLowerCase())) settled = true;
      else if (['false', 'f', 'no', 'n', 'off', '0'].includes(raw.toLowerCase())) settled = false;
      else
        return reply.code(422).send({
          detail: [
            {
              type: 'bool_parsing',
              loc: ['query', 'settled'],
              msg: 'Input should be a valid boolean, unable to interpret input',
              input: raw,
            },
          ],
        });
    }
    const level = parseReadDetail(get('detail'));
    const id = get('id');
    const search = get('search');
    const project = get('project');
    const project_id = get('project_id');
    const result = database.read((sql) => {
      if (id !== undefined) {
        requireFullForOne(level);
        rejectParameters('id', { search, settled, project, project_id, limit, offset });
        const row = views.manager_detail(sql, id);
        return { response: row, rows: [row] };
      }
      const resolved = resolveProject(sql, project_id ?? null, project ?? null)?.id ?? null;
      if (level === 'full') {
        rejectParameters('detail=full', { search, settled, limit, offset });
        const rows = views.list_managers(sql, resolved);
        return { response: { managers: rows }, rows };
      }
      const response = views.list_manager_summaries(
        sql,
        { limit: limit ?? DEFAULT_LIST_LIMIT, offset: offset ?? 0 },
        { project_id: resolved, search: search ?? null, settled },
      );
      return { response, rows: response.managers };
    });
    await add_work_attention(database, services.conversationSystem, [], result.rows);
    return result.response;
  });
  app.get<{ Params: { item_id: string } }>(
    '/managers/:item_id/workspace-content',
    async (request, reply) => {
      reply.header('Cache-Control', 'no-store');
      return database.read((sql) =>
        views.manager_workspace_document(sql, request.params.item_id, clock.now()),
      );
    },
  );
  app.get<{ Params: { item_id: string } }>('/managers/:item_id/workspace', async (request) => {
    const id = request.params.item_id;
    const result = database.read((sql) => ({
      ...views.manager_detail(sql, id),
      ...views.manager_workspace_document(sql, id, clock.now()),
      tickets: views.manager_tickets(sql, id),
    }));
    const conversation_history = conversation_start.read_manager_conversation_history(database, id);
    await add_work_attention(database, services.conversationSystem, result.tickets, [result]);
    return {
      ...result,
      conversation_history,
      artifacts: await list_artifact_entries(services.config.db_path, id),
    };
  });
  app.get<{ Params: { item_id: string } }>(
    '/managers/:item_id/conversation/start-values',
    async (request) => {
      const item = database.read((sql) => data.read_manager(sql, request.params.item_id).item);
      const values = conversation_start.manager_resolve(database, item);
      return {
        backend_key: values.backend_key,
        model: values.model,
        reasoning_effort: values.reasoning_effort,
      };
    },
  );
  app.post<{ Params: { item_id: string } }>(
    '/managers/:item_id/conversation/send',
    async (request, reply) => {
      const body = parse_owner_send_body(request.body, reply);
      if (body === null) return;
      const item = database.read((sql) => data.read_manager(sql, request.params.item_id).item);
      const current = conversation_start.read_agent_conversation(database, item.manager_agent_key);
      const overrides = {
        backend_key:
          body.backend_key === null ? null : require_conversation_backend_key(body.backend_key),
        model: body.model,
        reasoning_effort: body.reasoning_effort,
        reasoning_effort_is_set: body.provided_fields.has('reasoning_effort'),
      };
      if (
        current !== null &&
        overrides.backend_key !== null &&
        overrides.backend_key !== item.manager_launch_configuration.employee_backend
      )
        throw new PlannerError(
          'validation',
          'an existing Manager conversation cannot change backend',
          { conversation_id: current, backend_key: overrides.backend_key },
        );
      const target = { kind: 'manager' as const, id: item.id };
      const admission = video_send_admission(
        database,
        services.config.db_path,
        files,
        body.content,
        body.sender_message_id,
        { kind: 'manager', id: item.id, expected_conversation_id: body.conversation_id },
        target,
        services.identity(request).principal,
      );
      const delivered = await send_message(
        conversations(services),
        database,
        clock,
        services.identity(request),
        target,
        admission !== null
          ? (id) => admission.content(id)
          : (id) =>
              conversation_message_content(files, id, body.content, {
                sender_message_id: body.sender_message_id,
              }),
        body.mode,
        {
          admission,
          conversation_id: body.conversation_id,
          created_conversation_id: conversation_start.new_conversation_id(),
          runs_under: overrides,
          owner_read_through_sequence: body.owner_read_through_sequence,
          sender_message_id: body.sender_message_id,
          sent_at_unix_milliseconds: body.sent_at_unix_milliseconds,
        },
      );
      return { conversation_id: delivered.conversation_id, ...delivery_fate_json(delivered.fate) };
    },
  );
  app.post<{ Params: { item_id: string } }>(
    '/managers/:item_id/conversation/reset',
    async (request) =>
      manager_lifecycle_lock(request.params.item_id).run(async () => {
        const item = database.read((sql) => data.read_manager(sql, request.params.item_id).item);
        await conversation_start.reset_agent_conversation(
          conversations(services),
          database,
          item.manager_agent_key,
        );
        return { conversation_id: null };
      }),
  );
  app.delete<{ Params: { item_id: string } }>('/managers/:item_id', async (request) => ({
    ok: true,
    ...(await delete_manager(
      conversations(services),
      database,
      request.params.item_id,
      services.config.db_path,
    )),
  }));
  app.patch<{ Params: { item_id: string } }>('/managers/:item_id', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const body = request.body;
    for (const key of Object.keys(body))
      if (!['title', 'goal', 'recap', 'project_id', 'project'].includes(key))
        throw new PlannerError('validation', 'unknown item field', { field: key });
    if (!Object.keys(body).length)
      throw new PlannerError('validation', 'no item fields to update', {});
    const edits: Record<string, string | null> = {};
    for (const key of ['title', 'goal', 'recap']) if (key in body) edits[key] = body_str(body, key);
    return database.write((tx) => {
      if ('project' in body || 'project_id' in body)
        edits.project_id = resolveProject(
          tx,
          body_opt_str(body, 'project_id'),
          body_opt_str(body, 'project'),
          true,
        )!.id;
      data.update_manager(tx, request.params.item_id, { edits, clock });
      return views.manager_detail(tx, request.params.item_id);
    });
  });
  app.patch<{ Params: { item_id: string } }>(
    '/managers/:item_id/settled',
    async (request, reply) => {
      if (!dictionaryBody(request.body, reply)) return;
      if (
        Object.keys(request.body).join(',') !== 'settled' ||
        typeof request.body.settled !== 'boolean'
      )
        throw new PlannerError('validation', 'settled must be a boolean');
      const settled = request.body.settled;
      return database.write((tx) => {
        data.set_manager_settled(tx, request.params.item_id, {
          settled,
          principal: services.identity(request).principal,
          clock,
        });
        return views.manager_detail(tx, request.params.item_id);
      });
    },
  );
  // Only holder page intake retains numeric token kinds. Ordinary JSON routes stay unchanged.
  await app.register(async (pageApp) => {
    pageApp.removeAllContentTypeParsers();
    const pageParser = (
      _request: FastifyRequest,
      body: string | Buffer,
      done: (error: Error | null, value?: unknown) => void,
    ): void => {
      try {
        done(null, body.length ? parse_numeric_json(String(body)) : undefined);
      } catch (error) {
        done(
          Object.assign(error as Error, {
            code: 'FST_ERR_CTP_INVALID_JSON_BODY',
            jsonSource: String(body),
          }),
        );
      }
    };
    pageApp.addContentTypeParser(
      /^application\/(?:[^;]+\+)?json(?:;.*)?$/,
      { parseAs: 'string' },
      pageParser,
    );
    pageApp.addContentTypeParser('*', { parseAs: 'string' }, (request, body, done) => {
      if (!request.headers['content-type']) pageParser(request, body, done);
      else done(null, body);
    });
    for (const kind of ['chief', 'manager'] as const) {
      const path = kind === 'chief' ? '/chief/page' : '/managers/:item_id/page';
      pageApp.get<{ Params: { item_id?: string } }>(path, async (request) =>
        database.read((sql) => read_page(sql, kind === 'chief' ? null : request.params.item_id!)),
      );
      pageApp.patch<{ Params: { item_id?: string } }>(path, async (request, reply) => {
        // Numeric metadata is an internal object, but its wire value is a scalar.
        if (request.body instanceof JsonNumber) {
          return reply.code(422).send({
            detail: [
              {
                type: 'dict_type',
                loc: ['body'],
                msg: 'Input should be a valid dictionary',
                input: request.body,
              },
            ],
          });
        }
        if (!dictionaryBody(request.body, reply)) return;
        return database.write((tx) =>
          write_page(
            tx,
            kind === 'chief' ? null : request.params.item_id!,
            request.body as Record<string, unknown>,
          ),
        );
      });
      pageApp.get<{ Params: { item_id?: string } }>(path + '/readings', async (request) =>
        database.read((sql) =>
          page_readings(
            sql,
            kind === 'chief' ? null : request.params.item_id!,
            clock.now(),
            clock instanceof TestClock ? clock.isoformat() : undefined,
          ),
        ),
      );
    }
  });
};
