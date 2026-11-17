/** The Claude driver. Its runtime loads only when an instance is created. */
import { NO_DRIVER_CONFIG } from '../core/driver.ts';
import type { AgentDriver } from '../core/driver.ts';
import { parse_driver_kind } from '../core/identity.ts';
import { claude_client } from './client.ts';
import { claude_manifest } from './manifest.ts';
import { claude_models } from './models.ts';
import { claude_readable_tool_call_detail } from './tool_detail.ts';

export const claude_driver: AgentDriver<null> = {
  driver_kind: parse_driver_kind('claude'),
  client: claude_client,
  manifest: claude_manifest,
  home_instructions_file: 'CLAUDE.md',
  config: NO_DRIVER_CONFIG,
  models: claude_models,
  readable_tool_call_detail: claude_readable_tool_call_detail,
  async create(input, host) {
    const { create_claude_instance } = await import('./runtime/instance.ts');
    return create_claude_instance(input, host);
  },
  async executor() {
    return (await import('./executor.ts')).claude_executor;
  },
};
