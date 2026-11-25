/** Candidate package, worker, and tool admission belongs to the invocation child owner. */
import { access, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { constants } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { path_is_within } from '../../core/path_containment.ts';
import { is_record } from '../../core/records.ts';
import { BUILT_IN_MANIFESTS } from '../../drivers/built_in_manifests.ts';
import { is_file } from '../backup/evidence.ts';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import type {
  AppManifest,
  RuntimeContract,
  NodeRuntimeContract,
  RuntimeTarget,
} from './contracts.ts';
import { AppValidationError, MAC_HOME } from './contracts.ts';
import { validate_app_manifest } from './identity.ts';
import {
  os_release_values,
  require_host_node,
  require_minimum_node,
  verify_runtime_host_facts,
} from './logic/host.ts';
import { RUNTIME_ADMISSION_TIMEOUT_SECONDS } from './process_configuration.ts';
import { find_release_executable } from './process.ts';
import {
  PROBE_HOME_DIRECTORIES,
  RuntimeProbeUnsettled,
  run_runtime_json_probe,
  run_runtime_probe,
} from './admission_process.ts';
import { NATIVE_NODE_PROBE } from './admission_probe.ts';
import { resolved_path } from '../paths.ts';

const HOST_PATH = '/usr/local/bin:/usr/bin:/bin';
function current(deadline: number): void {
  if (performance.now() / 1000 >= deadline)
    throw new AppValidationError('runtime admission exceeded its time budget');
}
export async function candidate_tool_path(
  root: string,
  path: string,
  executable: boolean,
): Promise<string> {
  const actual = await realpath(path);
  if (!path_is_within(root, actual) || !(await is_file(actual)))
    throw new AppValidationError('runtime tool resolves outside the candidate or is not a file');
  if (executable) await access(actual, constants.X_OK);
  return actual;
}
async function verify_host(
  contract: RuntimeContract,
  state: string,
  deadline: number,
  owner: OwnedChildProcesses,
): Promise<void> {
  if (contract.system === 'darwin') {
    if (process.platform !== 'darwin' || process.arch !== 'arm64')
      throw new AppValidationError('runtime requires macOS arm64');
    const { verify_darwin_host_facts } = await import('./logic/darwin_host.ts');
    const version = await run_runtime_probe(
      ['/usr/bin/sw_vers', '-productVersion'],
      state,
      deadline,
      owner,
    );
    verify_darwin_host_facts(contract, process.platform, process.arch, version);
    return;
  }
  if (
    contract.system !== 'linux' ||
    process.platform !== contract.system ||
    (process.arch === 'x64' ? 'x86_64' : process.arch) !== contract.architecture
  )
    throw new AppValidationError('runtime requires Linux x86_64');
  const text = async (path: string) =>
    new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await readFile(path));
  let release: string, cpuinfo: string;
  try {
    try {
      release = await text('/etc/os-release');
    } catch (error) {
      if (!(error instanceof Error && 'code' in error)) throw error;
      release = await text('/usr/lib/os-release');
    }
    cpuinfo = await text('/proc/cpuinfo');
  } catch (error) {
    if (!(error instanceof Error)) throw error;
    throw new AppValidationError(`unsupported runtime host: ${error.message}`);
  }
  const distribution = os_release_values(release);
  const report = process.report.getReport() as unknown as {
    header: { glibcVersionRuntime?: string };
  };
  verify_runtime_host_facts(contract, {
    system: process.platform,
    architecture: process.arch === 'x64' ? 'x86_64' : process.arch,
    distribution: distribution.ID ?? '',
    version: distribution.VERSION_ID ?? '',
    glibc: report.header.glibcVersionRuntime ?? null,
    feature_sets: cpuinfo
      .split('\n')
      .filter((line) => line.startsWith('flags'))
      .map((line) =>
        line
          .slice(line.indexOf(':') + 1)
          .trim()
          .split(/\s+/),
      ),
  });
  current(deadline);
  const { default: koffi } = await import('koffi');
  for (const library of contract.external_libraries) {
    try {
      koffi.load(library);
    } catch (error) {
      throw new AppValidationError(`runtime host library is missing: ${library}`, { cause: error });
    }
    current(deadline);
  }
  for (const name of contract.external_executables) {
    if (name === 'node') {
      // Linux machine code keeps its supervisor's baseline interpreter, even without global Node.
      await access(await realpath(process.execPath), constants.X_OK);
    } else if ((await find_release_executable(name, { PATH: HOST_PATH })) === null)
      throw new AppValidationError(`runtime host prerequisite is missing: ${name}`);
    current(deadline);
  }
}
/** Each driver's admission commands for this system, drivers in manifest order. */
export function native_tool_commands(
  root: string,
  contract: RuntimeContract,
  node = 'node',
): string[][] {
  return BUILT_IN_MANIFESTS.flatMap(({ manifest }) => manifest.install?.admission_commands ?? [])
    .filter(
      (command) =>
        command.systems.includes(contract.system) &&
        (contract.system !== 'linux' ||
          command.runtime_contracts === undefined ||
          command.runtime_contracts.includes(contract.version)),
    )
    .map((command) =>
      command.runner === 'node'
        ? [node, join(root, command.path), ...command.arguments]
        : [join(root, command.path), ...command.arguments],
    );
}
export async function validate_node_probe_report(
  root: string,
  contract: NodeRuntimeContract,
  report: unknown,
): Promise<void> {
  if (
    !is_record(report) ||
    Object.keys(report).sort().join(',') !==
      ['node_version', 'native', 'loaded_libraries', 'package_paths'].sort().join(',')
  )
    throw new AppValidationError('runtime probe report has missing or unknown fields');
  if (typeof report.node_version !== 'string' || report.native !== 'passed')
    throw new AppValidationError('runtime probe did not pass');
  require_minimum_node(report.node_version, contract);
  const libraries = report.loaded_libraries;
  if (
    !Array.isArray(libraries) ||
    !libraries.every((path) => typeof path === 'string' && path.startsWith('/')) ||
    !isDeepStrictEqual(
      libraries,
      [...libraries].sort((a, b) => Buffer.compare(Buffer.from(a), Buffer.from(b))),
    )
  )
    throw new AppValidationError('runtime probe reported malformed loaded libraries');
  for (const path of libraries)
    if (path.endsWith('.node') && !path_is_within(root, await resolved_path(path)))
      throw new AppValidationError('runtime probe loaded a native addon outside the candidate');
  const declared: unknown = JSON.parse(
    new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
      await readFile(join(root, 'server/package.json')),
    ),
  );
  if (
    !is_record(declared) ||
    !is_record(declared.dependencies) ||
    !is_record(report.package_paths) ||
    !isDeepStrictEqual(
      Object.keys(report.package_paths).sort(),
      Object.keys(declared.dependencies).sort(),
    )
  )
    throw new AppValidationError("runtime probe packages differ from the candidate's own");
  for (const value of Object.values(report.package_paths))
    if (typeof value !== 'string' || !path_is_within(root, await resolved_path(value)))
      throw new AppValidationError("runtime probe packages differ from the candidate's own");
}
/**
 * Install-time admission. The release must match its own receipt and contract, and this host
 * must meet the requirements that the release declares. Candidate code does not run here: the
 * installer's health check after the swap, with rollback, proves that the release runs.
 */
