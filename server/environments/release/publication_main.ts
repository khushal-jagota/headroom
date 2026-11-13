/** Private one-request entry. The host must isolate its environment before Node starts. */
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { writeSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { inspect } from 'node:util';
import type {
  ChildProcess,
  ChildProcessWithoutNullStreams,
  SpawnOptions,
  SpawnOptionsWithoutStdio,
} from 'node:child_process';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import { probe_environment } from './admission_process.ts';
import { build_linux_publication } from './publication.ts';
import { remove_owned_tree } from './archive.ts';
import { validate_app_sha } from './logic/manifest.ts';
import { is_object } from '../updates/codec.ts';
import type {
  LinuxPublicationBuild,
  LinuxPublicationOptions,
  PublicationSourceRun,
} from './publication_contracts.ts';

async function json_file(path: string): Promise<unknown> {
  return JSON.parse(
    new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readFile(path)),
  );
}
function text(value: unknown, name: string): string {
  if (typeof value !== 'string' || !value || value.includes('\0'))
    throw new Error(`Invalid publication ${name}`);
  return value;
}
async function source_run(requested_sha: string): Promise<PublicationSourceRun> {
  const env = process.env,
    get = (name: string) => text(env[name], name);
  if (env.GITHUB_ACTIONS !== 'true')
    throw new Error('Publication build requires actual GitHub run provenance');
  const repository = get('GITHUB_REPOSITORY'),
    sha = validate_app_sha(get('GITHUB_SHA')),
    event = get('GITHUB_EVENT_NAME'),
    ref = get('GITHUB_REF'),
    workflow_ref = get('GITHUB_WORKFLOW_REF');
  const payload = await json_file(get('GITHUB_EVENT_PATH'));
  if (
    !is_object(payload) ||
    !is_object(payload.repository) ||
    payload.repository.full_name !== repository ||
    sha !== requested_sha
  )
    throw new Error('Publication GitHub event does not match its source');
  if (
    (event === 'push' && (payload.after !== sha || payload.ref !== ref)) ||
    (typeof payload.ref === 'string' && payload.ref !== ref)
  )
    throw new Error('Publication GitHub event does not match its ref or SHA');
  if (
    event === 'pull_request' &&
    (!is_object(payload.pull_request) || payload.pull_request.merge_commit_sha !== sha)
  )
    throw new Error('Publication pull request does not match the selected merge SHA');
  const prefix = repository + '/',
    separator = workflow_ref.lastIndexOf('@');
  const workflow_path = workflow_ref.slice(prefix.length, separator);
  if (
    !workflow_ref.startsWith(prefix) ||
    separator < prefix.length ||
    !/^\.github\/workflows\/[^/]+\.ya?ml$/.test(workflow_path) ||
    !ref.startsWith('refs/')
  )
    throw new Error('Invalid publication workflow identity');
  const positive = (name: string) => {
    const value = get(name);
    if (!/^[1-9][0-9]*$/.test(value)) throw new Error(`Invalid publication ${name}`);
    return value;
  };
  return {
    repository,
    source_sha: sha,
    event,
    ref,
    workflow_path,
    workflow_ref,
    run_id: positive('GITHUB_RUN_ID'),
    run_attempt: positive('GITHUB_RUN_ATTEMPT'),
  };
}
async function main(): Promise<0 | 1> {
  const owner = new ChildProcesses(),
    controller = new AbortController(),
    failures = new Set<unknown>();
  let interrupted: 'SIGINT' | 'SIGTERM' | undefined,
    scratch: string | undefined,
    output: string | undefined,
    result: LinuxPublicationBuild | undefined,
    forced = false;
  const force = () => {
    if (forced) return;
    forced = true;
    try {
      owner.force_kill(performance.now() / 1000);
    } catch (error) {
      failures.add(error);
    }
  };
  const stop = (signal: 'SIGINT' | 'SIGTERM') => {
    if (interrupted) return;
    interrupted = signal;
    const reason = new Error(`Publication build interrupted by ${signal}`);
    failures.add(reason);
    owner.seal();
    controller.abort(reason);
    force();
  };
  const interrupt = () => stop('SIGINT'),
    terminate = () => stop('SIGTERM');
  process.on('SIGINT', interrupt);
  process.on('SIGTERM', terminate);
  try {
    if (process.argv.length !== 3) throw new Error('Expected one publication build request file');
    const request = await json_file(process.argv[2]!);
    if (
      !is_object(request) ||
      Object.keys(request).sort().join(',') !==
        'output_directory,public_cli_entrypoint,requested_sha,source_root'
    )
      throw new Error('Invalid publication build request');
    const options: LinuxPublicationOptions = {
      source_root: text(request.source_root, 'source_root'),
      requested_sha: validate_app_sha(text(request.requested_sha, 'requested_sha')),
      output_directory: text(request.output_directory, 'output_directory'),
    };
    for (const path of [options.source_root, options.output_directory])
      if (!isAbsolute(path)) throw new Error('Publication paths must be absolute');
    const dependencies = {
      public_cli_entrypoint: text(request.public_cli_entrypoint, 'public_cli_entrypoint'),
    };
    const provenance = await source_run(options.requested_sha);
    controller.signal.throwIfAborted();
    scratch = await mkdtemp(join(tmpdir(), 'panels-publication-'));
    for (const name of [
      'config',
      'cache',
      'codex',
      'claude',
      'hermes',
      'data',
      'logs',
      'backups',
      'app',
      'workspace',
      'scratch',
    ])
      await mkdir(join(scratch, name), { mode: 0o700 });
    await writeFile(join(scratch, 'config.yaml'), '{}\n', { mode: 0o600 });
    const environment = probe_environment(scratch),
      native_spawn = owner.spawn.bind(owner);
    function spawn(
      command: string,
      args: readonly string[],
      options: SpawnOptionsWithoutStdio,
    ): ChildProcessWithoutNullStreams;
    function spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess;
    function spawn(command: string, args: readonly string[], options: SpawnOptions): ChildProcess {
      return native_spawn(command, args, { ...options, env: { ...environment, ...options.env } });
    }
    owner.spawn = spawn;
    controller.signal.throwIfAborted();
    result = await build_linux_publication(options, dependencies, provenance, {
      child_processes: owner,
      signal: controller.signal,
      deadline: null,
    });
    output = options.output_directory;
  } catch (error) {
    failures.add(error);
  } finally {
    owner.seal();
    if (failures.size) force();
    try {
      await owner.settled();
    } catch (error) {
      failures.add(error);
    }
    if (scratch) {
      try {
        await rm(scratch, { recursive: true, force: true });
      } catch (error) {
        failures.add(error);
      }
    }
    if (output && failures.size) {
      try {
        await remove_owned_tree(output);
      } catch (error) {
        failures.add(error);
      }
    }
    process.off('SIGINT', interrupt);
    process.off('SIGTERM', terminate);
  }
  const errors = [...failures],
    primary = errors[0];
  const error =
    errors.length > 1
      ? new AggregateError(errors, 'Publication build did not close cleanly', { cause: primary })
      : primary;
  if (interrupted) {
    try {
      writeSync(
        2,
        inspect(error, {
          depth: null,
          colors: false,
          maxArrayLength: null,
          maxStringLength: null,
        }) + '\n',
      );
    } catch {
      /* Original signal still owns termination. */
    }
    process.kill(process.pid, interrupted);
    return 1;
  }
  if (errors.length) throw error;
  if (result === undefined) throw new Error('Publication build produced no result');
  return 0;
}
try {
  process.exitCode = await main();
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
