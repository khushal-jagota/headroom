/** The real Codex implementations of the fixed snapshot and usage boundaries. */
import type { Database } from '../../../core/database/contracts.ts';
import type { OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import type { BackendProviders } from '../../snapshot/composition.ts';
import type { BackendSnapshot, CodexLoginStanding } from '../../snapshot/contracts.ts';
import type {
  CodexLoginRejectionMemory,
  SavedCodexLoginSource,
} from '../../snapshot/provider_contracts.ts';
import { create_durable_codex_login_rejection } from '../../backend_state.ts';
import {
  SubprocessBackendProbeEnvironment,
  backend_io_is_test,
} from '../../snapshot/environment.ts';
import { CodexUsageAdapter } from '../../backend_usage/codex.ts';
import {
  CodexModelCatalogUnavailable,
  CodexSignInUnavailable,
} from '../../snapshot/provider_contracts.ts';
import { read_stored_codex_login } from './login.ts';
import { CodexSignInService } from './login_session.ts';
import { probe_codex_model_catalog } from './model_catalog.ts';
import { read_codex_rate_limits } from './usage.ts';

export function read_codex_login_standing(
  rejection: CodexLoginRejectionMemory | null,
): CodexLoginStanding {
  const stored = read_stored_codex_login();
  const generation = stored.generation;
  const rejected = rejection?.applies_to(generation) ?? false;
  return {
    stored,
    generation,
    rejected,
    everything_a_card_reads({ now }) {
      return [
        generation,
        rejected,
        stored.present,
        stored.readable,
        stored.access_token_has_expired({ now }),
      ];
    },
  };
}
export const saved_codex_login_source: SavedCodexLoginSource = {
  read_stored_codex_login,
  read_codex_login_standing,
};
export interface CodexProviderContext {
  database: Database;
  now(): Date;
  read_codex_snapshot(): Promise<Pick<BackendSnapshot, 'executable_path'>>;
  one_shot_children: OwnedOneShotChildren;
}
export function create_codex_providers(
  context: CodexProviderContext,
): Pick<BackendProviders, 'codex' | 'saved_codex_login' | 'codex_sign_in' | 'usage'> {
  // Production composition calls this only after its independent test-mode refusal.
  const environment = new SubprocessBackendProbeEnvironment(context.one_shot_children);
  const rejection = create_durable_codex_login_rejection(context.database, context.now);
  const probes = new Map<AbortController, Promise<unknown>>();
  let closed = false;
  const sign_in = new CodexSignInService({
    resolve_argv: async () => {
      if (backend_io_is_test())
        throw new CodexSignInUnavailable('Production sign-in is unavailable in tests.');
      const executable = (await context.read_codex_snapshot()).executable_path;
      return executable === null ? null : [executable, 'app-server'];
    },
    remembered_login_rejection: rejection,
    read_login: (home) => {
      if (backend_io_is_test())
        throw new CodexSignInUnavailable('Production sign-in is unavailable in tests.');
      return read_stored_codex_login(home);
    },
    now: context.now,
  });
  return {
    codex: async (executable) => {
      if (closed) throw new CodexModelCatalogUnavailable('Codex model discovery is shut down.');
      if (backend_io_is_test())
        throw new CodexModelCatalogUnavailable(
          'Production catalog reads are unavailable in tests.',
        );
      const controller = new AbortController();
      const operation = probe_codex_model_catalog(executable, { signal: controller.signal });
      probes.set(controller, operation);
      try {
        return await operation;
      } finally {
        probes.delete(controller);
      }
    },
    saved_codex_login: {
      read_stored_codex_login(home) {
        if (backend_io_is_test())
          throw new Error('Production login reads are unavailable in tests.');
        return saved_codex_login_source.read_stored_codex_login(home);
      },
      read_codex_login_standing(memory) {
        if (backend_io_is_test())
          throw new Error('Production login reads are unavailable in tests.');
        return saved_codex_login_source.read_codex_login_standing(memory);
      },
    },
    codex_sign_in: {
      read: () => sign_in.read(),
      start: () => sign_in.start(),
      cancel: (session_id) => sign_in.cancel(session_id),
      async shutdown() {
        closed = true;
        const stopped = new CodexModelCatalogUnavailable('Codex providers stopped at shutdown.');
        for (const controller of probes.keys()) controller.abort(stopped);
        const settled = await Promise.allSettled([
          sign_in.shutdown(),
          environment.shutdown(),
          ...probes.values(),
        ]);
        const failed = settled
          .filter((result) => result.status === 'rejected')
          .filter((result) => result.reason !== stopped);
        if (failed.length)
          throw new AggregateError(
            failed.map((result) => result.reason),
            'Codex provider shutdown failed',
          );
      },
    },
    usage: {
      codex: new CodexUsageAdapter({
        executable_path: (name) => environment.executable_path(name),
        now: context.now,
        read_codex_rate_limits: async (executable, options) => {
          if (closed) throw new Error('Codex usage is shut down.');
          if (backend_io_is_test())
            throw new Error('Production usage reads are unavailable in tests.');
          const controller = new AbortController();
          const signal = options.signal
            ? AbortSignal.any([options.signal, controller.signal])
            : controller.signal;
          const operation = read_codex_rate_limits(executable, { ...options, signal });
          probes.set(controller, operation);
          try {
            return await operation;
          } finally {
            probes.delete(controller);
          }
        },
      }),
    },
  };
}
