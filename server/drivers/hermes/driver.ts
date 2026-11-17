/** The Hermes driver. Its runtime loads only when an instance is created. */
import { NO_DRIVER_CONFIG } from '../core/driver.ts';
import type { AgentDriver } from '../core/driver.ts';
import { parse_driver_kind } from '../core/identity.ts';
import { hermes_client } from './client.ts';
import { hermes_manifest } from './manifest.ts';
import { hermes_models } from './models.ts';

export const hermes_driver: AgentDriver<null> = {
  driver_kind: parse_driver_kind('hermes'),
  client: hermes_client,
  manifest: hermes_manifest,
  config: NO_DRIVER_CONFIG,
  models: hermes_models,
  async create(input, host) {
    const { create_hermes_instance } = await import('./runtime/instance.ts');
    return create_hermes_instance(input, host);
  },
  async executor() {
    return (await import('./executor.ts')).hermes_executor;
  },
};
