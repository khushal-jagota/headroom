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
  return {
    ...providers,
    ...codex,
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
      if (backend_io_is_test())
        throw new ClaudeModelCatalogUnavailable(
          'Production catalog reads are unavailable in tests.',
        );
      try {
        return await probe_claude_model_catalog(executable, children);
      } catch (error) {
        if (error instanceof ProviderUnavailable)
          throw new ClaudeModelCatalogUnavailable(error.message);
        throw error;
      }
    },
  };
}
