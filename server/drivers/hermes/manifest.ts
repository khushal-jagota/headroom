/** Hermes' static facts. Its Python scripts stay in src/planner. */
import type { DriverManifest } from '../core/manifest.ts';

export const hermes_manifest: DriverManifest = {
  executable: {
    name: 'hermes',
    configured_beside: {
      environment_name: 'PLAN_HERMES_PYTHON',
      default_path: '~/.hermes/hermes-agent/venv/bin/python',
    },
  },
  home: {
    environment_name: 'PLAN_HERMES_HOME',
    extra_environment_names: ['HERMES_HOME'],
    default_path: '~/.hermes',
    isolated_directory_name: 'hermes',
    cli_option: '--hermes-home',
  },
  launcher_environment_names: ['PLAN_HERMES_PYTHON'],
  reserved_environment_prefixes: ['HERMES_'],
  install: {
    release_files: [
      'src/planner/conversation/backends/hermes_acp_extension.py',
      'src/planner/conversation/backends/hermes_model_catalog.py',
    ],
  },
  status_recipe: {
    executable_name: 'hermes',
    missing_binary_diagnosis:
      'Hermes is not installed at the configured PLAN_HERMES_PYTHON environment.',
    identity_arguments: null,
    login_command: null,
    registry_package_name: null,
    native_path_marker: null,
    native_update_command: null,
    manual_only_detail:
      "Panels cannot confirm this Hermes installation is managed by Hermes' native updater, so it offers no update here.",
  },
};
