/** The Codex driver. Its runtime loads only when an instance is created. */
import { NO_DRIVER_CONFIG } from '../core/driver.ts';
import type { AgentDriver } from '../core/driver.ts';
import { parse_driver_kind } from '../core/identity.ts';
import { codex_client } from './client.ts';
import { codex_manifest } from './manifest.ts';
import { codex_launch_model_refusal, codex_models } from './models.ts';

export const codex_driver: AgentDriver<null> = {
  driver_kind: parse_driver_kind('codex'),
  client: codex_client,
  manifest: codex_manifest,
  config: NO_DRIVER_CONFIG,
  models: codex_models,
  launch_model_refusal: codex_launch_model_refusal,
  async create(input, host) {
    const { create_codex_instance } = await import('./runtime/instance.ts');
    return create_codex_instance(input, host);
  },
};
