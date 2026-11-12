/** Ticket and Chief conversation routes. Runtime helpers own associations and execution. */
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { CHIEF, PlannerError } from '../core/contracts.ts';
import { dictionaryBody } from '../list_reads/http.ts';
import { require_conversation_backend_key } from '../conversation/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import {
  parse_owner_send_body,
  conversation_message_content,
  delivery_fate_json,
  video_send_admission,
} from '../conversation/http.ts';
import type { OwnerSendBody } from '../conversation/http.ts';
import { ConversationMessageFiles as FileConversationMessageFiles } from '../conversation/record/message_files.ts';
import * as conversation_start from '../runtime/conversation_start.ts';
import type {
  ConversationStartOverrides,
  ConversationStartValues,
} from '../runtime/logic/conversation_start_resolution.ts';
import { start_ready_worker_step } from '../runtime/worker_step_readiness_loop.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { CHIEF_SETTINGS_KEY } from '../worker_settings/service.ts';
import { send_message } from '../message_delivery/service.ts';
import { MessageRecordedToOwner } from '../message_delivery/contracts.ts';
import * as data from './data/index.ts';
import { ticket_detail, ticket_json } from './views.ts';
import { body_str, body_opt_str, parse_principal } from './routes.ts';
import { restart_worker } from './worker_restart.ts';
import type { EmployeeLaunchConfiguration } from './contracts.ts';
function conversations(services: AreaServices): ConversationSystem {
  if (!services.conversationSystem) throw new Error('conversation system is unavailable');
  return services.conversationSystem;
}
function start_values_json(values: ConversationStartValues): Record<string, unknown> {
  return {
    backend_key: values.backend_key,
    model: values.model,
    reasoning_effort: values.reasoning_effort,
    workspace_folder:
      values.workspace_path_policy === 'executor_default' ? null : values.workspace_folder,
    workspace_path_policy: values.workspace_path_policy,
  };
}
function runs_under(body: OwnerSendBody): ConversationStartOverrides {
  return {
    backend_key:
      body.backend_key === null ? null : require_conversation_backend_key(body.backend_key),
    model: body.model,
    reasoning_effort: body.reasoning_effort,
    reasoning_effort_is_set: body.provided_fields.has('reasoning_effort'),
  };
}
function resolve_configuration(
  services: AreaServices,
  backend: string,
  model: string,
  effort: string | null,
): EmployeeLaunchConfiguration {
  const employee_backend = require_conversation_backend_key(backend);
  services.database.read((sql) => {
    const row = sql.get<{
      enabled: number;
    }>('SELECT enabled FROM backend_model_enablement WHERE backend_key=? AND model_id=?', [
      employee_backend,
      model,
    ]);
    if (row && !row.enabled) {
      throw new PlannerError(
        'validation',
        `Model "${model}" is disabled. Choose an enabled model.`,
        { employee_backend, employee_launch_model: model },
      );
    }
  });
  return {
    employee_backend,
    employee_launch_model: model,
    employee_launch_reasoning_effort: effort,
  };
}
export const conversation_routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  const files = new FileConversationMessageFiles(services.config.db_path);
  app.put<{
    Params: {
      ticket_id: string;
    };
  }>('/tickets/:ticket_id/employee-configuration', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const raw = request.body;
    const keys = ['employee_backend', 'employee_launch_model', 'employee_launch_reasoning_effort'];
    if (Object.keys(raw).sort().join(',') !== keys.sort().join(',')) {
      throw new PlannerError(
        'validation',
        'Employee configuration update requires the exact complete body',
      );
    }
    const resolved = resolve_configuration(
      services,
      body_str(raw, 'employee_backend'),
      body_str(raw, 'employee_launch_model'),
      body_opt_str(raw, 'employee_launch_reasoning_effort'),
    );
    const now = services.clock.nowUnix();
    const ticket = services.database.write((tx) =>
      data.write_employee_configuration(tx, request.params.ticket_id, { ...resolved, now }),
    );
    return services.database.read((sql) =>
      ticket_detail(sql, ticket.id, now, services.config.db_path),
    );
  });
  app.post<{
    Params: {
      ticket_id: string;
    };
  }>('/tickets/:ticket_id/restart-worker', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const backend = body_opt_str(request.body, 'employee_backend');
    const model = body_opt_str(request.body, 'employee_launch_model');
    if ((backend === null) !== (model === null)) {
      throw new PlannerError(
        'validation',
        'a launch configuration needs both a backend and a model',
      );
    }
    const effort = body_opt_str(request.body, 'employee_launch_reasoning_effort');
    const resolved =
      backend !== null && model !== null
        ? resolve_configuration(services, backend, model, effort)
        : null;
    return restart_worker(
      conversations(services),
      services.database,
      services.identity(request).principal,
      request.params.ticket_id,
      {
        now: () => services.clock.nowUnix(),
        write_employee_configuration:
          resolved === null
            ? null
            : (tx, now) => {
                data.write_employee_configuration(tx, request.params.ticket_id, {
                  ...resolved,
                  now,
                });
              },
        start_worker_step: () =>
          start_ready_worker_step(request.params.ticket_id, {
            connect_database: () => services.database,
            conversation_system: conversations(services),
            worker_type_registry: configured_worker_type_registry(),
            now: () => services.clock.nowUnix(),
          }),
      },
    );
  });
  app.delete<{
    Params: {
      ticket_id: string;
    };
  }>('/tickets/:ticket_id', async (request, reply) => {
    const query = new URL(request.url, 'http://panels.invalid').searchParams;
    const raw_force = query.getAll('force').at(-1);
    let force = false;
    if (raw_force !== undefined) {
      if (['true', 't', 'yes', 'y', 'on', '1'].includes(raw_force.toLowerCase())) force = true;
      else if (!['false', 'f', 'no', 'n', 'off', '0'].includes(raw_force.toLowerCase())) {
        reply.code(422).send({
          detail: [
            {
              type: 'bool_parsing',
              loc: ['query', 'force'],
              msg: 'Input should be a valid boolean, unable to interpret input',
              input: raw_force,
            },
          ],
        });
        return;
      }
    }
    const principal = services.identity(request).principal;
    const even_while_running = force || principal.kind === 'manager';
    const ticket = services.database.read((sql) => data.read_ticket(sql, request.params.ticket_id));
    if (ticket.conversation_id !== null) {
      if (even_while_running) await conversations(services).kill(ticket.conversation_id);
      else if (await conversations(services).is_running(ticket.conversation_id)) {
        throw new PlannerError('already_running', 'ticket conversation is still running', {
          ticket_id: ticket.id,
        });
      }
    }
    const deleted = services.database.write((tx) =>
      data.delete_ticket(tx, ticket.id, {
        principal,
        now: services.clock.nowUnix(),
        even_while_running,
      }),
    );
    return { ok: true, ...deleted };
  });
  app.get('/chief/conversation', async () => ({
    conversation_id: conversation_start.read_agent_conversation(
      services.database,
      CHIEF_SETTINGS_KEY,
    ),
  }));
  app.get('/chief/conversation/start-values', async () =>
    start_values_json(conversation_start.agent_resolve(services.database)),
  );
  app.post('/chief/conversation/reset', async () => {
    await conversation_start.reset_agent_conversation(
      conversations(services),
      services.database,
      CHIEF_SETTINGS_KEY,
    );
    return { conversation_id: null };
  });
  app.get<{
    Params: {
      ticket_id: string;
    };
  }>('/tickets/:ticket_id/conversation/start-values', async (request) => {
    const ticket = services.database.read((sql) => data.read_ticket(sql, request.params.ticket_id));
    return start_values_json(conversation_start.worker_resolve(services.database, ticket));
  });
  app.post<{
    Params: {
      ticket_id: string;
    };
  }>('/tickets/:ticket_id/conversation/reset', async (request) => {
    const now = services.clock.nowUnix();
    await conversation_start.reset_ticket_conversation(
      conversations(services),
      services.database,
      request.params.ticket_id,
      { now },
    );
    return services.database.read((sql) =>
      ticket_json(data.read_ticket(sql, request.params.ticket_id), now),
    );
  });
  for (const kind of ['chief', 'ticket'] as const) {
    const path =
      kind === 'chief' ? '/chief/conversation/send' : '/tickets/:ticket_id/conversation/send';
    app.post<{
      Params: {
        ticket_id?: string;
      };
    }>(path, async (request, reply) => {
      const body = parse_owner_send_body(request.body, reply);
      if (body === null) return;
      const created_conversation_id = conversation_start.new_conversation_id();
      // Python resolves Chief launch overrides before attachment admission.
      const chief_overrides = kind === 'chief' ? runs_under(body) : null;
      const principal = services.identity(request).principal;
      video_send_admission(body.content, body.sender_message_id, principal);
      const target =
        kind === 'chief' ? CHIEF : { kind: 'ticket' as const, id: request.params.ticket_id! };
      const delivered = await send_message(
        conversations(services),
        services.database,
        services.clock,
        services.identity(request),
        target,
        (id) =>
          conversation_message_content(files, id, body.content, {
            sender_message_id: body.sender_message_id,
          }),
        body.mode,
        {
          conversation_id: body.conversation_id,
          created_conversation_id,
          runs_under: chief_overrides ?? runs_under(body),
          owner_read_through_sequence: body.owner_read_through_sequence,
          sender_message_id: body.sender_message_id,
          sent_at_unix_milliseconds: body.sent_at_unix_milliseconds,
        },
      );
      return { conversation_id: delivered.conversation_id, ...delivery_fate_json(delivered.fate) };
    });
  }
  app.post<{
    Params: {
      ticket_id: string;
    };
  }>('/tickets/:ticket_id/request-help', async (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const body = request.body;
    if (Object.keys(body).some((key) => key !== 'message' && key !== 'recipient')) {
      throw new PlannerError('validation', 'unknown help request field');
    }
    const message = body_str(body, 'message');
    if (!message.trim()) throw new PlannerError('validation', 'help message must not be empty');
    const ticket = services.database.read((sql) => data.read_ticket(sql, request.params.ticket_id));
    const recipient = parse_principal(body.recipient, 'recipient', true) ?? ticket.ceiling_holder;
    const delivered = await send_message(
      conversations(services),
      services.database,
      services.clock,
      services.identity(request),
      recipient,
      message,
    );
    return {
      target: recipient,
      conversation_id: delivered.conversation_id,
      ...(delivered.fate instanceof MessageRecordedToOwner
        ? { fate: 'recorded' }
        : delivery_fate_json(delivered.fate)),
    };
  });
};
