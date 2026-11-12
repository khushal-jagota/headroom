import { requestOrigin } from '../core/http/origin.ts';
import { mimeType } from './mime.ts';
import { open } from 'node:fs/promises';
import { dirname, join, basename } from 'node:path';
import { realpathSync } from 'node:fs';
import { createHash } from 'node:crypto';
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { PlannerError } from '../core/contracts.ts';
import { HttpError } from '../core/http/errors.ts';
import { sendFile } from '../core/http/static.ts';
import {
  dictionaryBody,
  integerQuery,
  scalarQueries,
  QueryIntegerError,
} from '../list_reads/http.ts';
import { body_str } from '../tickets/routes.ts';
import type { OwnerKind } from './contracts.ts';
import {
  resolveFile,
  resolveHtml,
  unsafeEncoded,
  safeId,
  safeRelative,
  filesRoot,
} from './logic/paths.ts';
import { ownerRoot, writeText, deleteFile } from './managed_files.ts';
import { inventory, pythonJson } from './catalog/inventory.ts';
import { linked } from './catalog/read.ts';
import { PAGE_SIZE, PAGE_MAX_BYTES } from './catalog/config.ts';
const INLINE = new Set([
  'image/avif',
  'image/bmp',
  'image/gif',
  'image/jpeg',
  'image/png',
  'image/webp',
  'audio/aac',
  'audio/flac',
  'audio/mp4',
  'audio/mpeg',
  'audio/ogg',
  'audio/wav',
  'audio/webm',
  'audio/x-wav',
  'video/mp4',
  'video/ogg',
  'video/quicktime',
  'video/webm',
  'video/x-m4v',
]);
const REUSE_MEMORY_BOUND_BYTES = 2 * 1024 * 1024;
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  function requireGoal(id: string): void {
    services.database.read((sql) => {
      if (!sql.get('SELECT 1 FROM goals WHERE id=?', [id]))
        throw new PlannerError('not_found', 'goal not found', {});
    });
  }
  for (const family of ['tickets', 'managers', 'sprint-items', 'goals'] as const) {
    const kind: OwnerKind =
      family === 'tickets' ? 'ticket' : family === 'goals' ? 'goal' : 'manager';
    const parameter = kind === 'ticket' ? 'ticket_id' : kind === 'goal' ? 'goal_id' : 'manager_id';
    const label = kind === 'manager' ? 'Manager' : kind;
    app.get(`/files/${family}/:${parameter}/*`, async (request, reply) => {
      const params = request.params as Record<string, string>,
        owner = params[parameter]!;
      const missing = () => new PlannerError('not_found', `${label} file not found`, {});
      if (unsafeEncoded.test((request.raw.url ?? '').split('?')[0]!)) throw missing();
      if (kind === 'goal') requireGoal(owner);
      let file: string;
      try {
        const root = request.headers['x-panels-artifact-root'];
        file =
          typeof root === 'string'
            ? resolveHtml(services.config.db_path, root, (request.raw.url ?? '').split('?')[0]!)
            : resolveFile(services.config.db_path, kind, owner, params['*']!);
      } catch {
        throw missing();
      }
      const mime = mimeType(file);
      const filename = basename(file),
        encoded = encodeURIComponent(filename).replace(
          /[!'()*]/g,
          (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase(),
        );
      const disposition = INLINE.has(mime) ? 'inline' : 'attachment';
      reply
        .header(
          'content-disposition',
          encoded === filename
            ? `${disposition}; filename="${filename.replaceAll('\\', '\\\\').replaceAll('"', '\\"')}"`
            : `${disposition}; filename*=utf-8''${encoded}`,
        )
        .header('x-content-type-options', 'nosniff')
        .header('cache-control', 'private, no-cache');
      const contentType = mime.startsWith('text/') ? mime + '; charset=utf-8' : mime;
      if (!request.headers.range && !/^(audio|video)\//.test(mime)) {
        const handle = await open(file, 'r');
        try {
          const info = await handle.stat(),
            bytes = Buffer.alloc(REUSE_MEMORY_BOUND_BYTES + 1);
          // Read through EOF or one byte over the bound, even if the file grows.
          let length = 0;
          while (length < bytes.length) {
            const read = await handle.read(bytes, length, bytes.length - length, null);
            if (!read.bytesRead) break;
            length += read.bytesRead;
          }
          if (length <= REUSE_MEMORY_BOUND_BYTES) {
            const body = bytes.subarray(0, length),
              etag = '"' + createHash('sha256').update(body).digest('hex') + '"';
            reply.header('etag', etag);
            const named = request.headers['if-none-match'];
            if (
              named
                ?.split(',')
                .map((v) => v.trim().replace(/^W\//, ''))
                .some((v) => v === '*' || v === etag)
            ) {
              reply.removeHeader('content-disposition').removeHeader('x-content-type-options');
              reply.code(304).send();
              return;
            }
            reply
              .header('content-type', contentType)
              .header('last-modified', info.mtime.toUTCString())
              .header('accept-ranges', 'bytes')
              .send(body);
            return;
          }
        } finally {
          await handle.close();
        }
      }
      await sendFile(request, reply, file, {
        mime: contentType,
        rangeErrorHeaders: ['cache-control', 'content-disposition', 'x-content-type-options'],
      });
    });
    if (kind === 'ticket') continue;
    for (const method of ['PUT', 'DELETE'] as const)
      app.route({
        method,
        url: `/files/${family}/:${parameter}/*`,
        handler: async (request, reply) => {
          const params = request.params as Record<string, string>,
            owner = params[parameter]!,
            path = params['*']!;
          if (unsafeEncoded.test((request.raw.url ?? '').split('?')[0]!))
            throw new PlannerError('not_found', `${label} file not found`, {});
          if (method === 'PUT' && !dictionaryBody(request.body, reply)) return;
          const content =
            method === 'PUT' ? body_str(request.body as Record<string, unknown>, 'content') : '';
          const work = () => {
            if (kind === 'goal') requireGoal(owner);
            const root = ownerRoot(services.config.db_path, kind, owner, method === 'PUT');
            if (method === 'PUT') writeText(root!, path, content, label);
            else deleteFile(root, path, label);
          };
          if (kind === 'goal') services.database.write(work);
          else work();
          if (method === 'DELETE') return { ok: true, [parameter]: owner, path };
          return {
            [parameter]: owner,
            path,
            url: `/files/${kind === 'manager' ? 'managers' : 'goals'}/${owner}/${
              kind === 'goal'
                ? path
                    .split('/')
                    .map((p) =>
                      encodeURIComponent(p).replace(
                        /[!'()*]/g,
                        (c) => '%' + c.charCodeAt(0).toString(16).toUpperCase(),
                      ),
                    )
                    .join('/')
                : path
            }`,
          };
        },
      });
  }
  app.get('/api/artifact-catalog/:kind/:owner_id', async (request) => {
    const { kind, owner_id: owner } = request.params as { kind: OwnerKind; owner_id: string };
    if (!['ticket', 'manager', 'goal'].includes(kind))
      throw new HttpError(422, [
        {
          type: 'literal_error',
          loc: ['path', 'kind'],
          msg: "Input should be 'ticket', 'manager' or 'goal'",
          input: kind,
          ctx: { expected: "'ticket', 'manager' or 'goal'" },
        },
      ]);
    const query = scalarQueries(request.query as Record<string, string>),
      directory = query.directory ?? '';
    let offset: number, limit: number;
    try {
      offset = integerQuery(query.offset, 'offset') ?? 0;
      limit = integerQuery(query.limit, 'limit') ?? PAGE_SIZE;
    } catch (error) {
      if (error instanceof QueryIntegerError) throw new HttpError(422, [error.payload()]);
      throw error;
    }
    try {
      safeId(owner);
      if (directory) safeRelative(directory);
    } catch {
      throw new PlannerError('validation', 'invalid artifact directory', {});
    }
    if (offset < 0 || limit < 1 || limit > PAGE_SIZE)
      throw new PlannerError('validation', 'invalid artifact page', {});
    if (kind === 'goal') requireGoal(owner);
    if (kind === 'ticket')
      services.database.read((sql) => {
        if (!sql.get('SELECT 1 FROM tickets WHERE id=?', [owner]))
          throw new PlannerError('not_found', 'ticket not found', {});
      });
    const configured = services.config.db_path,
      db = join(realpathSync(dirname(configured)), basename(configured));
    const kept = await inventory(
      join(filesRoot(db, kind), owner),
      `/files/${kind === 'manager' ? 'managers' : kind + 's'}/${owner}/`,
    );
    const rows = Object.hasOwn(kept.directories, directory) ? kept.directories[directory]! : [];
    return services.database.read((sql) => {
      let total = rows.length,
        window = rows.slice(offset, offset + limit),
        revision = '0';
      if (!directory && kind === 'ticket') {
        const links = linked(
          sql,
          owner,
          requestOrigin(request),
          kept.seen,
          Math.max(0, offset - rows.length),
          Math.max(0, limit - window.length),
        );
        total += links.total;
        window = [...window, ...links.entries];
        revision = String(
          sql.get<{ revision: number }>(
            'SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?',
            ['ticket-artifact-links', owner],
          )?.revision ?? 0,
        );
      }
      const entries = [];
      let used = 0;
      for (const row of window) {
        const size = Buffer.byteLength(pythonJson(row));
        if (used + size > PAGE_MAX_BYTES) {
          if (!entries.length)
            throw new PlannerError('validation', 'artifact metadata exceeds page budget', {});
          break;
        }
        used += size;
        entries.push(row);
      }
      return { revision: kept.revision + ':' + revision, directory, offset, total, entries };
    });
  });
};
