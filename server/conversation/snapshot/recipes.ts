import type { ConversationBackendKey } from '../contracts.ts';
import type { ProbeRecipe } from './contracts.ts';
export const RECIPES: Readonly<Record<ConversationBackendKey, ProbeRecipe>> = {
  codex: {
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
  claude: {
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
  hermes: {
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
