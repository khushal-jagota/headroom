/** Export-only provider proof through a scripted fetch transport and real ZIP/files. */
import { createReadStream, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { createHash } from 'node:crypto';
import { GitHubUpdateProvider } from '../provider.ts';
import { canonical_json } from '../codec.ts';
interface Reply {
  status?: number;
  json?: unknown;
  file?: string;
  failure?: boolean;
  body_failure?: boolean;
}
interface Case {
  name: string;
  action: 'available' | 'publication' | 'download';
  run_id: number;
  artifact_id: number;
  responses: Record<string, Reply>;
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
        if (reply.failure) throw new Error(readFileSync(request.token_file, 'utf8'));
        const body = reply.body_failure
          ? new ReadableStream({
              start(controller) {
                controller.error(new Error(readFileSync(request.token_file, 'utf8')));
              },
            })
          : reply.file === undefined
            ? canonical_json(reply.json ?? null)
            : Readable.toWeb(createReadStream(reply.file));
        return new Response(body as BodyInit, { status: reply.status ?? 200 });
      },
    });
    try {
      let result: unknown;
      if (item.action === 'available') result = await provider.available();
      else if (item.action === 'publication') result = await provider.publication(item.run_id);
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
    }
  }
  return results;
}
