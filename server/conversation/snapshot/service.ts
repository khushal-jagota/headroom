/** Keep complete cards readable while independent backend probes and updates run. */
import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import { FifoLock } from '../runtime/state.ts';
import type {
  BackendCardSource,
  BackendProbeEnvironment,
  BackendSnapshot,
  BackendSnapshotServiceContract,
  BackendSnapshotServiceOptions,
  BackendUpdateResult,
  SnapshotOptions,
} from './contracts.ts';
import { cancellable_environment } from './cancellation.ts';
import { parse_version } from './logic/versions.ts';
import { built_in_model_version } from './probe.ts';
import {
  MACHINE_CATALOG_BACKSTOP_SECONDS,
  UPDATE_COMMAND_TIMEOUT_SECONDS,
  UPDATE_OUTPUT_TAIL_MAXIMUM_CHARACTERS,
  VERSION_PROBE_TIMEOUT_SECONDS,
} from './configuration.ts';
export class BackendSnapshotService implements BackendSnapshotServiceContract {
  private readonly options: BackendSnapshotServiceOptions;
  private readonly now: () => Date;
  private readonly sources: ReadonlyMap<BackendInstanceId, BackendCardSource>;
  private readonly kept = new Map<BackendInstanceId, BackendSnapshot>();
  private readonly probed_at = new Map<BackendInstanceId, Date>();
  /** The freshness key each kept card was probed against, recorded only when it had one. */
  private readonly probed_against = new Map<BackendInstanceId, string>();
  /** The executable identity (real_path and mtimeMs) each kept card was probed against on home reads. */
  private readonly probed_executable = new Map<BackendInstanceId, string>();
  private readonly snapshot_locks: ReadonlyMap<BackendInstanceId, FifoLock>;
  private readonly update_locks: ReadonlyMap<BackendInstanceId, FifoLock>;
  constructor(options: BackendSnapshotServiceOptions) {
    this.options = options;
    this.now = options.now ?? (() => new Date());
    this.sources = new Map(options.sources.map((source) => [source.instance_id, source]));
    this.snapshot_locks = new Map(
      options.sources.map((source) => [source.instance_id, new FifoLock()]),
    );
    this.update_locks = new Map(
      options.sources.map((source) => [source.instance_id, new FifoLock()]),
    );
  }
  retained(backend_key: BackendInstanceId): BackendSnapshot | null {
    return this.kept.get(backend_key) ?? null;
  }
  async snapshots(options: SnapshotOptions = {}): Promise<readonly BackendSnapshot[]> {
    return Promise.all(
      this.options.sources.map((source) => this.snapshot(source.instance_id, options)),
    );
  }
  private source(backend_key: BackendInstanceId): BackendCardSource {
    const source = this.sources.get(backend_key);
    if (source === undefined) throw new Error(`No backend card source for ${backend_key}`);
    return source;
  }
  async snapshot(
    backend_key: BackendInstanceId,
    options: SnapshotOptions = {},
  ): Promise<BackendSnapshot> {
    options.signal?.throwIfAborted();
    const source = this.source(backend_key);
    const environment = cancellable_environment(this.options.environment, options.signal);
    const refresh = options.refresh ?? false;
    const freshness_moved = this.freshness_has_moved(source);
    const executable_moved = this.executable_has_moved(source, environment);
    let stale = freshness_moved || executable_moved;
    if (this.options.machine_catalog && !refresh) {
      const previous = this.kept.get(backend_key);
      if (previous) {
        const backstop_expired =
          (this.now().getTime() - this.probed_at.get(backend_key)!.getTime()) / 1000 >=
          MACHINE_CATALOG_BACKSTOP_SECONDS;
        stale = stale || backstop_expired;
        if (!stale && source.status.version_detects_upgrade) {
          const executable = environment.executable_path(source.executable_name);
          let version: string | null = null;
          if (executable !== null) {
            const outcome = await environment.run([executable, '--version'], {
              timeout_seconds: VERSION_PROBE_TIMEOUT_SECONDS,
            });
            version = parse_version(outcome.standard_output + outcome.standard_error);
          }
          stale = stale || version !== previous.version;
        }
      }
    }
    if (!refresh && !stale) {
      const kept = this.kept.get(backend_key);
      if (kept) return kept;
    }
    const lock = this.snapshot_locks.get(backend_key)!;
    const waiting = lock.locked;
    return lock.run(async () => {
      options.signal?.throwIfAborted();
      const freshness_moved_now = this.freshness_has_moved(source);
      const executable_moved_now = this.executable_has_moved(source, environment);
      if (waiting && !freshness_moved_now && !executable_moved_now) {
        const shared = this.kept.get(backend_key);
        if (shared) return shared;
      }
      const stale_now = stale || freshness_moved_now || executable_moved_now;
      const kept = refresh || stale_now ? undefined : this.kept.get(backend_key);
      if (kept) return kept;
      const reading_at = this.now();
      const probed_against = source.status.freshness_key?.(reading_at) ?? null;
      const probed_executable = this.executable_stamp(source, environment);
      const draft = await source.status.probe({
        environment,
        refresh,
        discover_update_advisory: refresh && (options.discover_update_advisory ?? true),
        signal: options.signal,
        now: reading_at,
        model_version: built_in_model_version,
      });
      let probed: BackendSnapshot = { backend_key, ...draft };
      options.signal?.throwIfAborted();
      if (
        probed_against !== null &&
        (source.status.freshness_key?.(this.now()) ?? null) !== probed_against
      ) {
        // What the card read moved during the probe. Never keep that card.
        return {
          ...probed,
          identity: null,
          available_models: [],
          reasoning_effort_options: [],
          default_model_id: null,
          default_reasoning_effort: null,
          models_complete: false,
        };
      }
      const previous = this.kept.get(backend_key);
      if (
        stale_now &&
        !refresh &&
        !executable_moved_now &&
        previous?.version === probed.version &&
        probed.update_advisory === null
      ) {
        const advisory = previous.update_advisory;
        if (advisory != null) probed = { ...probed, update_advisory: advisory };
      }
      if (!freshness_moved_now && previous?.models_complete && !probed.models_complete) {
        probed = {
          ...probed,
          available_models: previous.available_models,
          reasoning_effort_options: previous.reasoning_effort_options,
          default_model_id: previous.default_model_id,
          default_reasoning_effort: previous.default_reasoning_effort,
          models_complete: true,
        };
      }
      this.kept.set(backend_key, probed);
      this.probed_at.set(backend_key, reading_at);
      if (probed.models_complete && this.options.keep_complete_model_list) {
        try {
          await this.options.keep_complete_model_list(probed);
        } catch (error) {
          console.error(`the ${backend_key} model list was not saved`, error);
        }
      }
      if (probed_against !== null) this.probed_against.set(backend_key, probed_against);
      if (probed_executable !== null) this.probed_executable.set(backend_key, probed_executable);
      return probed;
    });
  }
  /** After a kept card had a key, an absent key ('null') still differs from it. */
  private freshness_has_moved(source: BackendCardSource): boolean {
    const against = this.probed_against.get(source.instance_id);
    if (source.status.freshness_key === undefined || against === undefined) return false;
    return (source.status.freshness_key(this.now()) ?? 'null') !== against;
  }
  /** Resolved executable path and modification time for home upgrade invalidation without spawning `--version`. */
  private executable_stamp(
    source: BackendCardSource,
    environment: BackendProbeEnvironment,
  ): string | null {
    if (this.options.machine_catalog || !source.status.version_detects_upgrade) return null;
    const executable = environment.executable_path(source.executable_name);
    if (executable === null) return 'missing';
    const resolved = environment.real_path(executable);
    const mtime_ms = environment.modified_time_ms?.(resolved) ?? null;
    return JSON.stringify([resolved, mtime_ms]);
  }
  private executable_has_moved(
    source: BackendCardSource,
    environment: BackendProbeEnvironment,
  ): boolean {
    const previous = this.probed_executable.get(source.instance_id);
    if (previous === undefined) return false;
    return this.executable_stamp(source, environment) !== previous;
  }
  async update_backend(
    backend_key: BackendInstanceId,
    options: { signal?: AbortSignal } = {},
  ): Promise<BackendUpdateResult> {
    this.source(backend_key);
    return this.update_locks.get(backend_key)!.run(async () => {
      options.signal?.throwIfAborted();
      const before = await this.snapshot(backend_key, { refresh: true, ...options });
      const advisory = before.update_advisory;
      const unavailable = 'There is no update Panels can run for this backend.';
      if (!before.installed)
        return { outcome: 'failed', detail: before.diagnoses[0] ?? unavailable, output_tail: '' };
      if (advisory === null) return { outcome: 'failed', detail: unavailable, output_tail: '' };
      if (advisory.update_command === null)
        return { outcome: 'failed', detail: advisory.detail, output_tail: '' };
      const result = await this.options.environment.run(advisory.update_command, {
        timeout_seconds: UPDATE_COMMAND_TIMEOUT_SECONDS,
        signal: options.signal,
      });
      const output_tail = [
        ...[result.standard_output, result.standard_error].filter(Boolean).join('\n'),
      ]
        .slice(-UPDATE_OUTPUT_TAIL_MAXIMUM_CHARACTERS)
        .join('');
      const after = await this.snapshot(backend_key, { refresh: true, ...options });
      if (result.exit_code !== 0)
        return {
          outcome: 'failed',
          detail: `The update command exited with code ${result.exit_code}.`,
          output_tail,
        };
      if (after.version !== null && after.version !== before.version)
        return { outcome: 'succeeded', detail: `Updated to ${after.version}.`, output_tail };
      return {
        outcome: 'unchanged',
        detail: `The update command finished, but the installed version is still ${before.version ?? 'unknown'}.`,
        output_tail,
      };
    });
  }
}
