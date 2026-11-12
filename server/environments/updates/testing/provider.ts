/** Export-only provider proof through a scripted fetch transport and real ZIP/files. */
import { createReadStream, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';
import { GitHubUpdateProvider } from '../provider.ts';
import { canonical_json } from '../codec.ts';
import { numeric_value } from '../../../core/http/numeric_json.ts';
interface Reply {
  status?: number;
  json?: unknown;
  file?: string;
  failure?: boolean;
  body_failure?: boolean;
  chunks?: string[];
  chunk_ms?: number;
  stall?: boolean;
}
interface Case {
  name: string;
  action: 'available' | 'available_order' | 'publication' | 'download';
  run_id: number;
  artifact_id: number;
  responses: Record<string, Reply>;
  inactivity_ms?: number;
}
interface Request {
  root: string;
  token_file: string;
  cases: Case[];
}
export async function probe_update_provider(request: Request) {
  const results: unknown[] = [];
  for (const [index, item] of request.cases.entries()) {
    const seen: unknown[] = [];
    const original_timeout = globalThis.setTimeout;
    const started = performance.now();
    let aborted = false,
      production_timeout = 0;
    const inactivity_ms = numeric_value(item.inactivity_ms);
    if (inactivity_ms !== undefined) {
      // Scale only the production 60s network deadline in this isolated RPC.
      globalThis.setTimeout = ((...[handler, delay, ...args]: Parameters<typeof setTimeout>) => {
        if (delay === 60_000) production_timeout = delay;
        return original_timeout(handler, delay === 60_000 ? inactivity_ms : delay, ...args);
      }) as typeof setTimeout;
    }
    const provider = new GitHubUpdateProvider('owner/repository', request.token_file, {
      temporary_directory: request.root,
      fetch: async (input, options) => {
        const url = new URL(input instanceof globalThis.Request ? input.url : input.toString());
        const path = url.pathname.replace('/repos/owner/repository/', '');
        seen.push({
          path,
          query: Object.fromEntries(url.searchParams),
          authorized:
            new Headers(options?.headers).get('Authorization') ===
            `Bearer ${readFileSync(request.token_file, 'utf8').trim()}`,
        });
        const reply = item.responses[path];
        if (reply === undefined) throw new Error(`unexpected scripted path ${path}`);
        options?.signal?.throwIfAborted();
        if (reply.failure) throw new Error(readFileSync(request.token_file, 'utf8'));
        if (reply.chunks !== undefined) {
          let next = 0;
          let timer: NodeJS.Timeout | undefined;
          let release: (() => void) | undefined;
          let abort: (() => void) | undefined;
          const cleanup = () => {
            clearTimeout(timer);
            options?.signal?.removeEventListener('abort', abort!);
          };
          const body = new ReadableStream<Uint8Array>(
            {
              start(controller) {
                abort = () => {
                  aborted = true;
                  cleanup();
                  controller.error(options?.signal?.reason);
                  release?.();
                };
                options?.signal?.addEventListener('abort', abort, { once: true });
              },
              async pull(controller) {
                if (next === reply.chunks!.length && !reply.stall) {
                  cleanup();
                  controller.close();
                  return;
                }
                await new Promise<void>((resolve) => {
                  release = resolve;
                  if (next < reply.chunks!.length)
                    timer = original_timeout(resolve, numeric_value(reply.chunk_ms) ?? 0);
                });
                if (!options?.signal?.aborted)
                  controller.enqueue(Buffer.from(reply.chunks![next++]!));
              },
              cancel: cleanup,
            },
            { highWaterMark: 0 },
          );
          return new Response(body, { status: numeric_value(reply.status) ?? 200 });
        }
        const file = reply.file === undefined ? null : createReadStream(reply.file);
        if (file !== null) {
          const abort = () => file.destroy(options?.signal?.reason);
          options?.signal?.addEventListener('abort', abort, { once: true });
          file.once('close', () => options?.signal?.removeEventListener('abort', abort));
        }
        const body = reply.body_failure
          ? new ReadableStream({
              start(controller) {
                controller.error(new Error(readFileSync(request.token_file, 'utf8')));
              },
            })
          : file === null
            ? canonical_json(reply.json ?? null)
            : Readable.toWeb(file);
        return new Response(body as BodyInit, { status: numeric_value(reply.status) ?? 200 });
      },
    });
    try {
      let result: unknown;
      if (item.action === 'available') result = await provider.available();
      else if (item.action === 'available_order') {
        const considered: unknown[] = [];
        provider.publication = async (run_id) => {
          considered.push(run_id);
          return null;
        };
        await provider.available();
        result = { considered };
      } else if (item.action === 'publication') result = await provider.publication(item.run_id);
      else {
        const destination = join(request.root, `download-${index}.zip`);
        await provider.download_artifact(item.artifact_id, destination);
        const hash = createHash('sha256');
        let bytes = 0;
        for await (const chunk of createReadStream(destination)) {
          hash.update(chunk);
          bytes += chunk.length;
        }
        result = { sha256: hash.digest('hex'), bytes };
      }
      results.push({ name: item.name, result, error: null, seen });
    } catch (error) {
      if (!(error instanceof Error)) throw error;
      results.push({ name: item.name, result: null, error: error.message, seen });
    } finally {
      globalThis.setTimeout = original_timeout;
      if (item.inactivity_ms !== undefined)
        Object.assign(results.at(-1)!, {
          timing: { elapsed_ms: performance.now() - started, aborted, production_timeout },
        });
    }
  }
  return results;
}
