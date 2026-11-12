/** Export-only direct function comparison. All IO and host answers are synthetic. */
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { loadConfig } from '../../core/config.ts';
import { environment_commands } from '../commands.ts';
import { AppManifestValidationFailure } from '../command_contracts.ts';
import type {
  EnvironmentCommandArguments,
  EnvironmentCommandName,
  EnvironmentCommandResult,
  HostEnvironmentCommandDependencies,
} from '../command_contracts.ts';

interface Call {
  command: EnvironmentCommandName;
  args: unknown;
  manifest?: string;
  validator_failure?: 'os' | 'value' | 'runtime' | 'unexpected';
}
export async function command_probe(root: string, calls: readonly Call[]): Promise<unknown> {
  const database = join(root, 'runtime/database.sqlite');
  const config = loadConfig(join(root, 'config.yaml'), {
    PLAN_TEST_MODE: '1',
    PLAN_DB_PATH: database,
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_APP_ROOT: join(root, 'app'),
    PLAN_APP_SHA: 'a'.repeat(40),
    PLAN_TICKET_SCRATCH_ROOT: join(root, 'git/panels'),
    PLAN_TICKET_SCRATCH_DATABASE: database,
  });
  const deps: HostEnvironmentCommandDependencies = {
    load_config: () => config,
    now_unix: () => 1_800_000_000,
    status_options: {
      application_root: join(root, 'app'),
      dependencies: {
        now: () => '2026-11-05T12:00:00+00:00',
        platform_name: () => 'Darwin',
        statvfs: async () => ({ f_frsize: 4096, f_blocks: 100_000_000, f_bavail: 60_000_000 }),
        process_lines: async () => [],
        git_worktree_output: async () => '',
        live_reference_paths: async () => [],
      },
    },
  };
  const results: unknown[] = [];
  for (const call of calls) {
    if (call.manifest !== undefined)
      await writeFile(join(root, 'app/manifest.json'), call.manifest);
    try {
      let result: EnvironmentCommandResult;
      if (call.command === 'status' || call.command === 'cleanup') {
        const handler = environment_commands[call.command].handler as (
          args: EnvironmentCommandArguments['cleanup'],
          deps: HostEnvironmentCommandDependencies,
        ) => Promise<EnvironmentCommandResult>;
        result = await handler(call.args as EnvironmentCommandArguments['cleanup'], deps);
      } else if (call.command === 'backup-current') {
        result = await environment_commands['backup-current'].handler(
          call.args as EnvironmentCommandArguments['backup-current'],
          {
            validate_app_manifest: async () => {
              if (call.validator_failure === 'unexpected')
                throw Object.assign(new Error('synthetic unexpected validator failure'), {
                  name: 'LookupError',
                });
              throw new AppManifestValidationFailure(
                call.validator_failure ?? 'value',
                'app artifact digest does not match its manifest',
              );
            },
          },
        );
      } else {
        const handler = environment_commands[call.command].handler as (
          args: unknown,
          deps: undefined,
        ) => Promise<EnvironmentCommandResult>;
        result = await handler(call.args, undefined);
      }
      results.push(result);
    } catch (error) {
      results.push({ thrown: (error as Error).name });
    }
  }
  return {
    results,
    table: Object.fromEntries(
      Object.entries(environment_commands).map(([name, definition]) => [
        name,
        { path: definition.path, options: definition.options },
      ]),
    ),
  };
}
