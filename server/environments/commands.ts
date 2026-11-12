/** Python option declarations as data. No parsing or dispatcher registration. */
import type { EnvironmentCommandOption, EnvironmentCommandTable } from './command_contracts.ts';
import {
  environment_status,
  environment_cleanup,
  environment_backup,
  environment_backup_current,
  environment_provision_skills,
  environment_restore,
  environment_prepare,
  environment_inspect,
  environment_reset,
  environment_remove,
} from './cli.ts';

const registry: readonly EnvironmentCommandOption[] = [
  { name: '--instance-id', type: 'string', default: null },
  { name: '--environment-root', type: 'path', required: true },
  { name: '--port', type: 'integer', default: null },
  { name: '--credentials-env-file', type: 'path', default: null },
  {
    name: '--repository-root',
    destination: 'repository_roots',
    type: 'path',
    multiple: true,
    default: [],
    path: { exists: true, file_okay: false },
  },
  { name: '--json', destination: 'json', type: 'boolean', default: false },
];
const source: readonly EnvironmentCommandOption[] = [
  { name: '--source-db', type: 'path', required: true, path: { exists: true, dir_okay: false } },
  { name: '--backup-dir', type: 'path', required: true, path: { file_okay: false } },
];
export const environment_commands: EnvironmentCommandTable = {
  status: {
    path: ['environment', 'status'],
    options: [{ name: '--json', destination: 'json', type: 'boolean', default: false }],
    handler: environment_status,
  },
  cleanup: {
    path: ['environment', 'cleanup'],
    options: [
      { name: '--apply', type: 'boolean', default: false },
      { name: '--json', destination: 'json', type: 'boolean', default: false },
    ],
    handler: environment_cleanup,
  },
  backup: {
    path: ['environment', 'backup'],
    options: [...source, { name: '--deployed-revision', type: 'string', required: true }],
    handler: environment_backup,
  },
  'backup-current': {
    path: ['environment', 'backup-current'],
    options: [
      ...source,
      {
        name: '--current-app',
        type: 'path',
        required: true,
        path: { exists: true, file_okay: false },
      },
    ],
    handler: environment_backup_current,
  },
  'provision-skills': {
    path: ['environment', 'provision-skills'],
    options: [
      {
        name: '--database-parent',
        type: 'path',
        required: true,
        path: { exists: true, file_okay: false },
      },
      ...['hermes', 'codex', 'claude'].map((name) => ({
        name: `--${name}-home`,
        type: 'path' as const,
        required: true,
        path: { file_okay: false },
      })),
    ],
    handler: environment_provision_skills,
  },
  restore: {
    path: ['environment', 'restore'],
    options: [
      {
        name: '--snapshot',
        type: 'path',
        required: true,
        path: { exists: true, file_okay: false },
      },
      { name: '--destination-db', type: 'path', required: true, path: { dir_okay: false } },
      { name: '--live-stopped', type: 'boolean', required: true },
    ],
    handler: environment_restore,
  },
  prepare: {
    path: ['environment', 'prepare'],
    options: [
      { name: '--kind', type: 'choice', choices: ['live', 'staging'], required: true },
      ...registry,
    ],
    handler: environment_prepare,
  },
  inspect: {
    path: ['environment', 'inspect'],
    options: [
      { name: '--kind', type: 'choice', choices: ['live', 'staging'], required: true },
      ...registry,
    ],
    handler: environment_inspect,
  },
  reset: {
    path: ['environment', 'reset'],
    options: [
      { name: '--kind', type: 'choice', choices: ['staging'], required: true },
      ...registry,
    ],
    handler: environment_reset,
  },
  remove: {
    path: ['environment', 'remove'],
    options: [
      { name: '--kind', type: 'choice', choices: ['staging'], required: true },
      ...registry,
    ],
    handler: environment_remove,
  },
};
