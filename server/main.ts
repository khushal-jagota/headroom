/** Production entry fails before startup until its required composition is supplied. */
import { run_application_process } from './lifecycle/application.ts';
import { start_production_server } from './lifecycle/production.ts';
import { default_production_dependencies } from './lifecycle/production_bindings.ts';
import { startServer } from './core/start.ts';
import { testModeFromEnvironment } from './core/config.ts';

await run_application_process(
  {
    start: (onShutdownComplete) =>
      testModeFromEnvironment()
        ? startServer({ onShutdownComplete })
        : start_production_server(default_production_dependencies(), onShutdownComplete),
  },
  (code) => process.exit(code),
);
