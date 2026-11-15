import assert from 'node:assert/strict';
import { createServer as http_server } from 'node:http';
import { createServer } from 'node:net';
import {
  existsSync,
  linkSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { join, dirname } from 'node:path';
import { tmpdir } from 'node:os';
import { test, vi } from 'vitest';
import type { AppManifest } from '../environments/release/contracts.ts';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { healthy } from '../environments/updates/installer/health.ts';
import { install_home, check_home_port } from './home.ts';
import type { HomeInstallDependencies, HomeInstallRequest, HomeServicePlan } from './contracts.ts';
import { HomeCleanupUnsettled } from './contracts.ts';
import { write_home_job } from '../environments/updates/darwin/jobs.ts';
import { create_darwin_home_io } from '../environments/updates/darwin/service.ts';

async function unused_port(): Promise<number> {
  const server = createServer();
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
  const address = server.address();
  assert(address && typeof address !== 'string');
  await new Promise<void>((done, fail) => server.close((error) => (error ? fail(error) : done())));
  return address.port;
}
async function fixture() {
  const base = mkdtempSync(join(tmpdir(), 'home-install-'));
  const account = join(base, 'account');
  mkdirSync(account);
  const foreign = join(account, 'foreign');
  writeFileSync(foreign, 'existing account data');
  const controller = new AbortController(),
    owner = new ChildProcesses();
  const request: HomeInstallRequest = {
    root: join(base, 'new-parent', 'home'),
    service: 'com.panels.test',
    port: await unused_port(),
    isolated_providers: true,
    archive: join(base, 'archive'),
    archive_sha256: 'a'.repeat(64),
    revision: 'b'.repeat(40),
  };
  const unit = join(account, '.config/systemd/user/com.panels.test.service');
  const events: string[] = [];
  const service: HomeServicePlan = {
    files: [{ path: unit, content: 'owned unit\n', mode: 0o600 }],
    links: [{ path: join(account, 'home.plist'), target: join(request.root, 'agents/home.plist') }],
    async preflight() {},
    async start_home() {
      events.push('start');
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
    async healthy() {
      return false;
    },
    service: async () => service,
    warning: () => {
      throw new Error('Isolated installation must not inspect account skills');
    },
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

test('Mac job conversion refuses pre-existing and late hard links before either file write', async () => {
  const f = await fixture(), path = join(f.account, 'installer.plist');
  const job = { Label: 'com.panels.test', ProgramArguments: ['/unused/node'], WorkingDirectory: f.account, EnvironmentVariables: {} };
  let converted = false;
  const io = create_darwin_home_io(f.deps.execution, f.deps.environment, {
    command: async () => { converted = true; linkSync(path, join(f.account, 'late-link')); return { code: 0, stdout: '<plist/>', stderr: '' }; },
  });
  try {
    linkSync(f.foreign, path);
    await assert.rejects(write_home_job(path, job, io, readFileSync(f.foreign)), /no longer owns/);
    assert.equal(converted, false);
    assert.equal(readFileSync(f.foreign, 'utf8'), 'existing account data');
    rmSync(path);
    await assert.rejects(write_home_job(path, job, io), /replacement changed/);
    assert.equal(converted, true);
    assert.equal(readFileSync(join(f.account, 'late-link'), 'utf8'), JSON.stringify(job) + '\n');
  } finally { await f.close(); }
});

test('failed installation uses a fresh cleanup owner after cancellation and preserves linked foreign data', async () => {
  const f = await fixture();
  try {
    await assert.rejects(
      install_home(f.request, {
        ...f.deps,
        healthy: async () => {
          f.controller.abort(new Error('operator interrupt'));
          f.owner.seal();
          return false;
        },
      }),
      /did not report ready/,
    );
    assert.deepEqual(f.events, ['start', 'stop']);
    assert(!existsSync(f.request.root));
    assert(!existsSync(join(f.base, 'new-parent')));
    assert(!existsSync(f.unit));
    assert.equal(readFileSync(f.foreign, 'utf8'), 'existing account data');
  } finally {
    await f.close();
  }
});

test('replacement root and changed service files retain their trees before any unlink', async () => {
  const f = await fixture();
  try {
    const deps = {
      ...f.deps,
      healthy: async () => {
        renameSync(f.request.root, f.request.root + '-original');
        mkdirSync(f.request.root);
        writeFileSync(join(f.request.root, 'foreign'), 'replacement root');
        return false;
      },
    };
    await assert.rejects(install_home(f.request, deps), /Retained/);
    assert.equal(readFileSync(join(f.request.root, 'foreign'), 'utf8'), 'replacement root');
    assert(existsSync(join(f.request.root + '-original', 'app/sealed')));
    assert(existsSync(f.unit));
  } finally {
    await f.close();
  }
  const changed = await fixture();
  try {
    await assert.rejects(
      install_home(changed.request, {
        ...changed.deps,
        healthy: async () => {
          writeFileSync(changed.unit, 'foreign unit');
          return false;
        },
      }),
      /Retained/,
    );
    assert(existsSync(join(changed.request.root, 'app/sealed')));
    assert.equal(readFileSync(changed.unit, 'utf8'), 'foreign unit');
  } finally {
    await changed.close();
  }
});

test('an expired cleanup observer retains descriptor custody through the actual delayed stop', async () => {
  const f = await fixture(),
    cleanup_abort = new AbortController();
  const original_timeout = AbortSignal.timeout;
  const timeout = vi
    .spyOn(AbortSignal, 'timeout')
    .mockImplementation((milliseconds) =>
      milliseconds === 120000 ? cleanup_abort.signal : original_timeout(milliseconds),
    );
  const admitted = Promise.withResolvers<void>(),
    hold = Promise.withResolvers<void>();
  let checked_after_observer = false;
  const service = {
    ...f.service,
    async stop(_scope: unknown, assert_owned: (path: string) => Promise<void>) {
      admitted.resolve();
      await hold.promise;
      await assert_owned(f.unit);
      checked_after_observer = true;
    },
  };
  let retained: HomeCleanupUnsettled | undefined;
  try {
    const result = install_home(f.request, { ...f.deps, service: async () => service });
    const rejected = assert.rejects(result, (error: unknown) => {
      assert(error instanceof HomeCleanupUnsettled);
      retained = error;
      return true;
    });
    await admitted.promise;
    cleanup_abort.abort(new Error('scripted cleanup deadline'));
    await rejected;
    assert(existsSync(join(f.request.root, 'app/sealed')));
    hold.resolve();
    await assert.rejects(retained!.settled, /scripted cleanup deadline/);
    assert(checked_after_observer);
    assert(existsSync(f.unit));
  } finally {
    hold.resolve();
    timeout.mockRestore();
    await f.close();
  }
});

test('shared health requires exact ready identity for homes while retaining the update default', async () => {
  // The real HTTP boundary proves ready is read from decoded health, not merely a mock return.
  const started = performance.now();
  let body: unknown = { app_sha: 'c'.repeat(40), ready: true };
  const server = http_server((_request, response) => {
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify(body));
  });
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
  try {
    const address = server.address();
    assert(address && typeof address !== 'string');
    const url = `http://127.0.0.1:${address.port}/api/health`,
      sha = 'b'.repeat(40),
      signal = new AbortController().signal;
    await assert.rejects(check_home_port(address.port), /busy/);
    assert.equal(await healthy(url, sha, 0, signal, {}, true), false);
    body = { app_sha: sha, ready: false };
    assert.equal(await healthy(url, sha, 0, signal, {}, true), false);
    assert.equal(await healthy(url, sha, 0, signal), true);
    body = { app_sha: sha, ready: true };
    assert.equal(await healthy(url, sha, 0, signal, {}, true), true);
  } finally {
    await new Promise<void>((done, fail) =>
      server.close((error) => (error ? fail(error) : done())),
    );
    console.log(
      `home-health-boundary seconds=${((performance.now() - started) / 1000).toFixed(3)}`,
    );
  }
});
