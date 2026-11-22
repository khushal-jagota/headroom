import assert from 'node:assert/strict';
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { test } from 'vitest';
import type { ServerMoveInstallRequest } from '../server_move/contracts.ts';
import { install_moved_home, remove_moved_home } from './move.ts';
import { create_linux_home_service, remove_linux_home_service } from './linux.ts';
import { check_moved_home_database, initialize_candidate_home_database } from './database.ts';
import type { HomeCommand, HomeMoveDependencies } from './contracts.ts';
import { home_install_fixture } from './testing/home_fixture.ts';

const candidate = fileURLToPath(new URL('../../', import.meta.url));

/** A Linux account with fake systemd: unit state follows the unit files and enable/stop calls. */
async function move_fixture() {
  const f = await home_install_fixture();
  const base = realpathSync(f.base),
    units = join(f.account, '.config/systemd/user');
  const staged = join(base, 'staged/data');
  mkdirSync(join(staged, 'secrets'), { recursive: true });
  writeFileSync(join(staged, 'secrets/update-token'), 'copied token\n');
  writeFileSync(join(staged, 'planner.db'), 'copied database');
  const commands: string[] = [],
    active = new Set<string>();
  const command: HomeCommand = async (argv) => {
    commands.push(argv.join(' '));
    const name = argv.at(argv[2] === 'show' ? 3 : -1)!;
    let stdout = '';
    if (argv.includes('--property=Linger')) stdout = 'yes\n';
    else if (argv.includes('--property=LoadState'))
      stdout = existsSync(join(units, name)) ? 'loaded\n' : 'not-found\n';
    else if (argv.includes('--property=FragmentPath'))
      stdout = existsSync(join(units, name)) ? join(units, name) + '\n' : '\n';
    else if (argv[2] === 'is-active') stdout = active.has(name) ? 'active\n' : 'inactive\n';
    else if (argv[2] === 'enable') active.add(name);
    else if (argv[2] === 'stop') active.delete(name);
    return { code: 0, stdout, stderr: '' };
  };
  const checked: string[] = [];
  const deps: HomeMoveDependencies = {
    ...f.deps,
    command,
    service: (request, host) => create_linux_home_service(request, host, false),
    async check_database(app, path) {
      assert.equal(app, join(root, 'app'));
      checked.push(path);
    },
    remove_service: remove_linux_home_service,
    healthy: async () => true,
  };
  const root = join(base, 'new-parent', 'home');
  const request: ServerMoveInstallRequest = {
    command: 'install',
    root: f.request.root,
    service: 'com.panels.home',
    port: f.request.port,
    archive: f.request.archive,
    archive_sha256: f.request.archive_sha256,
    revision: f.request.revision,
    data_directory: staged,
    trusted_ingress: { provider: 'tailscale', allowed_login: 'owner@example.com' },
    held: {
      move_id: 'move-1',
      target_machine_id: 'machine-b',
      server_url: 'https://panels.example.com',
      server_id: 'server-1',
      move_number: 3,
    },
  };
  return { ...f, base, root, units, staged, commands, active, checked, deps, request };
}

test('a failed moved-home start gives the staged data folder back and removes only the attempt', async () => {
  const f = await move_fixture();
  try {
    const before = readdirSync(f.staged).sort();
    await assert.rejects(
      install_moved_home(f.request, { ...f.deps, healthy: async () => false }),
      /did not report ready/,
    );
    // The caller's folder is back where it was, without the attempt's settings or marker.
    assert.deepEqual(readdirSync(f.staged).sort(), before);
    assert.equal(readFileSync(join(f.staged, 'planner.db'), 'utf8'), 'copied database');
    assert.equal(readFileSync(join(f.staged, 'secrets/update-token'), 'utf8'), 'copied token\n');
    assert(!existsSync(join(f.base, 'new-parent')));
    assert(!existsSync(join(f.account, '.config')));
    assert(!existsSync(join(f.account, '.local/bin/panels')));
    assert(f.commands.includes('systemctl --user stop com.panels.home.service'));
    assert.equal(f.active.size, 0);
  } finally {
    await f.close();
  }
});

