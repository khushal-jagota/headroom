/** Fixed authenticated stop entry. Its deadline includes address/config resolution. */
import {
  lifecycle_launch_root,
  private_runtime_argument,
  resolve_unit_lifecycle_context,
} from './context.ts';
import { stop_managed_supervisor } from './managed_stop.ts';
import { MANAGED_STOP_CLIENT_SECONDS, monotonic_seconds } from './configuration.ts';

const deadline = monotonic_seconds() + MANAGED_STOP_CLIENT_SECONDS;
try {
  const args = private_runtime_argument(process.argv.slice(2));
  const root = await lifecycle_launch_root();
  const context = await resolve_unit_lifecycle_context(args, process.env, root);
  await stop_managed_supervisor(context, { deadline });
} catch (error) {
  console.error(error instanceof Error ? error.message : 'Panels managed stop failed');
  process.exitCode = 1;
}
