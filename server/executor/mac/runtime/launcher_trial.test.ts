/** The same app exchange and journal run on Linux and Darwin. Native signing and launchd
 * are exercised on the hosted Mac; only the platform copy and probe are replaced here. */
import { cp, mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { expect, test, vi } from 'vitest';
import { mac_installation_paths, MAC_RUNTIME_LAUNCHER_APP } from '../platform.ts';
import { supervise_launcher, wait_for_launcher_recovery } from './launcher_trial.ts';
import { digest_machine_build } from '../../../environments/release/machine_identity.ts';

test.each(['keep', 'revert', 'bad-probe', 'resume-after-crash'] as const)(
  'signed launcher transaction: %s',
  async (result) => {
    const home = await mkdtemp(join(tmpdir(), 'machine-launcher-'));
    const install = mac_installation_paths(home);
    const root = join(install.state, 'updates');
    const runtime = join(root, 'releases/new');
    const next = join(runtime, MAC_RUNTIME_LAUNCHER_APP);
    const id = 'trial';
    const key = createHash('sha256').update(id).digest('hex');
    const receipt = join(root, 'launcher-receipts', key + '.json');
    const staged = join(dirname(install.app), '.' + install.job + '.update.' + key + '.app');
    try {
      for (const dir of [install.app, next, dirname(install.plist)])
        await mkdir(dir, { recursive: true });
      await writeFile(join(install.app, 'binary'), 'old');
      await writeFile(join(next, 'binary'), 'new');
      await writeFile(
        join(root, 'pending.json'),
        JSON.stringify({ request_id: id, runtime_path: runtime }),
      );
      if (result === 'resume-after-crash') {
        const old = await digest_machine_build(install.app),
          target = await digest_machine_build(next);
        await cp(install.app, staged, { recursive: true });
        await writeFile(join(install.app, 'binary'), 'new');
        await writeFile(
          join(root, 'launcher-trial.json'),
          JSON.stringify({ request_id: id, old, next: target }),
        );
        await rm(join(root, 'pending.json'));
      }
      const done = supervise_launcher(id, {
        home,
        runtime,
        unregister: async () => {},
        stage: async (src, dst) => {
          await cp(src, dst, { recursive: true });
          return true;
        },
        probe: async (app) => {
          if (app === install.app && result === 'bad-probe')
            throw new Error('launcher refused the Worker');
        },
      });
      let recovered: Promise<string> | undefined;
      if (result === 'keep' || result === 'revert') {
        await vi.waitFor(async () =>
          expect(JSON.parse(await readFile(receipt, 'utf8')).ready).toBe(true),
        );
        expect(await readFile(join(install.app, 'binary'), 'utf8')).toBe('new');
        if (result === 'keep')
          await writeFile(
            join(root, 'selection.json'),
            JSON.stringify({ request_id: id, runtime_path: runtime }),
          );
        await rm(join(root, 'pending.json'));
        if (result === 'revert')
          recovered = wait_for_launcher_recovery(install.state).then(() =>
            readFile(join(install.app, 'binary'), 'utf8'),
          );
      }
      await done;
      if (recovered) expect(await recovered).toBe('old');
      expect(await readFile(join(install.app, 'binary'), 'utf8')).toBe(
        result === 'keep' ? 'new' : 'old',
      );
      expect(JSON.parse(await readFile(receipt, 'utf8'))).toMatchObject(
        result === 'keep'
          ? { ready: true, signing_requirement_changed: true }
          : { error: expect.any(String) },
      );
    } finally {
      await rm(home, { recursive: true, force: true });
    }
  },
);
