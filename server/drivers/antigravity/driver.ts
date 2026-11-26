import { ACP_CHILD_CAPABILITIES } from '../core/capabilities.ts';
import { acp_selection_transition } from '../core/selection.ts';
/** The Antigravity driver. Its runtime loads only when an instance is created. */
import { NO_DRIVER_CONFIG } from '../core/driver.ts';
import type { AgentDriver } from '../core/driver.ts';
import { parse_driver_kind } from '../core/identity.ts';
import { antigravity_client } from './client.ts';
import { antigravity_manifest } from './manifest.ts';
import { antigravity_transcript_usage } from './runtime/transcript_usage.ts';
import { home_path } from '../core/paths.ts';
import { homedir } from 'node:os';
import { antigravity_models } from './models.ts';

export const antigravity_driver: AgentDriver<null> = {
  driver_kind: parse_driver_kind('antigravity'),
  client: antigravity_client,
  manifest: antigravity_manifest,
  // agy reads AGENTS.md (and GEMINI.md) from its workspace.
  home_instructions_file: 'AGENTS.md',
  capabilities: {
    ...ACP_CHILD_CAPABILITIES,
    // agy 1.5.14 resumes itself after an async command completes (live ACP probe).
    turns: { ...ACP_CHILD_CAPABILITIES.turns, wakesOnBackgroundCompletion: true },
  },
  plan_selection_transition: acp_selection_transition,
  config: NO_DRIVER_CONFIG,
  models: antigravity_models,
  transcript_usage: (environment) =>
    antigravity_transcript_usage(
      home_path(antigravity_manifest.home!, environment, homedir(), {
        unset: '||',
        expand_configured: false,
      }),
    ),
  async create(input, host) {
    const { create_antigravity_instance } = await import('./runtime/instance.ts');
    return create_antigravity_instance(input, host);
  },
  async executor() {
    return (await import('./executor.ts')).antigravity_executor;
  },
};
