/** Latest lease offers prepare an independent, durable runtime handoff. */
import { join } from 'node:path';
import type { ExecutorIdentity } from '../../../execution_identity.ts';
import { observe_shutdown } from '../../../conversation/shutdown.ts';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import { encode_bounded } from '../../protocol.ts';
import { integer_value } from '../../integers.ts';
import type {
  MacRuntimeSelection,
  MacUpdateCheck,
  MacUpdatePhase,
  MacUpdatePrefetch,
  MacUpdateTarget,
} from '../../wire.generated.ts';
import type { FilesService } from '../files.ts';
import type { TransferHandle } from '../file_worker_contracts.ts';
import type { ExecutorPaths } from '../configuration.ts';
import * as settings from '../configuration.ts';
import type { MacMaintenanceContract } from '../runner/maintenance_contracts.ts';
import { atomic_record, read_record } from '../runtime/records.ts';
import { handoff_record } from '../runtime/handoff.ts';
import { HttpSessions, read_bounded } from '../transport/sessions.ts';
import { CONNECTORS } from '../transport/http.ts';
import type { MacHttpOptions } from '../transport/contracts.ts';

export interface MacUpdateRuntimeSettings {
  child: boolean;
  ready_path: string | null;
  handoff_path: string | null;
}
export interface MacUpdateControllerOptions {
  paths: ExecutorPaths;
  runner: Pick<
    MacMaintenanceContract,
    | 'planned_update_replay'
    | 'prepare_update'
    | 'finish_planned_replay'
    | 'resume_after_cancelled_update'
  >;
  presence: { readonly identity: ExecutorIdentity; leased(): boolean };
  outbox: { pending_count(): bigint };
  /** The host owns this separate worker and closes it after controller settlement. */
  files: FilesService;
  executor_id: string;
  runtime: MacUpdateRuntimeSettings;
  session_factory(): HttpSessions;
  request_exit(): void;
  on_failure(error: Error): void;
}
export function create_update_session(
  server_url: string,
  options: MacHttpOptions = {},
): HttpSessions {
  return new HttpSessions(
    settings.validate_server_url(server_url),
    { ...CONNECTORS, lease: settings.MAC_READS_CONNECTOR },
    options,
  );
}
export class MacUpdateController {
  private readonly root: string;
  private readonly cancellation = new AbortController();
  private readonly sessions = new Set<HttpSessions>();
  private sequence = Promise.resolve();
  private loop: Promise<void> | undefined;
  private shutdown_started = false;
  private finish!: () => void;
  private reject!: (error: unknown) => void;
  private readonly actual = new Promise<void>((resolve, reject) => {
    this.finish = resolve;
    this.reject = reject;
  });
  private readonly failures = new Set<unknown>();
  private latest: MacUpdateCheck | undefined;
  private offered = false;
  private wake: (() => void) | undefined;
  private prepared: string | null = null;
  private confirmed: string | null = null;
  private gate_pending: boolean;
  private prefetch: { sha: string; done: boolean; task: Promise<void> } | undefined;
  private readonly options: MacUpdateControllerOptions;
  constructor(options: MacUpdateControllerOptions) {
    this.options = options;
    void this.actual.catch(() => {});
    this.root = join(options.paths.root, 'updates');
    this.gate_pending = options.runner.planned_update_replay || options.runtime.ready_path !== null;
  }
  private get signal(): AbortSignal {
    return this.cancellation.signal;
  }
  start(): void {
    this.signal.throwIfAborted();
    if (this.loop !== undefined) return;
    this.loop = this.run();
    void this.loop.catch((error) => this.fault(error));
  }
  offer(check: MacUpdateCheck): void {
    if (this.signal.aborted) return;
    this.latest = check;
    this.offered = true;
    this.wake?.();
  }
  apply(check: MacUpdateCheck): Promise<void> {
    const work = this.sequence.then(() => {
      this.signal.throwIfAborted();
      return this.with_session((session) => this.perform(session, check));
    });
    this.sequence = work.catch(() => {});
    return work;
  }
  private fault(error: unknown): void {
    const first = this.failures.size === 0;
    this.failures.add(error);
    if (first) this.options.on_failure(error instanceof Error ? error : new Error(String(error)));
  }
  private async with_session<T>(work: (session: HttpSessions) => Promise<T>): Promise<T> {
    this.signal.throwIfAborted();
    const session = this.options.session_factory();
    this.sessions.add(session);
    try {
      return await work(session);
    } finally {
      try {
        await session.close();
        await session.settled();
      } catch (error) {
        this.fault(error);
        throw error;
      }
      this.sessions.delete(session);
    }
  }
  private async report(
    session: HttpSessions,
    target: MacUpdateTarget,
    phase: MacUpdatePhase,
    detail: string | null = null,
  ): Promise<boolean> {
    const body = encode_bounded(
      {
        request_id: target.request_id,
        application_revision: target.application_revision,
        phase,
        detail:
          detail === null
            ? null
            : Array.from(detail).slice(0, settings.MAX_MAC_UPDATE_DETAIL_CHARACTERS).join(''),
        identity:
          phase === 'current' || phase === 'ready_to_restart'
            ? this.options.presence.identity
            : null,
      },
      'MacUpdateReport',
    );
    const reply = await session.request(
      {
        loop: 'lease',
        method: 'POST',
        path: '/api/executor/update',
        body,
        headers: { 'Content-Type': 'application/json' },
        read_timeout: settings.REQUEST_TIMEOUT_SECONDS,
        signal: this.signal,
      },
      { 'X-Panels-Executor-ID': this.options.executor_id },
      async (response) => {
        if (response.statusCode !== 200) throw new Error('Mac update channel refused the request.');
        return JSON.parse(
          new TextDecoder('utf-8', { fatal: true }).decode(
            await read_bounded(response, settings.MAX_MAC_UPDATE_RESPONSE_BYTES),
          ),
        ) as { confirmed?: boolean } | null;
      },
    );
    return reply?.confirmed === true;
  }
  private valid_reference(value: MacUpdatePrefetch, expected_path: string): boolean {
    const bytes = integer_value(value.archive_size);
    return (
      value.application_revision.length === 40 &&
      /^[0-9a-f]{40}$/.test(value.application_revision) &&
      value.archive_sha256.length === 64 &&
      /^[0-9a-f]{64}$/.test(value.archive_sha256) &&
      value.archive_path === expected_path &&
      bytes > 0n &&
      bytes <= BigInt(settings.MAX_MAC_RUNTIME_ARCHIVE_BYTES)
    );
  }
  private release_path(value: MacUpdatePrefetch): string {
    return join(this.root, 'releases', value.application_revision + '-' + value.archive_sha256);
  }
  private async cached(value: MacUpdatePrefetch): Promise<string | null> {
    const path = this.release_path(value);
    try {
      await this.options.files.read_runtime_revision(path, value.application_revision);
      return path;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
      throw error;
    }
  }
  private async fetch(
    session: HttpSessions,
    value: MacUpdatePrefetch,
    name: string,
  ): Promise<string> {
    const files = this.options.files;
    const root = await files.open_directory(this.root, { create: true });
    const path = join(this.root, name);
    try {
      await files.unlink_at(root, name);
      await session.request(
        {
          loop: 'lease',
          method: 'GET',
          path: value.archive_path,
          headers: { 'Accept-Encoding': 'identity' },
          read_timeout: settings.REQUEST_TIMEOUT_SECONDS,
          signal: this.signal,
        },
        { 'X-Panels-Executor-ID': this.options.executor_id },
        async (response) => {
          if (response.statusCode !== 200) throw new Error('Mac runtime archive is unavailable.');
          if (response.headers['content-encoding'])
            throw new Error('Mac runtime archive uses transport encoding.');
          const handle: TransferHandle = await files.open_transfer(path, 'write');
          let complete = false,
            count = 0n;
          try {
            for await (const data of response) {
              this.signal.throwIfAborted();
              const bytes = Buffer.from(data as Uint8Array);
              count += BigInt(bytes.length);
              if (count > integer_value(value.archive_size))
                throw new Error('Mac runtime archive exceeds its published size.');
              for (let offset = 0; offset < bytes.length; offset += settings.TRANSFER_CHUNK_BYTES)
                await files.write_transfer(
                  handle,
                  bytes.subarray(offset, offset + settings.TRANSFER_CHUNK_BYTES),
                );
            }
            await files.finish_transfer(
              handle,
              value.archive_sha256,
              integer_value(value.archive_size),
            );
            complete = true;
          } finally {
            await files.close_transfer(handle, !complete);
          }
        },
      );
      this.signal.throwIfAborted();
      return await files.extract_runtime(
        path,
        join(this.root, 'releases'),
        value.archive_sha256,
        Number(integer_value(value.archive_size)),
        value.application_revision,
      );
    } finally {
      try {
        await files.unlink_at(root, name);
      } finally {
        await files.close_directory(root);
      }
    }
  }
  private async download(session: HttpSessions, target: MacUpdateTarget): Promise<string> {
    if (
      !target.request_id ||
      /[^A-Za-z0-9_-]/.test(target.request_id) ||
      !this.valid_reference(target, `/api/executor/update/${target.request_id}/archive`)
    )
      throw new TypeError('Invalid Mac runtime target.');
    if (this.prefetch?.sha === target.archive_sha256) await this.prefetch.task;
    this.signal.throwIfAborted();
    const cached = await this.cached(target);
    return cached ?? (await this.fetch(session, target, 'download.tar.gz'));
  }
  private async start_prefetch(value: MacUpdatePrefetch | null): Promise<void> {
    if (
      !value ||
      value.application_revision === this.options.presence.identity.application_revision ||
      (this.prefetch && (!this.prefetch.done || this.prefetch.sha === value.archive_sha256)) ||
      !this.valid_reference(value, `/api/executor/update/prefetch/${value.archive_sha256}`)
    )
      return;
    try {
      if (await this.cached(value)) return;
    } catch {
      return;
    }
    this.signal.throwIfAborted();
    const prefetch = { sha: value.archive_sha256, done: false, task: Promise.resolve() };
    prefetch.task = this.with_session((session) => this.fetch(session, value, 'prefetch.tar.gz'))
      .then(
        () => {},
        () => {},
      )
      .finally(() => {
        prefetch.done = true;
      });
    this.prefetch = prefetch;
  }
  private async perform(session: HttpSessions, check: MacUpdateCheck): Promise<void> {
    await this.release_handoff_custody();
    await this.release_runtime_admission();
    if (integer_value(check.schema_version) !== BigInt(settings.MAC_UPDATE_SCHEMA_VERSION))
      throw new TypeError('Unsupported Mac update channel schema.');
    await this.start_prefetch(check.prefetch ?? null);
    const target = check.target;
    if (target === null) {
      if (this.prepared !== null) {
        await this.resume_if_safe();
        this.prepared = null;
      }
      return;
    }
    if (this.prepared !== null && this.prepared !== target.request_id) {
      this.prepared = null;
      await this.resume_if_safe();
    }
    const failure = await read_record(this.options.files, join(this.root, 'failure.json'));
    if (failure?.request_id === target.request_id) {
      await this.report(
        session,
        target,
        failure.phase === 'failed' ? 'failed' : 'reverted',
        String(failure.detail ?? ''),
      );
      return;
    }
    const identity = this.options.presence.identity;
    if (target.application_revision === identity.application_revision) {
      if (
        this.options.presence.leased() &&
        this.confirmed !== target.request_id &&
        (await this.report(session, target, 'current'))
      ) {
        this.signal.throwIfAborted();
        if (this.options.runtime.ready_path !== null)
          await atomic_record(this.options.files, this.options.runtime.ready_path, {
            request_id: target.request_id,
            application_revision: target.application_revision,
            identity,
          });
        this.confirmed = target.request_id;
        await this.release_runtime_admission();
      }
      return;
    }
    if (this.options.runner.planned_update_replay) {
      await this.report(session, target, 'waiting');
      return;
    }
    if (this.prepared !== target.request_id) {
      let prepared: boolean;
      try {
        prepared = await this.options.runner.prepare_update(
          target.mode,
          target.request_id,
          this.signal,
        );
      } catch (error) {
        this.signal.throwIfAborted();
        const detail = `Mac work could not stop safely: ${String(error)}`;
        await atomic_record(this.options.files, join(this.root, 'failure.json'), {
          request_id: target.request_id,
          phase: 'failed',
          detail,
          admission_stopped: true,
        });
        await this.report(session, target, 'failed', detail);
        return;
      }
      this.signal.throwIfAborted();
      if (!prepared) {
        await this.report(session, target, 'waiting');
        return;
      }
      this.prepared = target.request_id;
    }
    if (!target.activate || this.options.presence.leased())
      await this.report(session, target, 'ready_to_restart');
    if (!target.activate) return;
    if (!this.options.runtime.child)
      throw new Error('Mac runtime update requires its fixed supervisor.');
    await this.report(session, target, 'downloading');
    let runtime: string;
    try {
      runtime = await this.download(session, target);
    } catch (error) {
      this.signal.throwIfAborted();
      const detail = Array.from(`Mac runtime installation failed: ${String(error)}`)
        .slice(0, settings.MAX_MAC_UPDATE_DETAIL_CHARACTERS)
        .join('');
      await atomic_record(this.options.files, join(this.root, 'failure.json'), {
        request_id: target.request_id,
        phase: 'failed',
        detail,
      });
      this.prepared = null;
      await this.resume_if_safe();
      await this.report(session, target, 'failed', detail);
      return;
    }
    this.signal.throwIfAborted();
    const previous = await read_record(this.options.files, join(this.root, 'selection.json'));
    const selection: MacRuntimeSelection = {
      request_id: target.request_id,
      application_revision: target.application_revision,
      runtime_path: runtime,
      previous_runtime_path: previous === null ? null : String(previous.runtime_path),
      previous_revision: identity.application_revision,
      executor_incarnation: identity.executor_incarnation,
      connection_generation: identity.connection_generation,
      archive_sha256: target.archive_sha256,
    };
    this.signal.throwIfAborted();
    await atomic_record(
      this.options.files,
      join(this.root, 'handoff.json'),
      handoff_record(identity, true),
    );
    await atomic_record(this.options.files, join(this.root, 'pending.json'), selection);
    if (this.options.presence.leased()) await this.report(session, target, 'ready_to_restart');
    this.signal.throwIfAborted();
    this.options.request_exit();
  }
  private async resume_if_safe(): Promise<void> {
    this.signal.throwIfAborted();
    const path = this.options.runtime.handoff_path;
    const custody = path === null ? null : await read_record(this.options.files, path);
    this.signal.throwIfAborted();
    if (custody?.replay_pending !== true) {
      if (this.gate_pending) await this.release_runtime_admission();
      else this.options.runner.resume_after_cancelled_update();
    }
  }
  async release_runtime_admission(): Promise<void> {
    this.signal.throwIfAborted();
    if (!this.gate_pending || this.prepared !== null) return;
    if (this.options.runtime.ready_path !== null) {
      const selected = await read_record(this.options.files, join(this.root, 'selection.json'));
      const pending = await read_record(this.options.files, join(this.root, 'pending.json'));
      if (this.confirmed === null || selected?.request_id !== this.confirmed || pending !== null)
        return;
    }
    if (!(await this.release_custody())) return;
    this.signal.throwIfAborted();
    this.options.runner.finish_planned_replay();
    this.options.runner.resume_after_cancelled_update();
    this.gate_pending = false;
  }
  async release_handoff_custody(): Promise<void> {
    this.signal.throwIfAborted();
    if (this.options.outbox.pending_count() === 0n) await this.release_custody();
  }
  private async release_custody(): Promise<boolean> {
    this.signal.throwIfAborted();
    const path = this.options.runtime.handoff_path;
    if (path === null) return true;
    if (!this.options.presence.leased()) return false;
    const receipt = await read_record(this.options.files, path);
    this.signal.throwIfAborted();
    if (receipt?.replay_pending === true)
      await atomic_record(
        this.options.files,
        path,
        handoff_record(this.options.presence.identity, false),
      );
    return true;
  }
  private wait(poll: boolean): Promise<void> {
    if (this.offered || this.signal.aborted) return Promise.resolve();
    return new Promise((resolve) => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const finish = () => {
        clearTimeout(timer);
        this.signal.removeEventListener('abort', finish);
        this.wake = undefined;
        resolve();
      };
      this.wake = finish;
      this.signal.addEventListener('abort', finish, { once: true });
      if (poll) timer = setTimeout(finish, settings.MAC_UPDATE_POLL_SECONDS * 1000);
    });
  }
  private async run(): Promise<void> {
    while (!this.signal.aborted) {
      await this.wait(this.options.runner.planned_update_replay || this.gate_pending);
      if (this.signal.aborted) return;
      try {
        if (this.offered && this.latest !== undefined) {
          const check = this.latest;
          this.offered = false;
          await this.apply(check);
        } else {
          await this.release_handoff_custody();
          await this.release_runtime_admission();
        }
      } catch (error) {
        if (!this.signal.aborted) {
          if (!(error instanceof Error)) throw error;
          console.warn('Mac update attempt failed:', error);
        }
      }
    }
  }
  stop(options: StopOptions): Promise<void> {
    if (!this.shutdown_started) {
      this.shutdown_started = true;
      this.cancellation.abort(new DOMException('Mac updates stopped', 'AbortError'));
      this.wake?.();
      const closing = [...this.sessions].map((session) => session.close());
      const cleanup = async () => {
        const results = await Promise.allSettled([this.sequence, this.loop, ...closing]);
        for (const result of results)
          if (result.status === 'rejected') this.failures.add(result.reason);
        await this.prefetch?.task;
        await Promise.all([...this.sessions].map((session) => session.settled()));
        if (this.failures.size === 1) throw [...this.failures][0];
        if (this.failures.size)
          throw new AggregateError([...this.failures], 'Mac update cleanup failed.');
      };
      void cleanup().then(this.finish, this.reject);
    }
    return observe_shutdown(this.actual, options, 'Mac updates remain unfinished at shutdown.');
  }
  settled(): Promise<void> {
    return this.actual;
  }
}
