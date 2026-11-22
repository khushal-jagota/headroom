// Updater tests exercise the compiled native logic without a browser or app host.
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const temporary = await mkdtemp(join(tmpdir(), 'panels-desktop-updates-'));
const compiled = join(temporary, 'build');
let status = 1;
try {
  const compile = spawnSync(process.execPath, [
    join(desktop, 'node_modules/typescript/bin/tsc'), '-p', desktop, '--outDir', compiled,
  ], { stdio: 'inherit' });
  status = compile.status ?? 1;
  if (status === 0) {
    const tests = spawnSync(process.execPath, [
      '--test', join(desktop, 'src/updates/updates.test.cjs'),
    ], { stdio: 'inherit', env: { ...process.env, PANELS_DESKTOP_TEST_BUILD: compiled } });
    status = tests.status ?? 1;
  }
} finally {
  await rm(temporary, { recursive: true, force: true });
}
process.exitCode = status;
