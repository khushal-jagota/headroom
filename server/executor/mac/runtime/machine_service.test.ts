import { mkdirSync, mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, test, vi } from 'vitest';

const home = mkdtempSync(join(tmpdir(), 'panels-helper-'));
mkdirSync(join(home, 'Library/LaunchAgents'), { recursive: true });
vi.mock('node:os', async (original) => ({
  ...(await original<typeof import('node:os')>()),
  userInfo: () => ({ homedir: home }),
}));
vi.mock('../../../environments/release/process.ts', () => ({
  run_release_command: vi.fn(async () => ({ code: 0, stdout: '', stderr: '' })),
}));
const { start_machine_coordinator } = await import('./machine_service.ts');

test.skipIf(process.platform !== 'darwin')(
  'the Mac update helper starts through the signed Panels app and names it',
  async () => {
    const app = '/Applications/Panels.app/Contents/MacOS/Panels';
    await start_machine_coordinator(
      { path: join(home, 'x.plist') } as never,
      'panels-machine-update-a',
      [app, '--panels-worker-spawn-v2', '/node', '/main.ts'],
    );
    const text = readFileSync(
      join(home, 'Library/LaunchAgents/panels-machine-update-a.plist'),
      'utf8',
    );
    expect(text).toContain(
      `<key>AssociatedBundleIdentifiers</key><array><string>com.panels.executor.typescript.app</string></array>`,
    );
    expect(text).toContain(
      `<array><string>${app}</string><string>--panels-worker-spawn-v2</string>`,
    );
  },
);
