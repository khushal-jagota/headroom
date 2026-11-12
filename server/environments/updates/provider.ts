/** Repository-scoped external reads. Credentials stay outside records and errors. */
import { readFileSync, createWriteStream } from 'node:fs';
import { mkdtemp, open, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import type { ReadableStream } from 'node:stream/web';
import { pipeline } from 'node:stream/promises';
import { JsonNumber, numeric_value } from '../../core/http/numeric_json.ts';
import type { NumericJSON } from '../../core/http/numeric_json.ts';
import type { PublishedUpdate, UpdateProvider } from './contracts.ts';
import { canonical_json, is_object, parse_attempt_json, parse_release } from './codec.ts';
import { instant } from './logic/experience.ts';
import { with_zip_member } from './zip.ts';

type Identity = NumericJSON<number>;
function repository_stream(response: Response, message: string): Readable {
  if (response.body === null) throw new Error(message);
  return Readable.from(
    (async function* () {
      try {
        for await (const chunk of Readable.fromWeb(response.body! as ReadableStream)) yield chunk;
      } catch {
        throw new Error(message);
      }
    })(),
  );
}
function same_number(left: unknown, right: unknown): boolean {
  const integer = (value: unknown): bigint | null => {
    if (value instanceof JsonNumber && value.integer !== null) return value.integer;
    const number = numeric_value(value);
    return number !== undefined && Number.isFinite(number) && Number.isInteger(number)
      ? BigInt(number)
      : null;
  };
  const a = integer(left),
    b = integer(right);
  return a !== null && b !== null ? a === b : numeric_value(left) === numeric_value(right);
}
function record(value: unknown): Record<string, unknown> {
  if (!is_object(value)) throw new Error('The repository update service refused the operation.');
  return value;
}
function list(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value))
    throw new Error('The repository update service refused the operation.');
  return value.map(record);
}
export interface GitHubUpdateProviderOptions {
  fetch?: typeof fetch;
  temporary_directory?: string;
}
export class GitHubUpdateProvider implements UpdateProvider {
  readonly repository: string;
  private readonly token_file: string | null;
  private readonly fetch: typeof fetch;
  private readonly temporary_directory: string;
  private publication_cache: PublishedUpdate | null = null;
  constructor(
    repository: string,
    token_file: string | null,
    options: GitHubUpdateProviderOptions = {},
  ) {
    if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid update repository');
    this.repository = repository;
    this.token_file = token_file;
    this.fetch = options.fetch ?? globalThis.fetch;
    this.temporary_directory = options.temporary_directory ?? tmpdir();
  }
  private headers(): Record<string, string> {
    const token = this.token_file === null ? '' : readFileSync(this.token_file, 'utf8').trim();
    if (!token) throw new Error('The repository update credential is unavailable.');
    return { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json' };
  }
  private async request(path: string, parameters: Record<string, string> = {}): Promise<Response> {
    const headers = this.headers();
    const url = new URL(`https://api.github.com/repos/${this.repository}/${path}`);
    for (const [key, value] of Object.entries(parameters)) url.searchParams.set(key, value);
    let response: Response;
    const controller = new AbortController();
    const network_read = async <T>(read: () => Promise<T>): Promise<T> => {
      const timer = setTimeout(() => controller.abort(), 30_000);
      try {
        return await read();
      } finally {
        clearTimeout(timer);
      }
    };
    try {
      response = await network_read(() =>
        this.fetch(url, {
          method: 'GET',
          headers,
          redirect: 'follow',
          signal: controller.signal,
        }),
      );
    } catch {
      controller.abort();
      throw new Error('The repository update service is unavailable or its result is uncertain.');
    }
    if (!response.ok) {
      controller.abort();
      await response.body?.cancel().catch(() => {});
      throw new Error('The repository update service refused the operation.');
    }
    if (response.body === null) {
      controller.abort();
      return response;
    }
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    try {
      for (;;) {
        const { value, done } = await network_read(() => reader.read());
        if (done) break;
        chunks.push(value);
      }
    } catch {
      throw new Error('The repository update service is unavailable or its result is uncertain.');
    } finally {
      controller.abort();
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
    return new Response(Buffer.concat(chunks), {
      status: response.status,
      headers: response.headers,
    });
  }
  private async json(
    path: string,
    parameters: Record<string, string> = {},
  ): Promise<Record<string, unknown>> {
    const response = await this.request(path, parameters);
    try {
      return record(parse_attempt_json(await response.text()));
    } catch {
      throw new Error('The repository update service refused the operation.');
    }
  }
  async available(): Promise<PublishedUpdate | null> {
    const result = await this.json('actions/workflows/deploy.yml/runs', {
      branch: 'main',
      event: 'push',
      status: 'success',
      per_page: '20',
    });
    for (const run of list(result.workflow_runs)) {
      const release = await this.publication(run.id as Identity);
      if (release !== null) return release;
    }
    return null;
  }
  async publication(run_id: Identity): Promise<PublishedUpdate | null> {
    const run = await this.json(`actions/runs/${canonical_json(run_id)}`);
    if (
      run.head_branch !== 'main' ||
      run.event !== 'push' ||
      run.conclusion !== 'success' ||
      !is_object(run.head_repository) ||
      run.head_repository.full_name !== this.repository ||
      run.path !== '.github/workflows/deploy.yml'
    )
      return null;
    const artifacts = await this.json(`actions/runs/${canonical_json(run_id)}/artifacts`, {
      per_page: '100',
    });
    const by_name = new Map(
      list(artifacts.artifacts)
        .filter((artifact) => artifact.expired === false)
        .map((artifact) => [artifact.name, artifact]),
    );
    const proof = by_name.get('panels-publication-proof'),
      app = by_name.get('panels-app');
    if (proof === undefined || app === undefined) return null;
    let release = this.publication_cache;
    if (
      release === null ||
      !same_number(release.publication_run_id, run_id) ||
      !same_number(release.artifact_id, app.id) ||
      !same_number(release.proof_artifact_id, proof.id)
    ) {
      const directory = await mkdtemp(join(this.temporary_directory, 'panels-update-proof-'));
      try {
        const path = join(directory, 'proof.zip');
        const response = await this.request(`actions/artifacts/${canonical_json(proof.id)}/zip`);
        await pipeline(
          repository_stream(
            response,
            'The repository update service is unavailable or its result is uncertain.',
          ),
          createWriteStream(path, { flags: 'wx' }),
        );
        const payload = await with_zip_member(path, 'publication.json', async (stream) => {
          const chunks: Buffer[] = [];
          for await (const chunk of stream) chunks.push(Buffer.from(chunk));
          return record(parse_attempt_json(Buffer.concat(chunks).toString('utf8')));
        });
        payload.proof_artifact_id = proof.id;
        release = parse_release(payload);
      } finally {
        await rm(directory, { recursive: true, force: true });
      }
    }
    if (
      !same_number(release.publication_run_id, run_id) ||
      !same_number(release.artifact_id, app.id) ||
      release.manifest.app_sha !== run.head_sha ||
      (release.mac !== null &&
        !same_number(by_name.get('panels-mac-runtime')?.id, release.mac.artifact_id))
    )
      throw new Error('Publication identity differs from trusted repository evidence.');
    if (instant(release.expires_at).epochMicroseconds <= BigInt(Date.now()) * 1000n) return null;
    if (release.manifest.runtime_contract === null) return null;
    this.publication_cache = release;
    return release;
  }
  async download_artifact(artifact_id: Identity, destination: string): Promise<void> {
    const metadata = await this.json(`actions/artifacts/${canonical_json(artifact_id)}`);
    if (metadata.expired === true) throw new Error('The pinned update artifact expired.');
    const headers = this.headers();
    const controller = new AbortController();
    // httpx's streaming timeout bounds each network read, not the whole archive.
    // Do not time a disk write or ask the network reader for another chunk during it.
    const network_read = async <T>(read: () => Promise<T>): Promise<T> => {
      const timer = setTimeout(() => controller.abort(), 60_000);
      try {
        return await read();
      } catch {
        controller.abort();
        throw new Error('The repository update download did not complete.');
      } finally {
        clearTimeout(timer);
      }
    };
    const response = await network_read(() =>
      this.fetch(
        `https://api.github.com/repos/${this.repository}/actions/artifacts/${canonical_json(artifact_id)}/zip`,
        { method: 'GET', headers, redirect: 'follow', signal: controller.signal },
      ),
    );
    if (!response.ok) {
      controller.abort();
      await response.body?.cancel().catch(() => {});
      throw new Error('The repository update service refused the download.');
    }
    if (response.body === null) {
      controller.abort();
      throw new Error('The repository update download did not complete.');
    }
    const reader = response.body.getReader();
    let output: Awaited<ReturnType<typeof open>> | undefined;
    try {
      output = await open(destination, 'wx');
      for (;;) {
        const { value, done } = await network_read(() => reader.read());
        if (done) break;
        let offset = 0;
        while (offset < value.byteLength) {
          const { bytesWritten } = await output.write(value, offset, value.byteLength - offset);
          offset += bytesWritten;
        }
      }
    } finally {
      controller.abort();
      await reader.cancel().catch(() => {});
      reader.releaseLock();
      await output?.close();
    }
  }
}
