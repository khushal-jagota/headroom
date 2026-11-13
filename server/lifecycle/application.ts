/** Replaceable application entry. Signal handling and ordered close stay in startServer. */
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolved_path } from '../environments/paths.ts';
import { startServer } from '../core/start.ts';
import { testModeFromEnvironment } from '../core/config.ts';
import { start_production_server } from './production.ts';
import { default_production_dependencies } from './production_bindings.ts';
import type { ApplicationEntrypoint } from './contracts.ts';
import { writeSync } from 'node:fs';

/** Only an actual process entry supplies its terminal exit capability. */
export async function run_application_process(
  application: ApplicationEntrypoint,
  exit: (code: 0 | 1) => void,
): Promise<void> {
  let completed = false;
  const complete = (code: 0 | 1): void => {
    if (completed) return;
    completed = true;
    try {
      writeSync(
        2,
        `Panels process exit ${code}; active resource types: ${process.getActiveResourcesInfo().join(', ')}\n`,
      );
    } finally {
      exit(code);
    }
  };
  try {
    await application.start(complete);
  } catch (error) {
    if (completed) return;
    try {
      writeSync(
        2,
        `${error instanceof Error ? error.message : 'Panels application startup failed'}\n`,
      );
    } finally {
      complete(1);
    }
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    process.chdir(await resolved_path(fileURLToPath(new URL('../../', import.meta.url))));
    await run_application_process(
      {
        start: (onShutdownComplete) =>
          testModeFromEnvironment()
            ? startServer({ onShutdownComplete })
            : start_production_server(default_production_dependencies(), onShutdownComplete),
      },
      (code) => process.exit(code),
    );
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Panels application startup failed');
    process.exit(1);
  }
}
