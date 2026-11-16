/** Private managed-stop operation. Public rendering and dispatch belong to Unit 20. */
import { request_control } from './control.ts';
import { read_stop_capability } from './stop_capability.ts';
import { ServerLifecycleError } from './contracts.ts';
import type { ManagedStopArguments, ManagedStopContext, PinnedSupervisor } from './contracts.ts';
import { CONTROL_EXCHANGE_SECONDS, monotonic_seconds } from './configuration.ts';

export async function stop_managed_supervisor(
  args: ManagedStopArguments,
  context: ManagedStopContext,
): Promise<void> {
  let pinned: PinnedSupervisor | undefined;
  try {
    const { managed_process_support } = await import('./linux_process.ts');
    const support = managed_process_support();
    const record = await read_stop_capability(args.runtime_directory);
    if (monotonic_seconds() >= context.deadline)
      throw new ServerLifecycleError('Managed stop command deadline expired');
    pinned = support.pin(record.supervisor_pid);
    const response = await request_control(
      args.control_socket_path,
      {
        version: 2,
        operation: 'stop_intent',
        incarnation: record.incarnation,
        capability: record.capability,
      },
      Math.min(context.deadline, monotonic_seconds() + CONTROL_EXCHANGE_SECONDS),
    );
    if (
      response.version !== 2 ||
      response.supervisor_pid !== pinned.pid ||
      response.incarnation !== record.incarnation
    )
      throw new ServerLifecycleError(
        'Panels supervisor changed incarnation during stop authorization',
      );
    if (monotonic_seconds() >= context.deadline)
      throw new ServerLifecycleError('Managed stop command deadline expired');
    pinned.signal_term();
    await pinned.wait_for_exit(context.deadline);
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    // File bytes and the authenticated request must never become public diagnostics.
    throw new ServerLifecycleError(
      error instanceof ServerLifecycleError
        ? error.message
        : 'Could not read or use the private managed stop capability',
    );
  } finally {
    pinned?.close();
  }
}
