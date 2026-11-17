/** Claude's static facts. */
import type { DriverManifest } from '../core/manifest.ts';

const MODULES = 'agent_backends/node_modules';
const CLAUDE_EXE = `${MODULES}/@anthropic-ai/claude-code/bin/claude.exe`;

export const claude_manifest: DriverManifest = {
  executable: {
    name: 'claude',
    override_environment_name: 'PLAN_CLAUDE_EXECUTABLE',
    // The retired bundled copy.
    ignored_override_path: CLAUDE_EXE,
  },
  home: {
    environment_name: 'CLAUDE_CONFIG_DIR',
    default_path: '~/.claude',
    isolated_directory_name: 'claude',
    cli_option: '--claude-home',
    // A set CLAUDE_CONFIG_DIR makes macOS Claude read a suffixed Keychain item.
    leave_unset_on_macos: true,
  },
  launcher_environment_names: ['PLAN_CLAUDE_EXECUTABLE', 'ANTHROPIC_API_KEY'],
  cli_floor: {
    minimum_version: '2.1.219',
    feature: 'queued message cancellation on Stop',
    version_pattern: /^(\S+) \(Claude Code\)$/,
  },
  install: {
    admission_environment: { CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1' },
    npm_packages: ['@anthropic-ai/claude-code'],
    release_files: [
      `${MODULES}/@anthropic-ai/claude-code/package.json`,
      'server/node_modules/@anthropic-ai/claude-agent-sdk/package.json',
    ],
    darwin_path_executable: true,
    // The supported Linux runtime uses glibc.
    linux_pruned_paths: ['server/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64-musl'],
    admission_commands: [
      {
        systems: ['darwin', 'linux'],
        runner: 'node',
        path: `${MODULES}/@anthropic-ai/claude-code/cli-wrapper.cjs`,
        arguments: ['--version'],
      },
      ...[
        `${MODULES}/@anthropic-ai/claude-code-darwin-arm64/claude`,
        CLAUDE_EXE,
        'server/node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64/claude',
      ].map((path) => ({
        systems: ['darwin'] as const,
        runner: 'direct' as const,
        path,
        arguments: ['--version'],
      })),
      ...[`${MODULES}/@anthropic-ai/claude-code-linux-x64/claude`, CLAUDE_EXE].map((path) => ({
        systems: ['linux'] as const,
        runner: 'direct' as const,
        path,
        arguments: ['--version'],
      })),
      {
        systems: ['linux'],
        runtime_contracts: [2],
        runner: 'direct',
        path: 'server/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude',
        arguments: ['--version'],
      },
      {
        systems: ['linux'],
        runtime_contracts: [1],
        runner: 'direct',
        path: '.venv/lib/python3.12/site-packages/claude_agent_sdk/_bundled/claude',
        arguments: ['--version'],
      },
    ],
  },
  status_recipe: {
    executable_name: 'claude',
    missing_binary_diagnosis: '`claude` is not installed or not on PATH.',
    identity_arguments: ['auth', 'status', '--json'],
    login_command: 'claude auth login',
    registry_package_name: '@anthropic-ai/claude-code',
    native_path_marker: null,
    native_update_command: null,
    manual_only_detail:
      'Panels only updates `claude` from the configured vps-owned user-local npm prefix; update this installation manually.',
  },
};