export function admit_runtime(
  root: string,
  manifest: AppManifest,
  child_processes: OwnedChildProcesses,
  expected_target?: RuntimeTarget,
): Promise<void> {
  return admit(root, manifest, child_processes, expected_target, false);
}
/**
 * Build-time proof for a release this same code built. It also runs the candidate's packages,
 * usage worker and driver tools, so it never runs against another version's release.
 */
export function prove_built_runtime(
  root: string,
  manifest: AppManifest,
  child_processes: OwnedChildProcesses,
): Promise<void> {
  return admit(root, manifest, child_processes, undefined, true);
}
async function admit(
  root: string,
  manifest: AppManifest,
  child_processes: OwnedChildProcesses,
  expected_target: RuntimeTarget | undefined,
  prove: boolean,
): Promise<void> {
  if (manifest.runtime_contract === null)
    throw new AppValidationError('candidate lacks a runtime contract; republish the app');
  const deadline = performance.now() / 1000 + RUNTIME_ADMISSION_TIMEOUT_SECONDS;
  root = await realpath(root);
  // The caller validates artifact bytes. Admission separately rebinds receipt, contract and inventory before code.
  const bound = await validate_app_manifest(join(root, 'manifest.json'), {
    expected_sha: manifest.app_sha,
    expected_target:
      expected_target ?? (manifest.runtime_contract.system === 'darwin' ? MAC_HOME : undefined),
    require_runtime: true,
    verify_artifact_digest: false,
  });
  if (!isDeepStrictEqual(bound, manifest))
    throw new AppValidationError('candidate manifest differs from the admitted receipt');
  current(deadline);
  const state = await mkdtemp(join(tmpdir(), 'panels-runtime-probe-'));
  let deferred_cleanup = false;
  try {
    for (const name of [
      'config',
      'cache',
      ...PROBE_HOME_DIRECTORIES,
      'data',
      'logs',
      'backups',
      'app',
      'workspace',
      'scratch',
    ])
      await mkdir(join(state, name), { mode: 0o700 });
    await writeFile(join(state, 'config.yaml'), '{}\n', { mode: 0o600 });
    const contract = manifest.runtime_contract;
    await verify_host(contract, state, deadline, child_processes);
    const host_node = expected_target?.system === 'linux' && expected_target.role === 'executor';
    const node = host_node
      ? await realpath(process.execPath)
      : await candidate_tool_path(root, join(root, 'node/bin/node'), true);
    if (host_node)
      require_host_node(
        await run_runtime_probe([node, '--version'], state, deadline, child_processes),
        contract,
      );
    current(deadline);
    if (!prove) return;
    require_host_node(
      await run_runtime_probe([node, '--version'], state, deadline, child_processes),
      contract,
    );
    const report = await run_runtime_json_probe(
      [
        node,
        '--experimental-import-meta-resolve',
        '--experimental-vm-modules',
        '--input-type=module',
        '-e',
        NATIVE_NODE_PROBE,
        root,
        contract.system,
      ],
      state,
      deadline,
      child_processes,
    );
    await validate_node_probe_report(root, contract, report);
    current(deadline);
    for (const command of native_tool_commands(root, contract, node)) {
      for (const [index, value] of command.entries()) {
        if (index === 0 && value === node) continue;
        if (isAbsolute(value)) command[index] = await candidate_tool_path(root, value, index === 0);
      }
      current(deadline);
      await run_runtime_probe(command, state, deadline, child_processes);
      current(deadline);
    }
  } catch (error) {
    if (error instanceof RuntimeProbeUnsettled) {
      deferred_cleanup = true;
      const settled = error.settled.finally(() => rm(state, { recursive: true, force: true }));
      void settled.catch((failure) => {
        console.error(`Runtime probe deferred cleanup failed for ${state}: ${String(failure)}`);
      });
      throw new RuntimeProbeUnsettled(settled, error.cause);
    }
    throw error;
  } finally {
    if (!deferred_cleanup) await rm(state, { recursive: true, force: true });
  }
}
