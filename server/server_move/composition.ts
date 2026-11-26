/** One server's move services, both roles, composed with its environment services. */
import { existsSync } from 'node:fs';
import type { FastifyPluginAsync } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import type { Config } from '../core/config.ts';
import { update_repository_configuration } from '../core/config.ts';
import type { Database } from '../core/database/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { UpdateBarrier } from '../environments/update_barrier.ts';
import { SERVER_MOVE_PROBE_TIMEOUT_SECONDS } from './configuration.ts';
import { HeldServerMove } from './held_service.ts';
import { ServerPackageCache, type ServerPackageSource } from './packages/cache.ts';
import { create_server_move_executor_routes, create_server_move_owner_routes } from './routes.ts';
import { ServerMoveCoordinator } from './service.ts';

export interface ServerMoveComposition {
  readonly coordinator: ServerMoveCoordinator;
  readonly held: HeldServerMove;
  readonly owner_routes: FastifyPluginAsync<AreaServices>;
  readonly executor_routes: FastifyPluginAsync;
}

/** The commit check and the committing probe: one HTTPS JSON read of the new address. */
export async function probe_new_address(origin: string, path: string): Promise<unknown> {
  const response = await fetch(new URL(path, origin), {
    redirect: 'error',
    headers: { accept: 'application/json' },
    signal: AbortSignal.timeout(SERVER_MOVE_PROBE_TIMEOUT_SECONDS * 1000),
  });
  if (response.status === 404) {
    await response.body?.cancel().catch(() => {});
    return null;
  }
  if (!response.ok) {
    await response.body?.cancel().catch(() => {});
    throw new Error(`The new address answered ${response.status}.`);
  }
  return response.json();
}

export function create_server_move(input: {
  readonly config: Config;
  readonly database: Database;
  readonly data_directory: string;
  readonly database_path: string;
  readonly update_barrier: UpdateBarrier;
  readonly update_running: () => boolean;
  readonly conversation: () => ConversationSystem | null;
  readonly package_source: ServerPackageSource | null;
  readonly emit_change: () => void;
  readonly probe?: (origin: string, path: string) => Promise<unknown>;
}): ServerMoveComposition {
  const { config, data_directory } = input;
  const update_token = update_repository_configuration(process.env, data_directory)?.token_file;
  const coordinator = new ServerMoveCoordinator({
    database: input.database,
    data_directory,
    database_path: input.database_path,
    state_paths: () => ({
      server_root: data_directory,
      database_path: input.database_path,
      credential_key_path: config.credential_key_path,
      apns_key_path: config.apns?.key_path ?? null,
      // An update token that is configured but absent is not state.
      update_token_path: update_token && existsSync(update_token) ? update_token : null,
    }),
    revision: config.app_sha,
    canonical_origin: config.trusted_ingress_canonical_origin,
    trusted_ingress:
      config.trusted_ingress_provider && config.trusted_ingress_allowed_login
        ? {
            provider: config.trusted_ingress_provider,
            allowed_login: config.trusted_ingress_allowed_login,
          }
        : null,
    update_barrier: input.update_barrier,
    update_running: input.update_running,
    conversation: input.conversation,
    packages: new ServerPackageCache(data_directory, input.package_source),
    probe: input.probe ?? probe_new_address,
    emit_change: input.emit_change,
  });
  const held = new HeldServerMove({
    database: input.database,
    data_directory,
    update_barrier: input.update_barrier,
    conversation: input.conversation,
  });
  held.recover();
  coordinator.recover();
  return {
    coordinator,
    held,
    owner_routes: create_server_move_owner_routes(coordinator, held),
    executor_routes: create_server_move_executor_routes(
      { config, database: input.database, data_directory },
      coordinator,
      held,
    ),
  };
}
