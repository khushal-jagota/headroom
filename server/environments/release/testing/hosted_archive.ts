/** Hosted-only archive operations on the driver's private real payload. */
import { lstat, readdir, realpath, stat, statfs, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { SpawnOptions } from 'node:child_process';
import { ChildProcesses } from '../../../lifecycle/owned_processes.ts';
import { create_app_archive, extract_app_archive, hash_archive } from '../archive.ts';
import { validate_app_manifest } from '../identity.ts';
import { AppValidationError } from '../contracts.ts';
import {
  RUNTIME_ADMISSION_TIMEOUT_SECONDS,
  RUNTIME_PROBE_TIMEOUT_SECONDS,
} from '../process_configuration.ts';
import { admit_runtime, native_tool_commands } from '../admission.ts';
import { RuntimeProbeUnsettled } from '../admission_process.ts';
import { NATIVE_PYTHON_PROBE } from '../python_admission_probe.ts';
import { find_release_executable } from '../process.ts';
import { inside } from '../../ticket_scratch/logic.ts';
import { create_migration_status } from '../../updates/migration_status.ts';
import { PYTHON_SCHEMA_STATUS_PROBE } from '../../updates/migration_probe.ts';
import * as configuration from '../configuration.ts';

interface SchemaObservation {
  database: string;
  expected: boolean | string;
}
type HostedRequest = {
  app: string;
  sha: string;
  report: string;
} & (
  | { operation: 'pack' | 'unpack'; archive: string; destination: string; checksum: string }
  | { operation: 'admit_runtime' }
  | { operation: 'schema'; cases: readonly SchemaObservation[] }
);
export async function hosted_archive(request: HostedRequest): Promise<void> {
  if (process.env.GITHUB_ACTIONS !== 'true') throw new Error('Hosted archive proof only');
  const owner = new ChildProcesses();
  let stopping = false;
  let observation_failure: unknown;
  const terminate = () => {
    stopping = true;
    process.exitCode = 1;
    owner.seal();
    try {
      owner.force_kill(performance.now() / 1000 + 5);
    } catch (error) {
      observation_failure ??= error;
    }
  };
  process.on('SIGTERM', terminate);
  process.on('SIGINT', terminate);
  const evidence: Record<string, unknown> = {
    operation: request.operation,
    limits: Object.fromEntries(
      Object.entries(configuration).filter(([name]) => name.startsWith('ARCHIVE_')),
    ),
  };
  let spawned = 0,
    closed = 0,
    spool_path: string | undefined;
  let space_calls = 0,
    minimum_available: bigint | undefined;
  let schema_case: SchemaObservation | undefined,
    candidate_root: string | undefined,
    candidate_python: string | undefined,
    candidate_python_version: string | undefined;
  let probe_state: string | undefined;
  let probe_argv: readonly string[] | undefined;
  const admission_commands: string[][] = [],
    admission_observed: string[][] = [];
  let admission_state: string | undefined;
  const spawn = owner.spawn.bind(owner);
  owner.spawn = ((command: string, args: readonly string[], options: SpawnOptions) => {
    if (stopping) throw new Error('Hosted proof stopped before native admission');
    if (request.operation === 'admit_runtime') {
      const expected = admission_commands[spawned];
      if (
        !expected ||
        command !== expected[0] ||
        args.length !== expected.length - 1 ||
        args.some((value, index) => value !== expected[index + 1])
      )
        throw new Error(`Hosted proof refused an unexpected admission command at ${spawned}`);
      const state = options.env?.HOME;
      if (
        typeof state !== 'string' ||
        options.cwd !== state ||
        options.env?.TMPDIR !== state ||
        (admission_state !== undefined && state !== admission_state)
      )
        throw new Error('Runtime admission lost its shared private state');
      const suffix = relative(dirname(request.report), state);
      if (!suffix || suffix === '..' || suffix.startsWith('..' + sep) || isAbsolute(suffix))
        throw new Error('Runtime admission state escaped the hosted private root');
      admission_state = state;
    } else if (request.operation === 'schema') {
      if (!schema_case || !candidate_root || !candidate_python || !candidate_python_version)
        throw new Error('Schema observation was not published before spawn');
      const expected = [
        '-I',
        '-B',
        '-c',
        PYTHON_SCHEMA_STATUS_PROBE,
        candidate_root,
        candidate_python_version,
        schema_case.database,
      ];
      if (
        command !== candidate_python ||
        args.length !== expected.length ||
        args.some((value, index) => value !== expected[index])
      )
        throw new Error('Hosted proof refused an unexpected candidate command');
      const state = options.env?.HOME;
      if (typeof state !== 'string' || options.cwd !== state || options.env?.TMPDIR !== state)
        throw new Error('Schema probe lost its private state');
      const suffix = relative(dirname(request.report), state);
      if (!suffix || suffix === '..' || suffix.startsWith('..' + sep) || isAbsolute(suffix))
        throw new Error('Schema probe state escaped the hosted private root');
      probe_state = state;
      probe_argv = [command, ...args];
    } else {
      if (
        command !== process.execPath ||
        args[0] !== fileURLToPath(new URL('../archive_unpack_main.ts', import.meta.url)) ||
        !Array.isArray(options.stdio) ||
        typeof options.stdio[3] !== 'number'
      )
        throw new Error('Hosted proof refused an unexpected child');
      const specification = JSON.parse(args[1]!) as { spool_root: string };
      spool_path = join(specification.spool_root, 'expanded.tar');
    }
    const child = spawn(command, args, options);
    if (request.operation === 'admit_runtime') admission_observed.push([command, ...args]);
    spawned++;
    child.once('close', () => closed++);
    return child;
  }) as typeof owner.spawn;
  try {
    if (request.operation === 'admit_runtime') {
      const root = await realpath(request.app);
      const manifest = await validate_app_manifest(join(root, 'manifest.json'), {
        expected_sha: request.sha,
        require_runtime: true,
        verify_artifact_digest: false,
      });
      if (manifest.runtime_contract?.version !== 1)
        throw new Error('Hosted runtime admission requires a genuine v1 candidate');
      // Match production's finite HOST_PATH search, independent of the launcher's PATH.
      const found = await find_release_executable('node', {
        PATH: '/usr/local/bin:/usr/bin:/bin',
      });
      if (found === null) throw new Error('Hosted runtime admission has no trusted host Node');
      const node = await realpath(found),
        contract = manifest.runtime_contract,
        python = await realpath(join(root, '.venv/bin/python'));
      if (!inside(python, root))
        throw new Error('Hosted admission Python resolves outside the candidate');
      admission_commands.push(
        [node, '--version'],
        [
          python,
          '-I',
          '-B',
          '-c',
          NATIVE_PYTHON_PROBE,
          root,
          contract.python_version,
          JSON.stringify(contract.external_libraries),
        ],
      );
      for (const command of native_tool_commands(root, contract, node)) {
        for (const [index, value] of command.entries()) {
          if (index === 0 && value === node) continue;
          if (isAbsolute(value)) {
            const actual = await realpath(value);
            if (!inside(actual, root))
              throw new Error('Hosted admission tool resolves outside the candidate');
            command[index] = actual;
          }
        }
        admission_commands.push(command);
      }
      const entries = (await readdir(dirname(request.report))).sort(),
        started = performance.now();
      try {
        await admit_runtime(root, manifest, owner);
      } catch (error) {
        if (error instanceof RuntimeProbeUnsettled) {
          owner.seal();
          const failures: unknown[] = [error];
          try {
            owner.force_kill(performance.now() / 1000 + 5);
          } catch (force) {
            failures.push(force);
          }
          try {
            await error.settled;
          } catch (cleanup) {
            failures.push(cleanup);
          }
          if (failures.length > 1)
            throw new AggregateError(failures, 'Hosted runtime admission cleanup failed');
        }
        throw error;
      }
      const seconds = (performance.now() - started) / 1000,
        after = (await readdir(dirname(request.report))).sort();
      if (
        stopping ||
        observation_failure !== undefined ||
        spawned !== admission_commands.length ||
        closed !== spawned ||
        !admission_state ||
        existsSync(admission_state) ||
        JSON.stringify(entries) !== JSON.stringify(after)
      )
        throw new Error(
          'Runtime admission lost its ordered commands, actual close or state cleanup',
        );
      Object.assign(evidence, {
        candidate: root,
        host_node: node,
        commands: admission_observed,
        spawned,
        closed,
        seconds,
        admission_timeout_seconds: RUNTIME_ADMISSION_TIMEOUT_SECONDS,
        parent_entries_before: entries,
        parent_entries_after: after,
        state_removed: true,
        boundary:
          'Successful genuine v1 runtime admission. No cancelled-probe cleanup or server-start claim.',
      });
    } else if (request.operation === 'schema') {
      candidate_root = await realpath(request.app);
      const manifest = await validate_app_manifest(join(candidate_root, 'manifest.json'), {
        expected_sha: request.sha,
        require_runtime: true,
        verify_artifact_digest: false,
      });
      if (manifest.runtime_contract?.version !== 1 || request.cases.length !== 4)
        throw new Error(
          'Hosted schema proof requires a genuine v1 candidate and four observations',
        );
      candidate_python = await realpath(join(candidate_root, '.venv/bin/python'));
      if (!inside(candidate_python, candidate_root))
        throw new Error('Hosted schema Python resolves outside the candidate');
      candidate_python_version = manifest.runtime_contract.python_version;
      const migration = create_migration_status(owner),
        rows = [];
      for (const item of request.cases) {
        if (stopping) throw new Error('Hosted schema proof stopped');
        schema_case = item;
        probe_state = undefined;
        probe_argv = undefined;
        const before = await hash_archive(item.database);
        const entries = (await readdir(dirname(request.report))).sort();
        const count = spawned,
          started = performance.now();
        let outcome: boolean | string;
        try {
          outcome = await migration.schema_change_pending(item.database, candidate_root);
        } catch (error) {
          if (
            !(error instanceof AppValidationError) ||
            typeof item.expected !== 'string' ||
            error.message !== item.expected
          )
            throw error;
          outcome = error.message;
        }
        const seconds = (performance.now() - started) / 1000;
        const after = await hash_archive(item.database);
        if (
          outcome !== item.expected ||
          before !== after ||
          spawned !== count + 1 ||
          closed !== spawned ||
          !probe_state ||
          existsSync(probe_state) ||
          JSON.stringify(entries) !==
            JSON.stringify((await readdir(dirname(request.report))).sort())
        )
          throw new Error(
            'Candidate schema observation lost its result, readonly bytes, actual close or private-state cleanup',
          );
        rows.push({
          database: item.database,
          outcome,
          hash_before: before,
          hash_after: after,
          argv: probe_argv,
          spawned: 1,
          closed: 1,
          state_removed: true,
          seconds,
          probe_timeout_seconds: RUNTIME_PROBE_TIMEOUT_SECONDS,
        });
      }
      Object.assign(evidence, {
        candidate: candidate_root,
        python_version: candidate_python_version,
        rows,
        spawned,
        closed,
        boundary:
          'Genuine built candidate wrapper with its unchanged origin assertions. No full staging ABI or runtime admission claim.',
      });
    } else if (request.operation === 'pack')
      evidence.checksum = await create_app_archive(request.app, request.archive);
    else {
      const manifest = await extract_app_archive(
        request.archive,
        request.destination,
        request.checksum,
        request.sha,
        owner,
        async (path) => {
          const info = await statfs(path, { bigint: true });
          const available = info.bavail * info.bsize;
          space_calls++;
          minimum_available =
            minimum_available === undefined || available < minimum_available
              ? available
              : minimum_available;
          if (spool_path) {
            // The final capacity call follows validation and sees the complete spool.
            const spool = await stat(spool_path);
            Object.assign(evidence, {
              expanded_bytes: spool.size,
              spool_allocated_bytes: spool.blocks * 512,
            });
          }
          return available;
        },
      );
      if (stopping || spawned !== closed)
        throw new Error('Unpack did not settle cleanly', { cause: observation_failure });
      if (((await lstat(request.destination)).mode & 0o777) !== 0o700)
        throw new Error('Unpack output root is not private');
      Object.assign(evidence, {
        manifest,
        spawned,
        closed,
        capacity: { calls: space_calls, minimum_available: String(minimum_available) },
      });
    }
    await writeFile(request.report, JSON.stringify(evidence) + '\n', { flag: 'wx' });
  } finally {
    owner.seal();
    try {
      try {
        if (spawned !== closed) owner.force_kill(performance.now() / 1000 + 5);
      } finally {
        await owner.settled();
      }
    } finally {
      process.off('SIGTERM', terminate);
      process.off('SIGINT', terminate);
    }
  }
}
