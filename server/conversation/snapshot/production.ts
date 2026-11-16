/** Bind only implemented providers. Route modules never import their transports. */
import {
  probe_claude_model_catalog,
  ClaudeModelCatalogUnavailable as ProviderUnavailable,
} from '../backends/claude_model_catalog.ts';
import { ClaudeModelCatalogUnavailable } from './provider_contracts.ts';
import { unavailable_backend_providers } from './composition.ts';
import type { BackendProviders } from './composition.ts';
import { backend_io_is_test } from './environment.ts';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { ClaudeUsageAdapter } from '../backend_usage/claude.ts';
import { http_get_usage } from '../backend_usage/http.ts';
import { create_codex_providers } from '../backends/codex_app_server/providers.ts';
import type { CodexProviderContext } from '../backends/codex_app_server/providers.ts';
import type { StopOptions } from '../../lifecycle/contracts.ts';
import {
  ModelCatalogCleanupFailed,
  retry_model_catalog_cleanup,
} from '../backends/catalog_cleanup.ts';
export function production_backend_providers(test_mode: true): BackendProviders;
export function production_backend_providers(
  test_mode: boolean,
  context: CodexProviderContext,
): BackendProviders;
export function production_backend_providers(
  test_mode = false,
  context?: CodexProviderContext,
): BackendProviders {
  const providers = unavailable_backend_providers();
  if (test_mode || backend_io_is_test()) return providers;
  if (context === undefined)
    throw new Error('Production backend providers require database and clock context.');
  const children = context.one_shot_children;
  if (!children) throw new Error('Production catalogs require a server-owned child lifetime');
  const codex = create_codex_providers(context);
  const failed_cleanup = new Set<ModelCatalogCleanupFailed>();
  const cleanup_history = new Set<unknown>();
  const probes = new Set<Promise<unknown>>();
  let closed = false;
  let stop_options: StopOptions | undefined;
  return {
    ...providers,
    ...codex,
    codex_sign_in:
      codex.codex_sign_in === null
        ? null
        : {
            read: () => codex.codex_sign_in!.read(),
            start: () => codex.codex_sign_in!.start(),
            cancel: (id) => codex.codex_sign_in!.cancel(id),
            async shutdown(options) {
              stop_options ??= options;
              closed = true;
              const codex_stop = Promise.allSettled([
                Promise.resolve().then(() => codex.codex_sign_in!.shutdown(options)),
              ]);
              const results = await Promise.allSettled([...probes]);
              for (const result of results)
                if (
                  result.status === 'rejected' &&
                  result.reason instanceof ModelCatalogCleanupFailed
                )
                  failed_cleanup.add(result.reason);
              // Codex shutdown joins the common one-shot owner. A Claude native close
              // retry must precede that join when it owns one of those children.
              await retry_model_catalog_cleanup(failed_cleanup, cleanup_history, codex_stop);
            },
          },
    usage: {
      ...codex.usage,
      claude: new ClaudeUsageAdapter({
        credential_path: join(
          process.env.CLAUDE_CONFIG_DIR ?? join(homedir(), '.claude'),
          '.credentials.json',
        ),
        http_get: http_get_usage,
        now: () => new Date(),
      }),
    },
    claude: async (executable) => {
      if (closed) throw new ClaudeModelCatalogUnavailable('Claude model discovery is shut down.');
      if (backend_io_is_test())
        throw new ClaudeModelCatalogUnavailable(
          'Production catalog reads are unavailable in tests.',
        );
      const operation = probe_claude_model_catalog(executable, children, {
        signal: children.signal,
        stop_options: () => stop_options,
      });
      probes.add(operation);
      try {
        return await operation;
      } catch (error) {
        if (error instanceof ModelCatalogCleanupFailed) failed_cleanup.add(error);
        if (error instanceof ProviderUnavailable)
          throw new ClaudeModelCatalogUnavailable(error.message);
        throw error;
      } finally {
        probes.delete(operation);
      }
    },
  };
}
