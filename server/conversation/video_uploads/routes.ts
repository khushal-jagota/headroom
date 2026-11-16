/** Owner upload transport. Bytes stream to the same durable service used by adoption. */
import type { FastifyPluginAsync } from 'fastify';
import type { Readable } from 'node:stream';
import type { AreaServices } from '../../core/authority/register.ts';
import { requireOwner } from '../../core/authority/identity.ts';
import { HttpError } from '../../core/http/errors.ts';
import { numeric_value } from '../../core/http/numeric_json.ts';
import { read_ticket } from '../../tickets/data/index.ts';
import { read_manager } from '../../managers/data.ts';
import { Fields, body_fields, object } from '../http/intake.ts';
import { mutable_conversation, unassociated_send } from '../routes.ts';
import type { ConversationHttpRuntime } from '../routes.ts';
import { VideoUploadError } from './contracts.ts';
import type { ReserveVideoUpload, UploadDestination } from './contracts.ts';
import type { VideoUploadService } from './service.ts';

function reserve_body(value: unknown): ReserveVideoUpload {
  const fields = body_fields(value);
  const raw = fields.body.destination;
  let destination: UploadDestination | null = null;
  if (!('destination' in fields.body))
    fields.issue('missing', ['destination'], 'Field required', fields.body);
  else {
    const nested = new Fields(raw, ['body', 'destination']);
    if (object(raw)) {
      const kind = nested.choice('kind', ['chief', 'manager', 'ticket', 'conversation']);
      for (const issue of nested.issues) if (issue.type === 'enum') issue.type = 'literal_error';
      const id = nested.text('id');
      const expected_conversation_id = nested.text('expected_conversation_id', true);
      nested.extras(['kind', 'id', 'expected_conversation_id']);
      destination = { kind: kind as UploadDestination['kind'], id: id!, expected_conversation_id };
    }
    fields.issues.push(...nested.issues);
  }
  const filename = fields.text('filename');
  const number = numeric_value(fields.body.byte_count);
  const count_fields = new Fields({
    ...fields.body,
    ...(number === undefined ? {} : { byte_count: number }),
  });
  const byte_count = count_fields.integer('byte_count');
  fields.issues.push(...count_fields.issues);
  if (byte_count !== null && byte_count <= 0)
    fields.issue(
      'greater_than',
      ['byte_count'],
      'Input should be greater than 0',
      fields.body.byte_count,
      { gt: 0 },
    );
  fields.extras(['destination', 'filename', 'byte_count']);
  fields.finish();
  return { destination: destination!, filename: filename!, byte_count: byte_count! };
}
async function upload_answer<T>(operation: () => T | Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    if (error instanceof VideoUploadError) throw new HttpError(error.status_code, error.message);
    throw error;
  }
}
export function createVideoUploadRoutes(
  service: VideoUploadService,
  runtime: ConversationHttpRuntime | null,
  signal?: AbortSignal,
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    app.addHook('preHandler', async (request) => requireOwner(services.identity(request)));
    const authorize = (destination: UploadDestination) => {
      if (destination.kind === 'ticket')
        services.database.read((sql) => read_ticket(sql, destination.id));
      else if (destination.kind === 'manager')
        services.database.read((sql) => read_manager(sql, destination.id));
      else if (destination.kind === 'chief') {
        if (destination.id !== 'chief') throw new HttpError(422, 'invalid Chief destination');
      } else {
        if (runtime === null) throw new HttpError(503, 'conversation system is unavailable');
        mutable_conversation(runtime, destination.id);
        unassociated_send(runtime, destination.id);
        if (runtime.record.store.read_conversation(destination.id) === null)
          throw new HttpError(404, 'no such conversation');
      }
    };
    const checked_status = (id: string) => {
      const status = service.status(id);
      authorize(status.destination);
      return status;
    };
    app.post('', async (request) =>
      upload_answer(() => {
        const body = reserve_body(request.body);
        authorize(body.destination);
        return service.reserve(body);
      }),
    );
    app.get<{ Params: { upload_id: string } }>('/:upload_id', async (request) =>
      upload_answer(() => checked_status(request.params.upload_id)),
    );
    app.delete<{ Params: { upload_id: string } }>('/:upload_id', async (request, reply) => {
      await upload_answer(() => {
        checked_status(request.params.upload_id);
        service.cancel(request.params.upload_id);
      });
      return reply.code(204).send();
    });
    // This encapsulated parser affects only the byte route, including JSON-labelled bytes.
    await app.register(async (bytes) => {
      bytes.removeAllContentTypeParsers();
      bytes.addContentTypeParser('*', (_request, payload, done) => done(null, payload));
      bytes.put<{ Params: { upload_id: string } }>('/:upload_id/bytes', async (request) =>
        upload_answer(async () => {
          checked_status(request.params.upload_id);
          const stream = request.body as Readable | undefined;
          const empty = async function* (): AsyncGenerator<Uint8Array> {};
          const abort = () => {
            stream?.destroy();
          };
          signal?.addEventListener('abort', abort, { once: true });
          if (signal?.aborted) abort();
          try {
            return await service.transfer(request.params.upload_id, stream ?? empty());
          } finally {
            signal?.removeEventListener('abort', abort);
          }
        }),
      );
    });
  };
}
