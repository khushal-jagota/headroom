import fs from 'node:fs';
import { join } from 'node:path';

function write(path: string, text: string, mode = 0o644): void {
  fs.mkdirSync(join(path, '..'), { recursive: true });
  fs.writeFileSync(path, text, { mode });
}
/** Only the classifier consumes this structure. This is not an admitted release. */
export function structural_app(app: string, sha: string): void {
  const contract = { version: 2 };
  write(
    join(app, 'manifest.json'),
    JSON.stringify({
      format: 'panels-app-v1',
      app_sha: sha,
      source_digest: 'd'.repeat(64),
      artifact_digest: 'e'.repeat(64),
      runtime_contract: contract,
    }),
  );
  write(join(app, 'runtime/contract.json'), JSON.stringify(contract));
  for (const file of ['bin/panels', 'bin/panels-launcher'])
    write(join(app, file), '#!/bin/sh\nexit 99\n', 0o755);
  for (const file of [
    'server/environments/release/launcher_main.ts',
    'server/lifecycle/foreground_main.ts',
    'server/lifecycle/managed_main.ts',
    'server/lifecycle/application.ts',
    'server/lifecycle/stop_main.ts',
    'server/stalls/watchdog_main.ts',
    'server/environments/updates/interruption_capture_main.ts',
    'server/environments/updates/restore_main.ts',
    'server/environments/updates/installer/main.ts',
  ])
    write(join(app, file), '// Inert structural routing fixture. Never execute.\n');
}
