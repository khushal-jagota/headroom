/** Fixed new-unit entry: managed Linux only, without a foreground fallback. */
import {
  application_launch,
  lifecycle_launch_root,
  private_runtime_argument,
  resolve_unit_lifecycle_context,
} from './context.ts';
import { run_server_supervisor } from './supervisor.ts';

try {
  const args = private_runtime_argument(process.argv.slice(2));
  const root = await lifecycle_launch_root();
  const context = await resolve_unit_lifecycle_context(args, process.env, root);
  process.exitCode = await run_server_supervisor({
    mode: {
      kind: 'managed-linux',
      runtime_directory: context.runtime_directory,
      application_root: root,
      database_path: context.config.db_path,
      backup_root: context.config.backup_dir,
    },
    application: application_launch(root, process.env),
    control_socket_path: context.control_socket_path,
    lease_path: context.lease_path,
    port: context.port,
  });
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Panels managed startup failed');
  process.exitCode = 1;
}
