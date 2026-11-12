/** Replaceable application entry. Signal handling and ordered close stay in startServer. */
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolved_path } from '../environments/paths.ts';
import { start_production_server } from './production.ts';
import { default_production_dependencies } from './production_bindings.ts';
import type { ApplicationEntrypoint } from './contracts.ts';

export async function run_application_process(application: ApplicationEntrypoint): Promise<void> {
  await application.start();
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    process.chdir(await resolved_path(fileURLToPath(new URL('../../', import.meta.url))));
    await run_application_process({
      start: () => start_production_server(default_production_dependencies()),
    });
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Panels application startup failed');
    process.exitCode = 1;
  }
}
