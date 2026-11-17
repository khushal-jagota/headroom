/** Codex's static facts. */
import type { DriverManifest } from '../core/manifest.ts';

export const codex_manifest: DriverManifest = {
  executable: { name: 'codex' },
  home: { environment_name: 'CODEX_HOME' },
  install: {
    npm_packages: ['@openai/codex'],
    release_files: ['agent_backends/node_modules/@openai/codex/package.json'],
  },
};
