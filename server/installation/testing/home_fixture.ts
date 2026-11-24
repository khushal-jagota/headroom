/** The first-install test fixture: a private account, fake services and no real commands. */
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import type { AppManifest } from '../../environments/release/contracts.ts';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import type {
  HomeInstallDependencies,
  HomeInstallSubmission,
  HomeServicePlan,
} from '../contracts.ts';

async function unused_port(): Promise<number> {
  const server = createServer();
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
  const address = server.address();
  assert(address && typeof address !== 'string');
  await new Promise<void>((done, fail) => server.close((error) => (error ? fail(error) : done())));
  return address.port;
}
export async function home_install_fixture() {
  const base = mkdtempSync(join(tmpdir(), 'home-install-'));
  const account = join(base, 'account');
  mkdirSync(account);
  const foreign = join(account, 'foreign');
  writeFileSync(foreign, 'existing account data');
  const controller = new AbortController(),
    owner = new ChildProcesses();
  const request: HomeInstallSubmission = {
    root: join(base, 'new-parent', 'home'),
    service: 'com.panels.test',
    port: await unused_port(),
    isolated_providers: true,
    archive: join(base, 'archive'),
    archive_sha256: 'a'.repeat(64),
    revision: 'b'.repeat(40),
    update_token: 'gho_fixtureUpdateToken0123456789',
  };
  const unit = join(account, '.config/systemd/user/com.panels.test.service');
  const events: string[] = [];
  const service: HomeServicePlan = {
    files: [{ path: unit, content: 'owned unit\n', mode: 0o600 }],
    links: [{ path: join(account, 'home.plist'), target: join(request.root, 'agents/home.plist') }],
    async preflight() {},
    async start_home() {
      assert(events.includes('initialize'));
      events.push('start');
    },
    async start_machine() {
      events.push('machine');
    },
    async start_auxiliaries() {
      events.push('auxiliary');
    },
    async stop(execution, assert_owned) {
      events.push('stop');
      assert.notEqual(execution.child_processes, owner);
      assert(!execution.signal.aborted);
      if (existsSync(unit)) await assert_owned(unit);
    },
  };
  const deps: HomeInstallDependencies = {
    node_executable: process.execPath,
    execution: { child_processes: owner, signal: controller.signal, deadline: null },
    environment: { HOME: account },
    account_home: account,
    system: 'linux',
    architecture: 'x86_64',
    uid: process.getuid!(),
    command: async () => {
      throw new Error('No real commands in the fixture');
    },
    async extract(_archive, destination) {
      // The private admission tree shares the nearest destination ancestor.
      assert.equal(dirname(dirname(destination)), base);
      mkdirSync(destination);
      writeFileSync(join(destination, 'sealed'), 'candidate bytes');
      symlinkSync(foreign, join(destination, 'foreign-link'));
      return { runtime_contract: { version: 2 } } as AppManifest;
    },
    async admit() {
      assert(!existsSync(request.root));
    },
    async initialize_database(app, path) {
      assert.equal(app, join(request.root, 'app'));
      assert.equal(path, join(request.root, 'data/planner.db'));
      assert(existsSync(dirname(path)));
      events.push('initialize');
    },
    async prepare_machine() {},
    async machine_healthy() {},
    async healthy() {
      return false;
    },
    service: async () => service,
  };
  return {
    base,
    account,
    foreign,
    controller,
    owner,
    request,
    unit,
    events,
    service,
    deps,
    close: async () => {
      owner.seal();
      await owner.settled();
      rmSync(base, { recursive: true, force: true });
    },
  };
}
