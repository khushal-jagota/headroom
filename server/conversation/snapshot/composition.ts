/** Production receives provider implementations as values. Missing ones stay explicit. */
import type { Database } from '../../core/database/contracts.ts';
import type { Config } from '../../core/config.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type { BackendProbeEnvironment, BackendHttpRuntime } from './contracts.ts';
import type {
  CodexModelCatalogSource,
  ClaudeModelCatalogSource,
  HermesModelCatalogSource,
  SavedCodexLoginSource,
  CodexSignInService,
} from './provider_contracts.ts';
import type { BackendUsageAdapter } from '../backend_usage/contracts.ts';
import type { ConversationBackendKey } from '../contracts.ts';
import type { RemoteCatalogSource } from '../model_resolution/contracts.ts';
import { BackendSnapshotService } from './service.ts';
import { create_backend_probe } from './probe.ts';
import { create_catalog_readers } from './catalogs/readers.ts';
import { BackendUsageService } from '../backend_usage/service.ts';
import { BackendStateStore, create_durable_codex_login_rejection } from '../backend_state.ts';
import { SubprocessBackendProbeEnvironment, backend_io_is_test } from './environment.ts';
import { RecordingBackendProbeEnvironment } from './recording_environment.ts';
import { observe_shutdown } from '../shutdown.ts';
import { ModelCatalogCleanupFailed } from '../backends/catalog_cleanup.ts';
export interface BackendProviders {
  readonly codex: CodexModelCatalogSource | null;
  readonly claude: ClaudeModelCatalogSource | null;
  readonly hermes: HermesModelCatalogSource | null;
  readonly saved_codex_login: SavedCodexLoginSource | null;
  readonly codex_sign_in: CodexSignInService | null;
  readonly usage: Readonly<Partial<Record<ConversationBackendKey, BackendUsageAdapter>>>;
  readonly machine_catalog: () => RemoteCatalogSource | null;
}
export function create_backend_runtime(options: {
  readonly database: Database;
  readonly config: Config;
  readonly now: () => Date;
  readonly providers: BackendProviders;
  readonly environment?: BackendProbeEnvironment;
  readonly one_shot_children?: OwnedOneShotChildren;
}): BackendHttpRuntime {
  const { database, providers, now } = options;
  const environment: BackendProbeEnvironment =
    options.environment ??
    (options.config.test_mode || backend_io_is_test()
      ? new RecordingBackendProbeEnvironment()
      : new SubprocessBackendProbeEnvironment(require_children(options.one_shot_children)));
  const rejection = create_durable_codex_login_rejection(database, now);
  let resolve_resource_join!: () => void, reject_resource_join!: (error: unknown) => void;
  const resource_join = new Promise<void>((resolve, reject) => {
    resolve_resource_join = resolve;
    reject_resource_join = reject;
  });
  let shutdown_result: Promise<void> | undefined;
  const cleanup_history: unknown[] = [];
  const retained_cleanup = (error: unknown): boolean =>
    error instanceof ModelCatalogCleanupFailed ||
    (error instanceof AggregateError && error.errors.some(retained_cleanup));
  return {
    shutdown(options = {}) {
      if (shutdown_result === undefined) {
        let resolve_attempt!: () => void, reject_attempt!: (error: unknown) => void;
        const attempt = new Promise<void>((resolve, reject) => {
          resolve_attempt = resolve;
          reject_attempt = reject;
        });
        // Publish before either cleanup can reenter shutdown. A failed catalog
        // close keeps the actual join pending and permits a later cleanup attempt.
        shutdown_result = attempt;
        void (async () => {
          const environment_stop = Promise.allSettled([
            Promise.resolve().then(() => environment.shutdown?.()),
          ]);
          const provider_stop = await Promise.allSettled([
            Promise.resolve().then(() => providers.codex_sign_in?.shutdown()),
          ]);
          // A failed provider close must escape before the common process-close
          // barrier: that barrier can remain pending until a later close retry.
          const pending_cleanup = provider_stop.filter(
            (result): result is PromiseRejectedResult =>
              result.status === 'rejected' && retained_cleanup(result.reason),
          );
          if (pending_cleanup.length) {
            const error = new AggregateError(
              pending_cleanup.map((result) => result.reason),
              'Backend shutdown failed',
            );
            cleanup_history.push(...pending_cleanup.map((result) => result.reason));
            if (shutdown_result === attempt) shutdown_result = undefined;
            reject_attempt(error);
            return;
          }
          const results = [...provider_stop, ...(await environment_stop)];
          const failures = results.filter((result) => result.status === 'rejected');
          if (failures.length) {
            cleanup_history.push(...failures.map((failure) => failure.reason));
          }
          if (cleanup_history.length)
            reject_resource_join(new AggregateError(cleanup_history, 'Backend shutdown failed'));
          else resolve_resource_join();
          void resource_join.then(resolve_attempt, reject_attempt);
        })();
      }
      return observe_shutdown(
        shutdown_result,
        options,
        'Backend resources remain unfinished at the shutdown deadline.',
      );
    },
    settled() {
      return resource_join;
    },
    database,
    backend_snapshots: new BackendSnapshotService({
      environment,
      now,
      read_codex_login_standing: () =>
        providers.saved_codex_login?.read_codex_login_standing(rejection) ?? null,
      probe: create_backend_probe(create_catalog_readers(providers)),
    }),
    backend_state: new BackendStateStore(database),
    backend_usage: new BackendUsageService(providers.usage),
    codex_sign_in: providers.codex_sign_in,
    machine_catalog: providers.machine_catalog,
  };
}
function require_children(children: OwnedOneShotChildren | undefined): OwnedOneShotChildren {
  if (!children) throw new Error('Production backend probes require a server-owned child lifetime');
  return children;
}
export function unavailable_backend_providers(): BackendProviders {
  return {
    codex: null,
    claude: null,
    hermes: null,
    saved_codex_login: null,
    codex_sign_in: null,
    usage: {},
    machine_catalog: () => null,
  };
}
