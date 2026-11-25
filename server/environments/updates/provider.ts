/** Repository-scoped external reads. Credentials stay outside records and errors. */
import { readFileSync, createWriteStream } from 'node:fs';
import { mkdtemp, open, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import type { ReadableStream } from 'node:stream/web';
import { pipeline } from 'node:stream/promises';
import { parseIsoDatetime } from '../../core/clock.ts';
import type { ArtifactSource, PublishedUpdate, UpdateProvider } from './contracts.ts';
import { is_object, parse_release } from './codec.ts';
import { instant } from './logic/experience.ts';
import { with_zip_member } from './zip.ts';
import { LINUX_HOME } from '../release/contracts.ts';
import type { RuntimeTarget } from '../release/contracts.ts';
import { TRUSTED_RELEASE_WORKFLOW } from '../release/source.ts';

function repository_stream(response: Response, message: string): Readable {
  if (response.body === null) throw new Error(message);
  return Readable.from(
    (async function* () {
      try {
        for await (const chunk of Readable.fromWeb(response.body! as ReadableStream)) yield chunk;
      } catch (error) {
        throw new Error(message, { cause: error });
      }
    })(),
  );
}
/** Logs a failed body cancel. An abort is expected here because the request controller aborts first. */
function cancel_failed(error: unknown): void {
  if (error instanceof Error && error.name === 'AbortError') return;
  console.warn('repository update response cancel failed', error);
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
  target?: RuntimeTarget;
}
export class GitHubUpdateProvider implements UpdateProvider {
  readonly repository: string;
  private readonly token_file: string | null;
  private readonly fetch: typeof fetch;
  private readonly temporary_directory: string;
  private publication_cache: PublishedUpdate | null = null;
  private readonly target: RuntimeTarget;
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
    this.target = options.target ?? LINUX_HOME;
  }
  private headers(): Record<string, string> {
    const token = this.token_file === null ? '' : readFileSync(this.token_file, 'utf8').trim();
    if (!token) throw new Error('The repository update credential is unavailable.');
    return { Authorization: `Bearer ${token}`, Accept: 'application/vnd.github+json' };
  }
  private request(
    path: string,
    parameters: Record<string, string>,
    missing_ok: true,
  ): Promise<Response | null>;
  private request(path: string, parameters?: Record<string, string>): Promise<Response>;
  private request(
    path: string,
    parameters: Record<string, string>,
    missing_ok: false,
    signal?: AbortSignal,
    accept?: string,
  ): Promise<Response>;
  private async request(
    path: string,
    parameters: Record<string, string> = {},
    missing_ok = false,
    signal?: AbortSignal,
    accept?: string,
  ): Promise<Response | null> {
    signal?.throwIfAborted();
    const headers = this.headers();
    if (accept !== undefined) headers.Accept = accept;
    const url = new URL(`https://api.github.com/repos/${this.repository}/${path}`);
    for (const [key, value] of Object.entries(parameters)) url.searchParams.set(key, value);
    let response: Response;
    const controller = new AbortController();
    const request_signal = signal
      ? AbortSignal.any([controller.signal, signal])
      : controller.signal;
    const network_read = async <T>(read: () => Promise<T>): Promise<T> => {
      const timer = setTimeout(() => controller.abort(), 30_000);
      try {
        const result = await read();
        signal?.throwIfAborted();
        return result;
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
          signal: request_signal,
        }),
      );
    } catch (error) {
      controller.abort();
      signal?.throwIfAborted();
      throw new Error('The repository update service is unavailable or its result is uncertain.', {
        cause: error,
      });
    }
    if (missing_ok && response.status === 404) {
      controller.abort();
      await response.body?.cancel().catch(cancel_failed);
      return null;
    }
    if (!response.ok) {
      controller.abort();
      await response.body?.cancel().catch(cancel_failed);
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
    } catch (error) {
      signal?.throwIfAborted();
      throw new Error('The repository update service is unavailable or its result is uncertain.', {
        cause: error,
      });
    } finally {
      controller.abort();
      await reader.cancel().catch(cancel_failed);
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
    signal?: AbortSignal,
  ): Promise<Record<string, unknown>> {
    return this.response_json(await this.request(path, parameters, false, signal));
  }
  private async response_json(response: Response): Promise<Record<string, unknown>> {
    try {
      return record(JSON.parse(await response.text()));
    } catch (error) {
      throw new Error('The repository update service refused the operation.', { cause: error });
    }
  }
  async available(): Promise<PublishedUpdate | null> {
    const publications: Record<string, unknown>[] = [];
    for (let page = 1; ; page++) {
      const response = await this.request('releases', { per_page: '100', page: String(page) });
      const releases = list(await response.json());
      publications.push(...releases);
      if (releases.length < 100) break;
    }
    const eligible = publications
      .filter(
        (publication) =>
          publication.draft === false &&
          publication.prerelease === false &&
          typeof publication.tag_name === 'string' &&
          publication.tag_name.startsWith('server-'),
      )
      .map((publication) => {
        const published_at_ms =
          typeof publication.published_at === 'string' ? Date.parse(publication.published_at) : NaN;
        if (!Number.isFinite(published_at_ms)) throw new Error('Invalid server publication date');
        return { publication, published_at_ms };
      })
      .sort((a, b) => b.published_at_ms - a.published_at_ms);
    for (const { publication } of eligible) {
      const assets = list(publication.assets);
      const proof = assets.find((asset) => asset.name === 'publication.json');
      if (proof === undefined) continue;
      const payload = await this.response_json(
        await this.request(
          `releases/assets/${proof.id}`,
          {},
          false,
          undefined,
          'application/octet-stream',
        ),
      );
      const source = record(payload.source_run);
      if (
        source.repository !== this.repository ||
        source.workflow_path !== TRUSTED_RELEASE_WORKFLOW ||
        source.workflow_ref !== `${this.repository}/${TRUSTED_RELEASE_WORKFLOW}@refs/heads/main` ||
        source.ref !== 'refs/heads/main' ||
        source.event !== 'push' ||
        typeof source.source_sha !== 'string' ||
        !/^[0-9a-f]{40}$/.test(source.source_sha) ||
        typeof source.run_id !== 'string' ||
        !/^[1-9][0-9]*$/.test(source.run_id) ||
        typeof source.run_attempt !== 'string' ||
        !/^[1-9][0-9]*$/.test(source.run_attempt) ||
        publication.tag_name !==
          `server-${source.source_sha}-${source.run_id}-${source.run_attempt}` ||
        publication.target_commitish !== source.source_sha ||
        payload.publication_run_id !== Number(source.run_id)
      )
        throw new Error('Publication identity differs from trusted repository evidence.');
      const check_asset = (value: Record<string, unknown>, name: string) => {
        if (
          value.artifact_source !== 'github-release' ||
          !Number.isSafeInteger(value.artifact_id) ||
          !assets.some((asset) => asset.id === value.artifact_id && asset.name === name)
        )
          throw new Error('Publication asset identity differs from its release.');
      };
      check_asset(payload, 'app.zip');
      for (const [key, name] of [
        ['mac', 'mac.zip'],
        ['linux_executor', 'linux-executor.zip'],
        ['mac_home', 'mac-home.zip'],
      ]) {
        if (payload[key!] !== undefined && payload[key!] !== null)
          check_asset(record(payload[key!]), name!);
      }
      let selected = payload;
      if (this.target.system === 'darwin') {
        if (!is_object(payload.mac_home)) continue;
        selected = { ...payload, ...payload.mac_home, mac: null };
      }
      const release = parse_release({ ...selected, proof_artifact_id: proof.id });
      if (
        release.manifest.app_sha !== source.source_sha ||
        release.expires_at !== null ||
        release.parts === undefined
      )
        throw new Error('Publication identity differs from trusted repository evidence.');
      if (release.manifest.runtime_contract === null) continue;
      if (
        release.manifest.runtime_contract.system !== this.target.system ||
        release.manifest.runtime_contract.architecture !== this.target.architecture ||
        (release.manifest.runtime_contract.system === 'darwin' &&
          release.manifest.runtime_contract.role !== this.target.role)
      )
        throw new Error('Publication runtime target differs from this home');
      return release;
    }
    return null;
  }
  async publication(run_id: number): Promise<PublishedUpdate | null> {
    const run = await this.json(`actions/runs/${run_id}`);
    if (
      run.head_branch !== 'main' ||
      run.event !== 'push' ||
      run.conclusion !== 'success' ||
      !is_object(run.head_repository) ||
      run.head_repository.full_name !== this.repository ||
      run.path !== TRUSTED_RELEASE_WORKFLOW
    )
      return null;
    const artifacts = await this.json(`actions/runs/${run_id}/artifacts`, {
      per_page: '100',
    });
    const by_name = new Map(
      list(artifacts.artifacts)
        .filter((artifact) => artifact.expired === false)
        .map((artifact) => [artifact.name, artifact]),
    );
    const proof = by_name.get('panels-publication-proof'),
      app = by_name.get(this.target.system === 'darwin' ? 'panels-mac-home' : 'panels-app');
    if (proof === undefined || app === undefined) return null;
    let release = this.publication_cache;
    if (
      release === null ||
      release.publication_run_id !== run_id ||
      release.artifact_id !== app.id ||
      release.proof_artifact_id !== proof.id
    ) {
      const directory = await mkdtemp(join(this.temporary_directory, 'panels-update-proof-'));
      try {
        const path = join(directory, 'proof.zip');
        const response = await this.request(`actions/artifacts/${proof.id}/zip`);
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
          return record(JSON.parse(Buffer.concat(chunks).toString('utf8')));
        });
        let selected = payload;
        if (this.target.system === 'darwin') {
          if (!is_object(payload.mac_home)) return null;
          selected = { ...payload, ...payload.mac_home, mac: null };
        }
        selected.proof_artifact_id = proof.id;
        release = parse_release(selected);
      } finally {
        await rm(directory, { recursive: true, force: true });
      }
    }
    if (
      release.publication_run_id !== run_id ||
      release.artifact_id !== app.id ||
      release.manifest.app_sha !== run.head_sha ||
      (release.mac !== null && by_name.get('panels-mac-runtime')?.id !== release.mac.artifact_id)
    )
      throw new Error('Publication identity differs from trusted repository evidence.');
    if (
      release.expires_at === null ||
      instant(release.expires_at).epochMicroseconds <= BigInt(Date.now()) * 1000n
    )
      return null;
    if (release.manifest.runtime_contract === null) return null;
    if (
      release.manifest.runtime_contract.system !== this.target.system ||
      release.manifest.runtime_contract.architecture !== this.target.architecture
    )
      throw new Error('Publication runtime target differs from this home');
    if (
      this.target.system === 'darwin' &&
      (release.manifest.runtime_contract.version !== 2 ||
        release.manifest.runtime_contract.system !== 'darwin' ||
        release.manifest.runtime_contract.architecture !== 'arm64' ||
        release.manifest.runtime_contract.role !== 'home')
    )
      throw new Error('Publication runtime target differs from this home');
    this.publication_cache = release;
    return release;
  }
  async download_artifact(
    artifact_id: number,
    destination: string,
    signal?: AbortSignal,
    source?: ArtifactSource,
  ): Promise<void> {
    signal?.throwIfAborted();
    if (source === undefined) {
      const metadata = await this.json(`actions/artifacts/${artifact_id}`, {}, signal);
      signal?.throwIfAborted();
      if (metadata.expired === true) throw new Error('The pinned update artifact expired.');
    }
    const headers = this.headers();
    if (source === 'github-release') headers.Accept = 'application/octet-stream';
    const controller = new AbortController();
    const request_signal = signal
      ? AbortSignal.any([controller.signal, signal])
      : controller.signal;
    // httpx's streaming timeout bounds each network read, not the whole archive.
    // Do not time a disk write or ask the network reader for another chunk during it.
    const network_read = async <T>(read: () => Promise<T>): Promise<T> => {
      const timer = setTimeout(() => controller.abort(), 60_000);
      try {
        const result = await read();
        signal?.throwIfAborted();
        return result;
      } catch (error) {
        controller.abort();
        signal?.throwIfAborted();
        throw new Error('The repository update download did not complete.', { cause: error });
      } finally {
        clearTimeout(timer);
      }
    };
    const response = await network_read(() =>
      this.fetch(
        `https://api.github.com/repos/${this.repository}/${source === 'github-release' ? `releases/assets/${artifact_id}` : `actions/artifacts/${artifact_id}/zip`}`,
        { method: 'GET', headers, redirect: 'follow', signal: request_signal },
      ),
    );
    if (!response.ok) {
      controller.abort();
      await response.body?.cancel().catch(cancel_failed);
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
          signal?.throwIfAborted();
          const { bytesWritten } = await output.write(value, offset, value.byteLength - offset);
          offset += bytesWritten;
        }
      }
    } finally {
      controller.abort();
      await reader.cancel().catch(cancel_failed);
      reader.releaseLock();
      await output?.close();
    }
    signal?.throwIfAborted();
  }
}
