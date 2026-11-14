/** Boot the exact sealed payload through its real foreground launcher, then stop it. */
import { mkdir, writeFile } from 'node:fs/promises';
import { isAbsolute, dirname, join } from 'node:path';
import { createServer } from 'node:net';
import type { ChildProcess } from 'node:child_process';
import { with_publication_entry } from './publication_entry.ts';
import { publication_json_file, publication_text } from './publication_identity.ts';
import { extract_app_archive, remove_owned_tree } from './archive.ts';
import { validate_app_manifest } from './identity.ts';
import { validate_app_sha } from './logic/manifest.ts';
import { require_host_node } from './logic/host.ts';
import { probe_environment } from './admission_process.ts';
import { assert_release_execution } from './process.ts';
import { is_object } from '../updates/codec.ts';
import { replace_existing_json } from '../updates/attempts.ts';
import { healthy } from '../updates/installer/health.ts';
import { UPDATE_HEALTH_TIMEOUT_SECONDS } from '../updates/configuration.ts';
import { MANAGED_STOP_CLIENT_SECONDS } from '../../lifecycle/configuration.ts';
import { observe_promise } from '../../core/promise_observer.ts';

async function loopback_port(): Promise<number> {
  const server = createServer();
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      server.close((error) => {
        if (error) reject(error);
        else if (address && typeof address !== 'string') resolve(address.port);
        else reject(new Error('Boot proof did not obtain a loopback port'));
      });
    });
  });
}

