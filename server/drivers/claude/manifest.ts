/** Claude's static facts. */
import type { DriverManifest } from '../core/manifest.ts';

export const claude_manifest: DriverManifest = {
  executable: { name: 'claude', override_environment_name: 'PLAN_CLAUDE_EXECUTABLE' },
  home: { environment_name: 'CLAUDE_CONFIG_DIR' },
  install: {
    npm_packages: ['@anthropic-ai/claude-code'],
    release_files: [
      'agent_backends/node_modules/@anthropic-ai/claude-code/package.json',
      'server/node_modules/@anthropic-ai/claude-agent-sdk/package.json',
    ],
  },
};
