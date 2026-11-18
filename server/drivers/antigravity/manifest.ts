/** Antigravity's static facts. Panels runs the `agy` CLI already on PATH and installs nothing. */
import type { DriverManifest } from '../core/manifest.ts';

export const antigravity_manifest: DriverManifest = {
  executable: { name: 'agy', override_environment_name: 'PLAN_ANTIGRAVITY_EXECUTABLE' },
  home: {
    environment_name: 'GEMINI_HOME',
    default_path: '~/.gemini',
    isolated_directory_name: 'antigravity',
    cli_option: '--antigravity-home',
  },
  launcher_environment_names: ['PLAN_ANTIGRAVITY_EXECUTABLE'],
  cli_floor: {
    minimum_version: '1.5.1',
    // Older releases required the `zed` client name.
    feature: "an ACP server that accepts Panels' own client name",
    // `agy --version` prints the bare version, as `1.5.10`.
    version_pattern: /^(\d+(?:\.\d+)+)$/,
  },
  status_recipe: {
    executable_name: 'agy',
    missing_binary_diagnosis: '`agy` is not installed or not on PATH.',
    identity_arguments: null,
    login_command: null,
    registry_package_name: null,
    native_path_marker: null,
    native_update_command: null,
    manual_only_detail: 'Panels does not update `agy`; update this installation manually.',
  },
};
