/** Keep complete cards readable while independent backend probes and updates run. */
import { ConversationBackendKey } from '../contracts.ts';
import { FifoLock } from '../runtime/state.ts';
import type {
  BackendSnapshot,
  BackendSnapshotServiceContract,
  BackendSnapshotServiceOptions,
  BackendUpdateResult,
  SnapshotOptions,
} from './contracts.ts';
import { cancellable_environment } from './cancellation.ts';
import { parse_version } from './logic/versions.ts';
import {
  MACHINE_CATALOG_BACKSTOP_SECONDS,
  UPDATE_COMMAND_TIMEOUT_SECONDS,
  UPDATE_OUTPUT_TAIL_MAXIMUM_CHARACTERS,
  VERSION_PROBE_TIMEOUT_SECONDS,
} from './configuration.ts';
export class BackendSnapshotService implements BackendSnapshotServiceContract {
  private readonly options: BackendSnapshotServiceOptions;
  private readonly now: () => Date;
  private readonly kept = new Map<ConversationBackendKey, BackendSnapshot>();
  private readonly probed_at = new Map<ConversationBackendKey, Date>();
  private codex_card_was_probed_against: string | null = null;
  private readonly snapshot_locks = new Map(
    Object.values(ConversationBackendKey).map((key) => [key, new FifoLock()]),
  );
  private readonly update_locks = new Map(
    Object.values(ConversationBackendKey).map((key) => [key, new FifoLock()]),
  );
  constructor(options: BackendSnapshotServiceOptions) {
    this.options = options;
    this.now = options.now ?? (() => new Date());
  }
  retained(backend_key: ConversationBackendKey): BackendSnapshot | null {
    return this.kept.get(backend_key) ?? null;
  }
  async snapshots(options: SnapshotOptions = {}): Promise<readonly BackendSnapshot[]> {
    return Promise.all(
      Object.values(ConversationBackendKey).map((key) => this.snapshot(key, options)),
    );
  }
  async snapshot(
    backend_key: ConversationBackendKey,
    options: SnapshotOptions = {},
  ): Promise<BackendSnapshot> {
    options.signal?.throwIfAborted();
    const environment = cancellable_environment(this.options.environment, options.signal);
    const refresh = options.refresh ?? false;
    const login_moved = this.codex_login_has_moved(backend_key);
    let stale = this.options.machine_catalog ? false : login_moved;
    if (this.options.machine_catalog && !refresh) {
      const previous = this.kept.get(backend_key);
      if (previous) {
        stale =
          (this.now().getTime() - this.probed_at.get(backend_key)!.getTime()) / 1000 >=
          MACHINE_CATALOG_BACKSTOP_SECONDS;
        if (backend_key !== 'hermes') {
          const executable = environment.executable_path(backend_key);
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
      if (waiting && !this.codex_login_has_moved(backend_key)) {
        const shared = this.kept.get(backend_key);
        if (shared) return shared;
      }
      const kept = refresh || stale ? undefined : this.kept.get(backend_key);
      if (kept) return kept;
      const reading_at = this.now();
      const codex_login = backend_key === 'codex' ? this.options.read_codex_login_standing() : null;
      let probed = await this.options.probe(backend_key, environment, {
        refresh,
        discover_update_advisory: refresh && (options.discover_update_advisory ?? true),
        codex_login,
        now: reading_at,
      });
      options.signal?.throwIfAborted();
      if (codex_login !== null) {
        const latest = this.options.read_codex_login_standing();
        if (
          JSON.stringify(latest?.everything_a_card_reads({ now: this.now() }) ?? null) !==
          JSON.stringify(codex_login.everything_a_card_reads({ now: reading_at }))
        ) {
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
      }
      if (stale && !refresh && probed.update_advisory === null) {
        const advisory = this.kept.get(backend_key)?.update_advisory;
        if (advisory != null) probed = { ...probed, update_advisory: advisory };
      }
      const previous = this.kept.get(backend_key);
      if (!login_moved && previous?.models_complete && !probed.models_complete) {
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
      if (backend_key === 'codex' && codex_login !== null)
        this.codex_card_was_probed_against = JSON.stringify(
          codex_login.everything_a_card_reads({ now: reading_at }),
        );
      return probed;
    });
  }
  private codex_login_has_moved(backend_key: ConversationBackendKey): boolean {
    if (backend_key !== 'codex' || this.codex_card_was_probed_against === null) return false;
    return (
      JSON.stringify(
        this.options.read_codex_login_standing()?.everything_a_card_reads({ now: this.now() }) ??
          null,
      ) !== this.codex_card_was_probed_against
    );
  }
  async update_backend(
    backend_key: ConversationBackendKey,
    options: { signal?: AbortSignal } = {},
  ): Promise<BackendUpdateResult> {
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
