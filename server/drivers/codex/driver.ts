import { turn_scoped_selection_transition } from '../core/selection.ts';
/** The Codex driver. Its runtime loads only when an instance is created. */
import { NO_DRIVER_CONFIG } from '../core/driver.ts';
import type { AgentDriver } from '../core/driver.ts';
import { parse_driver_kind } from '../core/identity.ts';
import { codex_client } from './client.ts';
import { codex_manifest } from './manifest.ts';
import { codex_transcript_usage } from './runtime/transcript_usage.ts';
import { home_path } from '../core/paths.ts';
import { homedir } from 'node:os';
import { codex_launch_model_refusal, codex_models } from './models.ts';

export const codex_driver: AgentDriver<null> = {
  driver_kind: parse_driver_kind('codex'),
  client: codex_client,
  manifest: codex_manifest,
  home_instructions_file: 'AGENTS.md',
  capabilities: {
    sessions: { supportsModelSwitchInSession: true },
    turns: { supportsActiveSteering: true, wakesOnBackgroundCompletion: false },
  },
  plan_selection_transition: turn_scoped_selection_transition,
  config: NO_DRIVER_CONFIG,
  models: codex_models,
  launch_model_refusal: codex_launch_model_refusal,
  transcript_usage: (environment) =>
    codex_transcript_usage(
      home_path(codex_manifest.home!, environment, homedir(), {
        unset: '||',
        expand_configured: false,
      }),
    ),
  async create(input, host) {
    const { create_codex_instance } = await import('./runtime/instance.ts');
    return create_codex_instance(input, host);
  },
  async executor() {
    return (await import('./executor.ts')).codex_executor;
  },
};
