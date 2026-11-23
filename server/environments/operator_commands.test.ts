/** Four command boundaries, private files and owned Node witnesses; no provider or executor. */
import {
  mkdir,
  mkdtemp,
  readFile,
  rename,
  rm,
  writeFile,
  copyFile,
  utimes,
  stat,
} from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { expect, test } from 'vitest';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { assert_temporary_path } from '../core/test_paths.ts';
import { run_release_command } from './release/process.ts';
import { build_exported_app } from './release/build.ts';
import { validate_app_manifest, digest_app_artifact } from './release/identity.ts';
import { write_app_entrypoints } from './release/entrypoints.ts';
import { createDatabaseFromSeed, createReadAccess } from '../core/database/index.ts';
import type { ApplicationSchemaSeed } from '../core/database/contracts.ts';
import { operator_commands } from './commands.ts';
import { create_production_operator_dependencies } from './operator_dependencies.ts';
import { prepare_environment_instance } from './registry/materialize.ts';

async function fixture(action: (f: Awaited<ReturnType<typeof private_fixture>>) => Promise<void>) {
  const f = await private_fixture();
  try {
    await action(f);
  } finally {
    try {
      for (const stop of f.stops) await stop();
    } finally {
      try {
        f.owner.force_kill(performance.now() / 1000 + 5);
      } finally {
        try {
          await f.owner.settled();
        } finally {
          await rm(f.root, { recursive: true, force: true });
        }
      }
    }
  }
}
async function private_fixture() {
  const root = assert_temporary_path(await mkdtemp(join(tmpdir(), 'pcli-')));
  const owner = new ChildProcesses();
  const stops: Array<() => Promise<void>> = [];
  const environment: NodeJS.ProcessEnv = {
    PATH: process.env.PATH,
    TMPDIR: tmpdir(),
    HOME: join(root, 'home'),
    CODEX_HOME: join(root, 'home/codex'),
    CLAUDE_CONFIG_DIR: join(root, 'home/claude'),
    PLAN_TEST_MODE: '1',
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    PLAN_CONFIG_PATH: join(root, 'config.yaml'),
    PLAN_DB_PATH: join(root, 'db.sqlite'),
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'control.sock'),
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(root, 'lease.lock'),
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_CREDENTIAL_KEY_PATH: join(root, 'credential.key'),
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_PORT: '0',
  };
  await mkdir(environment.HOME!, { mode: 0o700 });
  await writeFile(environment.PLAN_CONFIG_PATH!, '{}\n');
  const execution = {
    child_processes: owner,
    signal: new AbortController().signal,
    deadline: null,
  };
  const command = (argv: string[], cwd = root) =>
    run_release_command(argv, { timeout_seconds: 10, environment, cwd }, execution);
  return { root, owner, environment, execution, command, stops };
}
const module_url = (path: string) => new URL(path, import.meta.url).href;
async function wait_for<T>(read: () => Promise<T>): Promise<T> {
  const deadline = performance.now() + 5_000;
  while (true) {
    try {
      return await read();
    } catch (error) {
      if (performance.now() >= deadline) throw error;
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
}

// The witness binds before it publishes this address. No ambient address is accepted.
async function owned_health(
  root: string,
  supervisor_pid: number,
  previous_pid?: number,
): Promise<{ pid: number; port: number }> {
  const path = assert_temporary_path(join(root, 'ready.json'));
  return wait_for(async () => {
    const ready = JSON.parse(await readFile(path, 'utf8'));
    if (
      ready.root !== root ||
      ready.parent_pid !== supervisor_pid ||
      !Number.isInteger(ready.pid) ||
      ready.pid === previous_pid
    )
      throw new Error('No new owned fixture application');
    const address = new URL(`http://127.0.0.1:${ready.port}`);
    if (
      address.hostname !== '127.0.0.1' ||
      !Number.isInteger(ready.port) ||
      ready.port <= 0 ||
      ready.port > 65535
    )
      throw new Error('Refused live or unowned listener before connection');
    const response = await fetch(address, { signal: AbortSignal.timeout(1_000) });
    expect(await response.json()).toEqual({ pid: ready.pid, root });
    return ready;
  });
}
async function supervisor(f: Awaited<ReturnType<typeof private_fixture>>) {
  const app = join(f.root, 'application.mjs');
  await writeFile(
    app,
    `import{createServer}from'node:http';import{writeFileSync}from'node:fs';
const root=${JSON.stringify(f.root)};
const server=createServer((q,r)=>r.end(JSON.stringify({pid:process.pid,root})));
server.listen(0,'127.0.0.1',()=>writeFileSync(root+'/ready.json',JSON.stringify({pid:process.pid,parent_pid:process.ppid,root,port:server.address().port})));
process.on('SIGTERM',()=>server.close(()=>process.exit(0)));`,
  );
  const script = join(f.root, 'supervisor.mjs');
  await writeFile(
    script,
    `import{operator_commands}from${JSON.stringify(module_url('./commands.ts'))};
import{run_server_supervisor}from${JSON.stringify(module_url('../lifecycle/supervisor.ts'))};
const result=await operator_commands.serve.handler({}, {run_supervisor:()=>run_server_supervisor(${JSON.stringify(
      {
        mode: { kind: 'foreground' },
        application: {
          node_executable: process.execPath,
          entrypoint: app,
          launch_root: f.root,
          environment: f.environment,
        },
        control_socket_path: f.environment.PLAN_SERVER_CONTROL_SOCKET,
        lease_path: f.environment.PLAN_SERVER_LIFECYCLE_LEASE_PATH,
        port: 0,
      },
    )})});process.exitCode=result.exit_code;`,
  );
  const child = f.owner.spawn(process.execPath, [script], { env: f.environment, stdio: 'ignore' });
  const exited = new Promise<number | null>((resolve, reject) => {
    child.once('exit', resolve);
    child.once('error', reject);
  });
  f.stops.push(async () => {
    if (child.exitCode !== null || child.signalCode !== null) return;
    child.kill('SIGTERM');
    await wait_for(async () => {
      if (child.exitCode === null && child.signalCode === null)
        throw new Error('Owned supervisor did not stop');
    });
    await exited;
  });
  return { child, exited };
}

test('app-build emits a manifest for a real exact-SHA source export', async () => {
  await fixture(async (f) => {
    const source = join(f.root, 'source'),
      candidate = join(f.root, 'candidate');
    await mkdir(source);
    await writeFile(join(source, 'cli'), '#!/bin/sh\nexit 0\n', { mode: 0o755 });
    await f.command(['git', 'init', '--template=', source]);
    await f.command(['git', '-C', source, 'add', 'cli']);
    await f.command([
      'git',
      '-C',
      source,
      '-c',
      'user.email=fixture@example.invalid',
      '-c',
      'user.name=Fixture',
      'commit',
      '-m',
      'private',
    ]);
    const sha = (await f.command(['git', '-C', source, 'rev-parse', 'HEAD'])).stdout.trim();
    const result = await operator_commands['app-build'].handler(
      { source_root: source, requested_sha: sha, candidate_app: candidate },
      {
        build_app: (args) =>
          build_exported_app(
            args.source_root,
            { ...args, install_dependencies: false },
            { public_cli_entrypoint: 'cli' },
            f.execution,
          ),
      },
    );
    expect(result.exit_code).toBe(0);
    expect(JSON.parse(result.stdout)).toEqual(
      await validate_app_manifest(join(candidate, 'manifest.json')),
    );
    expect(JSON.parse(result.stdout).app_sha).toBe(sha);
    expect(await readFile(join(candidate, 'bin/panels'), 'utf8')).toContain('exec "$root"/cli');
  });
}, 20_000);

test('serve starts and cleanly stops its actual private supervised application', async () => {
  await fixture(async (f) => {
    const running = await supervisor(f);
    await owned_health(f.root, running.child.pid!);
    running.child.kill('SIGTERM');
    expect(await running.exited).toBe(0);
  });
}, 15_000);

test('restart replaces the application through the real private control socket', async () => {
  await fixture(async (f) => {
    const running = await supervisor(f);
    const first = await owned_health(f.root, running.child.pid!);
    const deps = await create_production_operator_dependencies('restart', undefined, f.environment);
    const result = await operator_commands.restart.handler({}, deps);
    expect(result).toEqual({ exit_code: 0, stdout: 'Panels restart accepted.\n', stderr: '' });
    const second = await owned_health(f.root, running.child.pid!, first.pid);
    expect(second.pid).not.toBe(first.pid);
    running.child.kill('SIGTERM');
    expect(await running.exited).toBe(0);
  });
}, 15_000);

test('run replaces its process with the prepared repository and retains its reserved listener', async () => {
  await fixture(async (f) => {
    const source = join(f.root, 'source');
    await mkdir(join(source, 'server/cli'), { recursive: true });
    await f.command(['git', 'init', '--template=', source]);
    await writeFile(join(source, 'package.json'), '{"type":"module"}');
    await writeFile(
      join(source, 'server/cli/main.ts'),
      `import{createServer}from'node:net';import{writeFileSync}from'node:fs';
const marker=${JSON.stringify(join(f.root, 'run-marker.json'))};writeFileSync(marker,'entered');
const server=createServer();server.listen({fd:Number(process.env.PLAN_SERVER_LISTENER_FD)},()=>{
const result=JSON.stringify({pid:process.pid,cwd:process.cwd(),db:process.env.PLAN_DB_PATH,test_mode:process.env.PLAN_TEST_MODE,port:server.address().port,argv:process.argv.slice(2)});writeFileSync(marker,result);console.log(result);server.close();});`,
    );
    const args = {
      kind: 'staging' as const,
      environment_root: join(f.root, 'environments'),
      repository_roots: [source],
      test_mode: true,
    };
    const manifest = await prepare_environment_instance(
      args,
      {
        async build_fixture(path) {
          await mkdir(join(path, '..'), { recursive: true });
          await writeFile(path, 'private fixture');
        },
        rename,
        remove: rm,
      },
      assert_temporary_path,
    );
    const result = await f.command([
      process.execPath,
      fileURLToPath(module_url('../cli/main.ts')),
      'environment',
      'run',
      '--kind',
      'staging',
      '--environment-root',
      args.environment_root,
      '--repository-root',
      source,
      '--test-mode',
    ]);
    const marker = await readFile(join(f.root, 'run-marker.json'), 'utf8').catch(
      () => 'entry did not run',
    );
    expect(result.stdout, result.stderr + '\nMarker: ' + marker).not.toBe('');
    const witness = JSON.parse(result.stdout);
    expect(witness).toMatchObject({
      cwd: source,
      db: manifest.db_path,
      test_mode: '1',
      argv: ['serve'],
    });
    expect(result.stderr).toContain(`staging http://127.0.0.1:${witness.port}`);
    expect(witness.port).toBeGreaterThan(0);
  });
}, 15_000);

// Copy the real launcher entry and relay its imports to this checkout. The small
// sealed tree exercises the generated shell wrappers without copying dependencies.
test('installed launcher runs nightly backup and applied maintenance on private data', async () => {
  await fixture(async (f) => {
    const app = join(f.root, 'app');
    const file = async (name: string, text: string, mode = 0o644) => {
      const path = join(app, name);
      await mkdir(join(path, '..'), { recursive: true });
      await writeFile(path, text, { mode });
    };
    await mkdir(join(app, 'node/bin'), { recursive: true });
    await copyFile(process.execPath, join(app, 'node/bin/node'));
    await file('package.json', '{"type":"module"}');
    await file(
      'server/environments/release/entrypoints.ts',
      `export { launch_app } from ${JSON.stringify(module_url('./release/entrypoints.ts'))};`,
    );
    await file(
      'server/core/environment.ts',
      `export { copy_environment } from ${JSON.stringify(module_url('../core/environment.ts'))};`,
    );
    await file(
      'server/cli/main.ts',
      `#!/usr/bin/env node\nimport { main } from ${JSON.stringify(module_url('../cli/main.ts'))}; await main();`,
      0o755,
    );
    await copyFile(
      fileURLToPath(module_url('./release/launcher_main.ts')),
      join(app, 'server/environments/release/launcher_main.ts'),
    );
    const revision = 'a'.repeat(40);
    await write_app_entrypoints(
      app,
      revision,
      { public_cli_entrypoint: 'server/cli/main.ts' },
      f.execution,
    );
    await file(
      'manifest.json',
      JSON.stringify({
        format: 'panels-app-v1',
        app_sha: revision,
        source_digest: 'b'.repeat(64),
        artifact_digest: await digest_app_artifact(app),
      }),
    );
    const seed = JSON.parse(
      await readFile(new URL('../core/database/application_schema.json', import.meta.url), 'utf8'),
    ) as ApplicationSchemaSeed;
    const db = createDatabaseFromSeed(f.environment.PLAN_DB_PATH!, seed, () => {});
    db.close();
    await mkdir(join(f.root, 'skills'));
    await writeFile(join(f.root, 'skills/proof.txt'), 'managed skill');
    const launcher = join(app, 'bin/panels-launcher');
    const backup = await f.command([
      launcher,
      'environment',
      'backup-current',
      '--source-db',
      f.environment.PLAN_DB_PATH!,
      '--backup-dir',
      f.environment.PLAN_BACKUP_DIR!,
      '--current-app',
      app,
    ]);
    expect(backup.code).toBe(0);
    const snapshot = backup.stdout.trim();
    expect(JSON.parse(await readFile(join(snapshot, 'metadata.json'), 'utf8'))).toMatchObject({
      verified: true,
      deployed_revision: revision,
    });
    expect(await readFile(join(snapshot, 'files/skills/proof.txt'), 'utf8')).toBe('managed skill');
    const snapshot_db = createReadAccess(join(snapshot, 'database.sqlite'));
    try {
      expect(snapshot_db.read((sql) => sql.get('SELECT version_num FROM alembic_version'))).toEqual(
        { version_num: seed.alembicRevision },
      );
    } finally {
      snapshot_db.close();
    }
    // The handler retains its warning-and-backup policy when installed files drift.
    await file('changed.txt', 'drift');
    const changed = await f.command([
      launcher,
      'environment',
      'backup-current',
      '--source-db',
      f.environment.PLAN_DB_PATH!,
      '--backup-dir',
      f.environment.PLAN_BACKUP_DIR!,
      '--current-app',
      app,
    ]);
    expect(changed.code).toBe(0);
    expect(changed.stderr).toContain('Warning: app artifact digest does not match its manifest');
    const expired = join(f.environment.PLAN_BACKUP_DIR!, '.backup-expired');
    await mkdir(expired);
    await writeFile(join(expired, 'partial'), 'expired backup');
    await utimes(expired, 0, 0);
    const cleanup = await f.command([launcher, 'environment', 'cleanup', '--apply']);
    expect(cleanup.code).toBe(0);
    expect(cleanup.stdout).toContain('applied cleanup');
    await expect(stat(expired)).rejects.toMatchObject({ code: 'ENOENT' });
    expect((await stat(snapshot)).isDirectory()).toBe(true);
  });
}, 20_000);
