/**
 * The move's HTTP doors. Owner routes are an ordinary area. Executor routes authenticate the
 * machine like the update routes and admit only the move's target machine.
 */
import { createHash } from 'node:crypto';
import { constants } from 'node:fs';
import { open } from 'node:fs/promises';
import { resolve } from 'node:path';
import { Readable } from 'node:stream';
import type { FastifyPluginAsync, FastifyReply, FastifyRequest } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { requireOwner } from '../core/authority/identity.ts';
import type { Config } from '../core/config.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import { is_executor_link_connection } from '../core/http/executor_link.ts';
import { JsonNumber } from '../core/http/numeric_json.ts';
import { sendFile } from '../core/http/static.ts';
import { path_is_within } from '../core/path_containment.ts';
import { authenticate_machine_request } from '../executor/app_transport.ts';
import { SERVER_MOVE_MAX_REQUEST_BYTES } from './configuration.ts';
import type { HeldServerMove } from './held_service.ts';
import type { ServerMoveCoordinator, ServerMoveFileSource } from './service.ts';

export function create_server_move_owner_routes(
  coordinator: ServerMoveCoordinator,
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    app.post<{ Params: { machine_id: string } }>(
      '/machines/:machine_id/move',
      async (request, reply) => {
        requireOwner(services.identity(request));
        const body = request.body as Record<string, unknown> | null;
        // The JSON intake keeps exact numbers; the port is a plain integer.
        const port = body?.port instanceof JsonNumber ? body.port.value : body?.port;
        const view = coordinator.start(
          request.params.machine_id,
          body !== null && typeof body === 'object' ? { ...body, port } : body,
        );
        return reply.code(202).send(view);
      },
    );
    app.get('/server-move', async () => ({ move: coordinator.view() }));
  };
}

async function body(request: FastifyRequest): Promise<unknown> {
  const input = request.body instanceof Readable ? request.body : request.raw;
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const part of input.iterator({ destroyOnReturn: false })) {
    const chunk = Buffer.from(part as Uint8Array);
    size += chunk.length;
    if (size > SERVER_MOVE_MAX_REQUEST_BYTES) {
      input.resume();
      throw new HttpError(413, 'The move request body is too large.');
    }
    chunks.push(chunk);
  }
  if (size === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new PlannerError('validation', 'The move request body is not JSON.');
  }
}

/** Stream one listed file. Before the pause the digest covers exactly the bytes sent. */
async function send_state_file(
  reply: FastifyReply,
  data_directory: string,
  source: ServerMoveFileSource,
): Promise<FastifyReply> {
  const path = resolve(source.path);
  if (!path_is_within(resolve(data_directory), path))
    throw new PlannerError('not_found', 'This file is not listed.');
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW).catch((error) => {
    if (['ENOENT', 'ELOOP', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? ''))
      throw new PlannerError('not_found', 'This file is not present.');
    throw error;
  });
  let streaming = false;
  try {
    const info = await handle.stat();
    if (!info.isFile()) throw new PlannerError('not_found', 'This file is not a regular file.');
    let sha256 = source.sha256;
    if (sha256 === null) {
      const digest = createHash('sha256');
      if (info.size > 0)
        for await (const chunk of handle.createReadStream({
          start: 0,
          end: info.size - 1,
          autoClose: false,
        }))
          digest.update(chunk as Buffer);
      sha256 = digest.digest('hex');
    }
    reply
      .header('content-type', 'application/octet-stream')
      .header('content-length', String(info.size))
      .header('x-panels-file-sha256', sha256);
    if (info.size === 0) return reply.send(Buffer.alloc(0));
    streaming = true;
    return reply.send(handle.createReadStream({ start: 0, end: info.size - 1 }));
  } finally {
    if (!streaming) await handle.close();
  }
}

export function create_server_move_executor_routes(
  services: {
    readonly config: Config;
    readonly database: Database;
    readonly data_directory: string;
  },
  coordinator: ServerMoveCoordinator,
  held: HeldServerMove,
): FastifyPluginAsync {
  return async (app) => {
    app.removeAllContentTypeParsers();
    app.addContentTypeParser('*', (_request, payload, done) => done(null, payload));
    const machine = (request: FastifyRequest) => authenticate_machine_request(request, services);
    /** The held routes serve only the local executor link of this machine. */
    const local_machine = (request: FastifyRequest) => {
      if (!is_executor_link_connection(request.raw.socket))
        throw new HttpError(403, 'A held server answers its move on the local executor link only.');
      return machine(request);
    };
    const forbidden = async <T>(work: () => T | Promise<T>): Promise<T> => {
      try {
        return await work();
      } catch (error) {
        if (error instanceof PlannerError && error.code === 'agent_forbidden')
          throw new HttpError(403, error.message);
        throw error;
      }
    };
    type Move = { Params: { move_id: string } };
    const prefix = '/api/executor/server-move';
    const uncompressed = { config: { uncompressed: true } };

    app.get(prefix, async (request) => coordinator.instruction(machine(request)));
    app.get<Move>(`${prefix}/:move_id/package`, uncompressed, async (request, reply) => {
      const path = coordinator.package_path(machine(request), request.params.move_id);
      await sendFile(request, reply, path, { mime: 'application/gzip' });
      return reply;
    });
    app.get<Move>(`${prefix}/:move_id/files`, async (request) =>
      coordinator.files(machine(request), request.params.move_id),
    );
    app.get<Move>(`${prefix}/:move_id/file`, uncompressed, async (request, reply) => {
      const relative = new URL(request.url, 'http://executor').searchParams.get('path') ?? '';
      const source = coordinator.file(machine(request), request.params.move_id, relative);
      return send_state_file(reply, services.data_directory, source);
    });
    app.post<Move>(`${prefix}/:move_id/pause`, async (request) => {
      const machine_id = machine(request);
      await body(request);
      return coordinator.pause(machine_id, request.params.move_id);
    });
    app.post<Move>(`${prefix}/:move_id/report`, async (request) => {
      const machine_id = machine(request);
      return coordinator.report(machine_id, request.params.move_id, await body(request));
    });
    app.post<Move>(`${prefix}/:move_id/switched`, async (request) => {
      const machine_id = machine(request);
      await body(request);
      return coordinator.switched(machine_id, request.params.move_id);
    });

    app.post(`${prefix}/held/probe`, async (request) => {
      const machine_id = local_machine(request);
      await body(request);
      return forbidden(() => held.probe(machine_id));
    });
    app.post<{ Params: { job_id: string } }>(`${prefix}/held/probe/:job_id`, async (request) => {
      const machine_id = local_machine(request);
      const answer = await body(request);
      return forbidden(() => held.answer(machine_id, request.params.job_id, answer));
    });
    app.post(`${prefix}/held/commit`, async (request) => {
      const machine_id = local_machine(request);
      const commit = await body(request);
      return forbidden(() => held.commit(machine_id, commit));
    });
  };
}
