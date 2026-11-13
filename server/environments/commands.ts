/** Operator handlers select lazily; metadata belongs to the public CLI catalog. */
import { commands } from '../cli/commands.ts';
import type { CommandDefinition } from '../cli/contracts.ts';
import type { OperatorHandler, OperatorName, OperatorTable } from './command_contracts.ts';

function definition<Name extends OperatorName>(
  name: Name,
  group: 'environment' | 'stalls' | null,
  load: () => Promise<OperatorHandler<Name>>,
): { readonly command: CommandDefinition; readonly handler: OperatorHandler<Name> } {
  const path = group === null ? [name] : [group, name];
  const command = commands.find((item) => !item.group && item.path.join('/') === path.join('/'));
  if (!command) throw new Error('Missing canonical operator command: ' + path.join(' '));
  return { command, handler: async (args, deps) => (await load())(args, deps) };
}
export const operator_commands: OperatorTable = {
  status: definition(
    'status',
    'environment',
    async () => (await import('./cli.ts')).environment_status,
  ),
  cleanup: definition(
    'cleanup',
    'environment',
    async () => (await import('./cli.ts')).environment_cleanup,
  ),
  backup: definition(
    'backup',
    'environment',
    async () => (await import('./cli.ts')).environment_backup,
  ),
  'backup-current': definition(
    'backup-current',
    'environment',
    async () => (await import('./cli.ts')).environment_backup_current,
  ),
  'provision-skills': definition(
    'provision-skills',
    'environment',
    async () => (await import('./cli.ts')).environment_provision_skills,
  ),
  restore: definition(
    'restore',
    'environment',
    async () => (await import('./cli.ts')).environment_restore,
  ),
  prepare: definition(
    'prepare',
    'environment',
    async () => (await import('./cli.ts')).environment_prepare,
  ),
  inspect: definition(
    'inspect',
    'environment',
    async () => (await import('./cli.ts')).environment_inspect,
  ),
  reset: definition(
    'reset',
    'environment',
    async () => (await import('./cli.ts')).environment_reset,
  ),
  remove: definition(
    'remove',
    'environment',
    async () => (await import('./cli.ts')).environment_remove,
  ),
  'app-build': definition(
    'app-build',
    'environment',
    async () => (await import('./release/cli.ts')).app_build,
  ),
  'app-identity': definition(
    'app-identity',
    'environment',
    async () => (await import('./release/cli.ts')).app_identity,
  ),
  run: definition('run', 'environment', async () => (await import('./run_cli.ts')).environment_run),
  serve: definition('serve', null, async () => (await import('../lifecycle/cli.ts')).serve),
  restart: definition('restart', null, async () => (await import('../lifecycle/cli.ts')).restart),
  'server-event-loop-tasks': definition(
    'server-event-loop-tasks',
    null,
    async () => (await import('../lifecycle/cli.ts')).server_event_loop_tasks,
  ),
  watchdog: definition(
    'watchdog',
    'stalls',
    async () => (await import('../lifecycle/cli.ts')).watchdog,
  ),
};
