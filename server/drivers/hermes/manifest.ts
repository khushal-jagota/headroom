/** Hermes' static facts. Its Python scripts stay in src/planner. */
import type { DriverManifest } from '../core/manifest.ts';

export const hermes_manifest: DriverManifest = {
  executable: { name: 'hermes' },
  home: { environment_name: 'PLAN_HERMES_HOME', extra_environment_names: ['HERMES_HOME'] },
  install: {
    release_files: [
      'src/planner/conversation/backends/hermes_acp_extension.py',
      'src/planner/conversation/backends/hermes_model_catalog.py',
    ],
  },
};
