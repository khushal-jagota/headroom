/** Codex's static facts. */
import type { DriverManifest } from '../core/manifest.ts';

const MODULES = 'agent_backends/node_modules';
const DARWIN_VENDOR = `${MODULES}/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin`;
const LINUX_VENDOR = `${MODULES}/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl`;

export const codex_manifest: DriverManifest = {
  executable: { name: 'codex' },
  home: {
    environment_name: 'CODEX_HOME',
    default_path: '~/.codex',
    isolated_directory_name: 'codex',
    cli_option: '--codex-home',
  },
  launcher_environment_names: ['OPENAI_API_KEY'],
  cli_floor: {
    minimum_version: '0.148.0',
    feature: 'thread-aware app/read',
    version_pattern: /^codex-cli (\S+)$/,
  },
  install: {
    npm_packages: ['@openai/codex'],
    release_files: [`${MODULES}/@openai/codex/package.json`],
    darwin_path_executable: true,
    admission_commands: [
      {
        systems: ['darwin', 'linux'],
        runner: 'node',
        path: `${MODULES}/@openai/codex/bin/codex.js`,
        arguments: ['--version'],
      },
      ...[`${DARWIN_VENDOR}/bin/codex`, `${DARWIN_VENDOR}/codex-path/rg`].map((path) => ({
        systems: ['darwin'] as const,
        runner: 'direct' as const,
        path,
        arguments: ['--version'],
      })),
      {
        systems: ['darwin'],
        runner: 'direct',
        path: `${DARWIN_VENDOR}/bin/codex-code-mode-host`,
        arguments: ['--help'],
      },
      ...[
        `${LINUX_VENDOR}/bin/codex`,
        `${LINUX_VENDOR}/codex-resources/bwrap`,
        `${LINUX_VENDOR}/codex-path/rg`,
      ].map((path) => ({
        systems: ['linux'] as const,
        runner: 'direct' as const,
        path,
        arguments: ['--version'],
      })),
      {
        systems: ['linux'],
        runner: 'direct',
        path: `${LINUX_VENDOR}/bin/codex-code-mode-host`,
        arguments: ['--help'],
      },
    ],
  },
  status_recipe: {
    executable_name: 'codex',
    missing_binary_diagnosis: '`codex` is not installed or not on PATH.',
    identity_arguments: ['login', 'status'],
    login_command: 'codex login',
    registry_package_name: '@openai/codex',
    native_path_marker: '/.codex/packages/standalone/',
    native_update_command: ['codex', 'update'],
    manual_only_detail:
      'Panels only updates `codex` from the configured vps-owned user-local npm prefix; update this installation manually.',
  },
};
