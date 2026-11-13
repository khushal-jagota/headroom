/** Fixed foreground entry. The supervisor, not APP, owns ordinary signals. */
import {
  application_launch,
  lifecycle_launch_root,
  resolve_unit_lifecycle_context,
} from './context.ts';
import { run_server_supervisor } from './supervisor.ts';

try {
  if (process.argv.length !== 2)
    throw new Error('Foreground Panels entrypoint accepts no arguments');
  const root = await lifecycle_launch_root();
  const context = await resolve_unit_lifecycle_context(
    { runtime_directory: '' },
    process.env,
    root,
  );
  process.exitCode = await run_server_supervisor({
    mode: { kind: 'foreground' },
    application: application_launch(root, process.env),
    control_socket_path: context.control_socket_path,
    lease_path: context.lease_path,
    port: context.port,
  });
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Panels foreground startup failed');
  process.exitCode = 1;
}