test('a moved home has no machine code service, no new database and fresh held settings; remove takes it back', async () => {
  const f = await move_fixture();
  try {
    // The installed app's check runs as a child: it passes a seeded database and refuses another file.
    const seeded = join(f.base, 'seeded.db');
    await initialize_candidate_home_database(candidate, seeded, f.deps.execution, process.execPath);
    await check_moved_home_database(candidate, seeded, f.deps.execution, process.execPath);
    await assert.rejects(
      check_moved_home_database(
        candidate,
        join(f.staged, 'planner.db'),
        f.deps.execution,
        process.execPath,
      ),
    );

    const result = await install_moved_home(f.request, f.deps);
    assert.deepEqual(result, {
      root: f.root,
      data_directory: join(f.root, 'data'),
      executor_link: join(f.root, 'data/executor-link/server.sock'),
    });
    assert(!existsSync(f.staged));
    assert.deepEqual(f.checked, [join(f.root, 'data/planner.db')]);
    assert.deepEqual(readdirSync(f.root).sort(), ['app', 'data', 'logs', 'workspaces']);
    // The copied state is untouched: no database, credential, identity or token was made.
    assert.deepEqual(readdirSync(join(f.root, 'data')).sort(), [
      'config.yaml',
      'move-held.json',
      'planner.db',
      'secrets',
    ]);
    assert.equal(readFileSync(join(f.root, 'data/planner.db'), 'utf8'), 'copied database');
    assert.equal(readFileSync(join(f.root, 'data/secrets/update-token'), 'utf8'), 'copied token\n');
    assert.deepEqual(
      JSON.parse(readFileSync(join(f.root, 'data/move-held.json'), 'utf8')),
      f.request.held,
    );
    const settings = JSON.parse(readFileSync(join(f.root, 'data/config.yaml'), 'utf8'));
    assert.equal(settings.db_path, 'planner.db');
    assert.equal(settings.trusted_ingress_provider, 'tailscale');
    assert.equal(settings.trusted_ingress_allowed_login, 'owner@example.com');
    assert.equal(settings.trusted_ingress_canonical_origin, 'https://panels.example.com');
    assert.deepEqual(readdirSync(f.units).sort(), [
      'com.panels.home.backup.service',
      'com.panels.home.backup.timer',
      'com.panels.home.maintenance.service',
      'com.panels.home.maintenance.timer',
      'com.panels.home.service',
      'com.panels.home.stall-watchdog.service',
      'com.panels.home.stall-watchdog.timer',
    ]);
    assert(!f.commands.some((command) => command.includes('.machine')));
    assert.equal(f.active.size, 4);
    assert(existsSync(join(f.account, '.local/bin/panels')));

    // A first install's root and a server that took over are refused before any effect.
    const first = join(f.base, 'first-home');
    mkdirSync(join(first, 'machine'), { recursive: true });
    await assert.rejects(
      remove_moved_home({ command: 'remove', root: first, service: 'com.panels.home' }, f.deps),
      /not made by a server move/,
    );
    const marker = readFileSync(join(f.root, 'data/move-held.json'));
    writeFileSync(join(f.base, 'marker'), marker);
    rmSync(join(f.root, 'data/move-held.json'));
    await assert.rejects(
      remove_moved_home({ command: 'remove', root: f.root, service: 'com.panels.home' }, f.deps),
      /not held by a move/,
    );
    assert.equal(f.active.size, 4);
    renameSync(join(f.base, 'marker'), join(f.root, 'data/move-held.json'));

    const removal = { command: 'remove', root: f.root, service: 'com.panels.home' };
    await remove_moved_home(removal, f.deps);
    assert(!existsSync(f.root));
    assert.deepEqual(readdirSync(f.units), []);
    assert.equal(f.active.size, 0);
    assert(!existsSync(join(f.account, '.local/bin/panels')));
    await remove_moved_home(removal, f.deps);
  } finally {
    await f.close();
  }
});
