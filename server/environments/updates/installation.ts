import { createHash } from 'node:crypto';
import { InstallationRecord } from './installer/record.ts';
/** The application owns only the launch client. The installer service outlives it. */
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import type { InstallerOperations } from './contracts.ts';
import { create_migration_status } from './migration_status.ts';
import { run_release_command } from '../release/process.ts';
import { accessSync, constants, mkdirSync, readFileSync, realpathSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { installer_attempt_id, INSTALLER_ENTRY } from './installer/configuration.ts';
import { replace_existing_json, replace_existing_text } from './attempts.ts';
import { pin_existing, selected_release } from './installer/selection.ts';
import {
  assert_installer_health_url,
  assert_installer_path,
  installer_test_mode,
} from './installer/configuration.ts';
import { AppValidationError } from '../release/contracts.ts';
import { INSTALLER_LAUNCH_TIMEOUT_SECONDS } from './configuration.ts';
import { DATABASE_SNAPSHOT_TIMEOUT_SECONDS } from './installer/configuration.ts';
import { is_staged_copy } from '../backup/capture.ts';
import { installer_unit, selected_installer_environment } from './installation_command.ts';

export function create_installer_operations(inputs: {
  readonly application_root: string;
  readonly child_processes: OwnedChildProcesses;
  readonly signal: AbortSignal;
  readonly environment: Readonly<NodeJS.ProcessEnv>;
  readonly darwin_home?: import('./darwin/service.ts').DarwinHomeService;
}): InstallerOperations {
  const environment = Object.freeze({ ...inputs.environment });
  const prepared = new Map<string, readonly string[]>();
  return {
    ...(inputs.darwin_home ? { requires_snapshot: true } : {}),
    ...create_migration_status(),
    async stage_snapshot(database, backup_directory) {
      for (const path of [inputs.application_root, database, backup_directory])
        assert_installer_path(path, environment);
      const result = await run_release_command(
        [
          join(inputs.application_root, 'node/bin/node'),
          join(inputs.application_root, 'server/environments/updates/snapshot_stage_main.ts'),
          database,
          backup_directory,
        ],
        { timeout_seconds: DATABASE_SNAPSHOT_TIMEOUT_SECONDS, environment: { ...environment } },
        { child_processes: inputs.child_processes, signal: inputs.signal, deadline: null },
      );
      const staged = result.stdout.trim();
      if (!(await is_staged_copy(staged, backup_directory)))
        throw new AppValidationError('The staged snapshot is outside the backup directory');
      return staged;
    },
    prepare(options) {
      inputs.signal.throwIfAborted();
      for (const path of [
        inputs.application_root,
        options.current_root,
        options.database,
        options.data_directory,
        options.backup_directory,
      ])
        assert_installer_path(path, environment);
      assert_installer_health_url(options.health_url, environment);
      if (realpathSync(inputs.application_root) !== selected_release(options.current_root))
        throw new AppValidationError('The running home differs from the installation to update');
      const manifest = JSON.parse(
        readFileSync(join(inputs.application_root, 'manifest.json'), 'utf8'),
      );
      if (manifest.runtime_contract?.version !== 2)
        throw new AppValidationError(
          'The running installation requires a sealed Node version-2 home release',
        );
      for (const [file, mode] of [
        ['node/bin/node', constants.X_OK],
        [INSTALLER_ENTRY, constants.R_OK],
      ] as const)
        accessSync(join(inputs.application_root, file), mode);
      const runtime = pin_existing(options.current_root, manifest.app_sha);
      const directory = join(
        options.data_directory,
        'update-installer-' + installer_attempt_id(options.attempt_id),
      );
      mkdirSync(directory, { mode: 0o700 });
      const request = join(directory, 'request.json');
      const node = join(runtime, 'node/bin/node');
      replace_existing_json(request, { ...options, node_executable: node });
      const command = [node, join(runtime, INSTALLER_ENTRY), request];
      prepared.set(options.attempt_id, Object.freeze([...command]));
      return [...command];
    },
    async launch(attempt_id, command) {
      inputs.signal.throwIfAborted();
      const expected = prepared.get(attempt_id);
      if (
        !expected ||
        expected.length !== command.length ||
        expected.some((part, index) => part !== command[index])
      )
        throw new AppValidationError('installer launch requires its prepared retained release');
      if (installer_test_mode(environment))
        throw new AppValidationError('Installer service launch is unavailable in tests');
      prepared.delete(attempt_id);
      if (inputs.darwin_home) {
        const { launch_darwin_installer } = await import('./darwin/launch.ts');
        await launch_darwin_installer(inputs.darwin_home, attempt_id, command);
        return;
      }
      const deadline = performance.now() / 1000 + INSTALLER_LAUNCH_TIMEOUT_SECONDS;
      const data = dirname(dirname(command[2]!));
      const unit =
        'panels-home-update-' +
        createHash('sha256').update(data).digest('hex').slice(0, 24) +
        '.service';
      const path = join(data, unit);
      replace_existing_text(
        path,
        installer_unit(command, join(data, 'update-barrier.json'), environment),
      );
      const systemctl = (...args: string[]) =>
        run_release_command(
          ['systemctl', '--user', ...args],
          { timeout_seconds: null, environment: selected_installer_environment(environment) },
          { child_processes: inputs.child_processes, signal: inputs.signal, deadline },
        );
      await systemctl('enable', path);
      await systemctl('daemon-reload');
      const fragment = await systemctl('show', unit, '--property=FragmentPath', '--value');
      if (realpathSync(fragment.stdout.trim()) !== realpathSync(path))
        throw new AppValidationError('The recovery service belongs to another home');
      new InstallationRecord({ data_directory: data, attempt_id }).record('coordinator_registered');
      await systemctl('restart', '--no-block', unit);
      inputs.signal.throwIfAborted();
    },
  };
}
