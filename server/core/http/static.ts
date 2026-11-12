import { open, readFile, stat, realpath } from 'node:fs/promises';
import { createReadStream } from 'node:fs';
import { resolve, sep, extname, join } from 'node:path';
import { Readable } from 'node:stream';
import { createHash, randomBytes } from 'node:crypto';
import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
// Starlette FileResponse emits chunks of this size. A final small chunk retains
// the ordinary (non-streaming) gzip length and threshold behavior.
const FILE_CHUNK_BYTES = 64 * 1024;
async function readSlice(file: string, start: number, end: number): Promise<Buffer> {
  const handle = await open(file, 'r');
  try {
    const bytes = Buffer.alloc(end - start);
    let length = 0;
    while (length < bytes.length) {
      const result = await handle.read(bytes, length, bytes.length - length, start + length);
      if (!result.bytesRead) break;
      length += result.bytesRead;
    }
    return bytes.subarray(0, length);
  } finally {
    await handle.close();
  }
}
const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/vnd.microsoft.icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.webmanifest': 'application/manifest+json',
  '.map': 'application/json',
  '.mp4': 'video/mp4',
  '.pdf': 'application/pdf',
};
export function indexHtml(root: string, sha: string | null): Promise<string> {
  return readFile(join(root, 'web/dist/index.html'), 'utf8')
    .catch(
      () =>
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Panels</title></head><body><div id="app" data-svelte-app>web/dist is missing; run npm --prefix web run build.</div></body></html>',
    )
    .then((document) => {
      const escaped = (sha ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#x27;');
      return document.replace(
        '</head>',
        `<meta name="panels-app-sha" content="${escaped}"></head>`,
      );
    });
}
function detail(reply: FastifyReply, status: number, text: string): void {
  reply.code(status).send({ detail: text });
}
function ranges(raw: string, size: number): [number, number][] {
  const [unit, spec] = raw.split('=');
  if (unit === undefined || spec === undefined) throw new Error('Malformed range header');
  if (unit.trim().toLowerCase() !== 'bytes') throw new Error('Only support bytes range');
  const result: [number, number][] = [];
  for (const part of spec.split(',')) {
    const match = /^\s*([+-]?\d*)\s*-\s*([+-]?\d*)\s*$/.exec(part);
    if (!match || (!match[1] && !match[2])) continue;
    const start = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]));
    const end = match[1] && match[2] && Number(match[2]) < size ? Number(match[2]) + 1 : size;
    result.push([start, end]);
  }
  if (!result.length) throw new Error('Range header: range must be requested');
  if (result.some(([start]) => !(0 <= start && start < size))) throw new RangeError();
  if (result.some(([start, end]) => start > end))
    throw new Error('Range header: start must be less than end');
  result.sort((a, b) => a[0] - b[0]);
  for (let i = 1; i < result.length;) {
    if (result[i]![0] <= result[i - 1]![1]) {
      result[i - 1]![1] = Math.max(result[i - 1]![1], result[i]![1]);
      result.splice(i, 1);
    } else i++;
  }
  return result;
}
export async function sendFile(
  request: FastifyRequest,
  reply: FastifyReply,
  file: string,
  options: {
    cache?: string;
    mime?: string;
    conditional?: boolean;
    rangeErrorHeaders?: readonly string[];
  } = {},
): Promise<void> {
  let info;
  try {
    info = await stat(file);
  } catch {
    detail(reply, 404, 'Not Found');
    return;
  }
  if (!info.isFile()) {
    detail(reply, 404, 'Not Found');
    return;
  }
  const modified = info.mtime.toUTCString();
  const precise = await stat(file, { bigint: true });
  const mtime = Number(precise.mtimeNs / 1000000000n) + Number(precise.mtimeNs % 1000000000n) / 1e9;
  const mtimeText = Number.isInteger(mtime) ? `${mtime}.0` : String(mtime);
  const etag = `"${createHash('md5').update(`${mtimeText}-${info.size}`).digest('hex')}"`;
  const mime = options.mime ?? MIME[extname(file)] ?? 'application/octet-stream';
  reply
    .header('content-type', mime)
    .header('accept-ranges', 'bytes')
    .header('content-length', info.size)
    .header('last-modified', modified)
    .header('etag', etag);
  if (options.cache) reply.header('cache-control', options.cache);
  if (options.conditional) {
    const match = request.headers['if-none-match'];
    const since = request.headers['if-modified-since'];
    if (
      (typeof match === 'string' &&
        match.split(',').some((v) => v.trim().replace(/^W\//, '') === etag)) ||
      (!match && typeof since === 'string' && Date.parse(since) >= Date.parse(modified))
    ) {
      for (const header of ['content-length', 'content-type', 'last-modified', 'accept-ranges'])
        reply.removeHeader(header);
      reply.code(304).send();
      return;
    }
  }
  const range = request.headers.range;
  const ifRange = request.headers['if-range'];
  if (range && (!ifRange || ifRange === etag || ifRange === modified)) {
    let parts;
    try {
      parts = ranges(range, info.size);
    } catch (error) {
      for (const name of ['content-length', 'etag', 'last-modified', 'accept-ranges'])
        reply.removeHeader(name);
      for (const name of options.rangeErrorHeaders ?? []) reply.removeHeader(name);
      if (options.cache && options.conditional)
        reply.header('cache-control', 'no-cache, must-revalidate');
      if (error instanceof RangeError) {
        reply.header('content-range', `bytes */${info.size}`);
        reply.header('content-type', 'text/plain; charset=utf-8').code(416).send('');
      } else {
        reply
          .header('content-type', 'text/plain; charset=utf-8')
          .code(400)
          .send((error as Error).message);
      }
      return;
    }
    if (parts.length === 1) {
      const [start, end] = parts[0]!;
      reply
        .code(206)
        .header('content-range', `bytes ${start}-${end - 1}/${info.size}`)
        .header('content-length', end - start)
        .send(
          request.method === 'HEAD' || end === start
            ? Buffer.alloc(0)
            : end - start < FILE_CHUNK_BYTES
              ? await readSlice(file, start, end)
              : createReadStream(file, { start, end: end - 1, highWaterMark: FILE_CHUNK_BYTES }),
        );
      await reply;
      return;
    }
    const bytes = await readFile(file);
    const boundary = randomBytes(13).toString('hex');
    const chunks = parts.flatMap(([start, end]) => [
      Buffer.from(
        `--${boundary}\r\nContent-Type: ${mime}\r\nContent-Range: bytes ${start}-${end - 1}/${info.size}\r\n\r\n`,
      ),
      bytes.subarray(start, end),
      Buffer.from('\r\n'),
    ]);
    chunks.push(Buffer.from(`--${boundary}--`));
    const body = Buffer.concat(chunks);
    reply
      .code(206)
      .header('content-type', `multipart/byteranges; boundary=${boundary}`)
      .header('content-length', body.length);
    await reply.send(Readable.from(chunks));
    return;
  }
  await reply.send(
    request.method === 'HEAD'
      ? Buffer.alloc(0)
      : info.size < FILE_CHUNK_BYTES
        ? await readFile(file)
        : createReadStream(file, { highWaterMark: FILE_CHUNK_BYTES }),
  );
}
export async function mountStatic(
  app: FastifyInstance,
  prefix: string,
  root: string,
  web = false,
): Promise<void> {
  app.route({
    method: ['GET', 'HEAD'],
    url: `${prefix}/*`,
    handler: async (request, reply) => {
      const raw = (
        request.params as {
          '*': string;
        }
      )['*'];
      const path = resolve(root, raw);
      try {
        const realRoot = await realpath(root);
        let file = await realpath(path);
        if (file !== realRoot && !file.startsWith(realRoot + sep)) {
          detail(reply, 404, 'Not Found');
          return;
        }
        const info = await stat(file);
        if (info.isDirectory() && web) {
          file = await realpath(join(file, 'index.html'));
          if (file !== realRoot && !file.startsWith(realRoot + sep)) {
            detail(reply, 404, 'Not Found');
            return;
          }
          if (!(await stat(file)).isFile()) {
            detail(reply, 404, 'Not Found');
            return;
          }
          if (!request.url.split('?')[0]!.endsWith('/')) {
            const url = new URL(request.url, `${request.protocol}://${request.host}`);
            url.pathname += '/';
            reply.code(307).header('location', url.href).send('');
            return;
          }
        }
        const cache = web
          ? /^assets\/[^/]+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9.]+$/.test(raw)
            ? 'public, max-age=31536000, immutable'
            : 'no-cache, must-revalidate'
          : undefined;
        await sendFile(request, reply, file, { cache, conditional: true });
      } catch {
        detail(reply, 404, 'Not Found');
      }
    },
  });
}