try {
  process.exitCode = await with_publication_entry(async (execution, register_output) => {
    if (process.argv.length !== 3) throw new Error('Expected one sealed boot request file');
    const request = await publication_json_file(process.argv[2]!);
    if (
      !is_object(request) ||
      Object.keys(request).sort().join(',') !==
        'archive_path,archive_sha256,requested_sha,work_root'
    )
      throw new Error('Invalid sealed boot request');
    const archive = publication_text(request.archive_path, 'archive path'),
      work = publication_text(request.work_root, 'boot work root'),
      hash = publication_text(request.archive_sha256, 'archive SHA256'),
      sha = validate_app_sha(publication_text(request.requested_sha, 'requested SHA'));
    if (
      !isAbsolute(archive) ||
      !isAbsolute(work) ||
      !/^[0-9a-f]{64}$/.test(hash) ||
      hash.length !== 64
    )
      throw new Error('Boot requires absolute paths and the sealed archive SHA256');
    if (execution.deadline === null || !Number.isFinite(execution.deadline))
      throw new Error('Boot requires the original finite publication deadline');
    const deadline = execution.deadline,
      began = performance.now(),
      app = join(work, 'app'),
      state = join(work, 'state');
    await mkdir(work, { mode: 0o700 });
    type Outcome = { code: number | null; signal: NodeJS.Signals | null };
    let child: ChildProcess | undefined,
      exited = false,
      stopping = false,
      result: Outcome | undefined,
      closed: Promise<Outcome> | undefined,
      stop_flight: Promise<Outcome | undefined> | undefined;
    const child_stopped = new AbortController();
    const stop = () =>
      (stop_flight ??= (async () => {
        stopping = true;
        if (
          child &&
          !exited &&
          child.exitCode === null &&
          child.signalCode === null &&
          !child.kill('SIGTERM')
        )
          throw new Error('Foreground supervisor TERM delivery was not confirmed');
        if (closed) {
          const observed = await observe_promise(
            closed,
            Math.max(
              0,
              Math.min(deadline - performance.now() / 1000, MANAGED_STOP_CLIENT_SECONDS),
            ) * 1000,
          );
          if (!observed.completed)
            throw new Error('Foreground supervisor close exceeded its stop budget');
          return observed.value;
        }
      })());
    try {
      const manifest = await extract_app_archive(
        archive,
        app,
        hash,
        sha,
        execution.child_processes,
        undefined,
        execution,
      );
      if (manifest.runtime_contract?.version !== 2)
        throw new Error('Boot requires the sealed Node runtime');
      require_host_node(process.version, manifest.runtime_contract);
      for (const name of [
        '',
        'config',
        'cache',
        'codex',
        'claude',
        'hermes',
        'data',
        'logs',
        'backups',
        'workspace',
        'scratch',
        'control',
      ])
        await mkdir(join(state, name), { mode: 0o700 });
      const port = await loopback_port(),
        config_path = join(state, 'config.yaml');
      await writeFile(
        config_path,
        JSON.stringify({
          port,
          test_mode: false,
          dispatch_enabled: false,
          workspace_root: join(state, 'workspace'),
          ticket_scratch_root: join(state, 'scratch'),
          ticket_scratch_database: join(state, 'data/probe.sqlite'),
          credential_key_path: join(state, 'credential.key'),
        }) + '\n',
        { mode: 0o600 },
      );
      const environment = {
        ...probe_environment(state),
        PATH: dirname(process.execPath) + ':/usr/local/bin:/usr/bin:/bin',
        PLAN_CONFIG_PATH: config_path,
        PLAN_PORT: String(port),
        PLAN_APP_ROOT: app,
        PLAN_SERVER_CONTROL_SOCKET: join(state, 'control/server.sock'),
        PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS: '1',
        PLAN_TEST_MODE: '0',
      };
      assert_release_execution(execution);
      child = execution.child_processes.spawn(
        process.execPath,
        [join(app, 'server/environments/release/launcher_main.ts'), 'serve'],
        { cwd: app, env: environment, stdio: 'inherit' },
      );
      closed = new Promise((resolve) => {
        child!.once('exit', () => {
          exited = true;
        });
        child!.once('error', (error) => child_stopped.abort(error));
        child!.once('close', (code, signal) => {
          if (!stopping)
            child_stopped.abort(
              new Error(`Foreground supervisor closed before health: ${code}/${signal}`),
            );
          resolve({ code, signal });
        });
      });
      const remaining = Math.min(
        UPDATE_HEALTH_TIMEOUT_SECONDS,
        deadline - performance.now() / 1000,
      );
      const signal = AbortSignal.any([
        execution.signal,
        child_stopped.signal,
        AbortSignal.timeout(Math.max(1, Math.ceil(remaining * 1000))),
      ]);
      const url = `http://127.0.0.1:${port}/api/health`;
      if (!(await healthy(url, sha, remaining, signal, environment)))
        throw new Error('Sealed application did not become healthy');
      const response = await fetch(`${url}?expected_sha=${sha}`, { signal, redirect: 'error' });
      const health: unknown = await response.json();
      if (
        response.status !== 200 ||
        !is_object(health) ||
        health.ready !== true ||
        health.app_sha !== sha
      )
        throw new Error('Sealed application health differs from the requested SHA');
      result = await stop();
      if (!result || result.code !== 0 || result.signal !== null)
        throw new Error('Foreground supervisor did not complete an ordinary clean stop');
      await validate_app_manifest(join(app, 'manifest.json'), {
        expected_sha: sha,
        require_runtime: true,
      });
      assert_release_execution(execution);
      replace_existing_json(join(work, 'boot-proof.json'), {
        requested_sha: sha,
        archive_sha256: hash,
        manifest,
        health,
        supervisor_exit: result,
        seconds: (performance.now() - began) / 1000,
      });
      register_output(work);
    } catch (error) {
      let failure = error;
      try {
        result = await stop();
      } catch (cleanup) {
        if (cleanup !== failure)
          failure = new AggregateError([failure, cleanup], 'Boot stop failed', { cause: failure });
      }
      if (
        !child ||
        (result && (child.pid === undefined || (result.code === 0 && result.signal === null)))
      ) {
        try {
          await remove_owned_tree(work);
        } catch (cleanup) {
          failure = new AggregateError([failure, cleanup], 'Boot cleanup failed', {
            cause: failure,
          });
        }
      } else console.error(`Boot cleanup is not confirmed; retain owned state at ${work}`);
      throw failure;
    }
  });
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
