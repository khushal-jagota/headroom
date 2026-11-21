/** The Antigravity driver. Its runtime loads only when an instance is created. */
import { NO_DRIVER_CONFIG } from '../core/driver.ts';
import type { AgentDriver } from '../core/driver.ts';
import { parse_driver_kind } from '../core/identity.ts';
import { antigravity_client } from './client.ts';
import { antigravity_manifest } from './manifest.ts';
import { antigravity_models } from './models.ts';

export const antigravity_driver: AgentDriver<null> = {
  driver_kind: parse_driver_kind('antigravity'),
  client: antigravity_client,
  manifest: antigravity_manifest,
  // agy reads AGENTS.md (and GEMINI.md) from its workspace.
  home_instructions_file: 'AGENTS.md',
  config: NO_DRIVER_CONFIG,
  models: antigravity_models,
  async create(input, host) {
    const { create_antigravity_instance } = await import('./runtime/instance.ts');
    return create_antigravity_instance(input, host);
  },
  async executor() {
    return (await import('./executor.ts')).antigravity_executor;
  },
};
