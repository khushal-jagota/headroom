/** The application owns only the launch client. The installer service outlives it. */
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import type { InstallerOperations } from './contracts.ts';
import { create_migration_status } from './migration_status.ts';
import { create_database_backup } from '../backup/capture.ts';
import { run_release_command } from '../release/process.ts';
import { copy_installer_runtime } from '../release/installer_bundle.ts';
import {
  assert_installer_health_url,
  assert_installer_path,
  installer_test_mode,
} from './installer/configuration.ts';
import { AppValidationError } from '../release/contracts.ts';
import { INSTALLER_LAUNCH_TIMEOUT_SECONDS } from './configuration.ts';
import {
  installer_transient_command,
  selected_installer_environment,
} from './installation_command.ts';

export function create_installer_operations(inputs: {
  readonly application_root: string;
  readonly node_executable: string;
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
    create_backup: create_database_backup,
    prepare(options) {
      inputs.signal.throwIfAborted();
      for (const path of [
        inputs.application_root,
        options.current_root,
        options.database,
        options.data_directory,
      ])
        assert_installer_path(path, environment);
      assert_installer_health_url(options.health_url, environment);
      const command = copy_installer_runtime(
        inputs.application_root,
        {
          ...options,
          node_executable: inputs.darwin_home?.identity.installer_node ?? inputs.node_executable,
        },
        inputs.signal,
      );
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
        throw new AppValidationError('installer launch requires its existing prepared copy');
      if (installer_test_mode(environment))
        throw new AppValidationError('Installer service launch is unavailable in tests');
      prepared.delete(attempt_id);
      if (inputs.darwin_home) {
        const { launch_darwin_installer } = await import('./darwin/launch.ts');
        await launch_darwin_installer(inputs.darwin_home, attempt_id, command);
        return;
      }
      const deadline = performance.now() / 1000 + INSTALLER_LAUNCH_TIMEOUT_SECONDS;
      await run_release_command(
        installer_transient_command(attempt_id, command, environment),
        {
          timeout_seconds: null,
          environment: selected_installer_environment(environment),
        },
        { child_processes: inputs.child_processes, signal: inputs.signal, deadline },
      );
      inputs.signal.throwIfAborted();
    },
  };
}
