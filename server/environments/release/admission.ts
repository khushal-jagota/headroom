/** Candidate package, worker, and tool admission belongs to the invocation child owner. */
import { access, mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { isAbsolute, join } from 'node:path';
import { constants } from 'node:fs';
import { isDeepStrictEqual } from 'node:util';
import { path_is_within } from '../../core/path_containment.ts';
import { is_record } from '../../core/records.ts';
import { is_file } from '../backup/evidence.ts';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import type { AppManifest, RuntimeContract, NodeRuntimeContract } from './contracts.ts';
import { AppValidationError } from './contracts.ts';
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
  RuntimeProbeUnsettled,
  run_runtime_json_probe,
  run_runtime_probe,
} from './admission_process.ts';
import { NATIVE_NODE_PROBE } from './admission_probe.ts';
import { NATIVE_PYTHON_PROBE } from './python_admission_probe.ts';
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
async function host_node(): Promise<string> {
  const found = await find_release_executable('node', { PATH: HOST_PATH });
  if (found === null) throw new AppValidationError('runtime host prerequisite is missing: node');
  return realpath(found);
}
async function verify_host(
  contract: RuntimeContract,
  state: string,
  deadline: number,
  owner: OwnedChildProcesses,
): Promise<string> {
  if (
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
    } catch {
      throw new AppValidationError(`runtime host library is missing: ${library}`);
    }
    current(deadline);
  }
  for (const name of contract.external_executables) {
    if ((await find_release_executable(name, { PATH: HOST_PATH })) === null)
      throw new AppValidationError(`runtime host prerequisite is missing: ${name}`);
    current(deadline);
  }
  const node = await host_node();
  require_host_node(await run_runtime_probe([node, '--version'], state, deadline, owner), contract);
  return node;
}
export function native_tool_commands(
  root: string,
  contract: RuntimeContract,
  node = 'node',
): string[][] {
  const modules = join(root, 'agent_backends/node_modules');
  const vendor = join(modules, '@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl');
  const site = join(root, '.venv/lib/python3.12/site-packages');
  const runtime =
    contract.version === 2
      ? [join(root, 'server/node_modules/@anthropic-ai/claude-agent-sdk-linux-x64/claude')]
      : [join(site, 'claude_agent_sdk/_bundled/claude'), join(site, 'playwright/driver/node')];
  return [
    [node, join(modules, '@anthropic-ai/claude-code/cli-wrapper.cjs'), '--version'],
    [node, join(modules, '@openai/codex/bin/codex.js'), '--version'],
    ...[
      join(modules, '@anthropic-ai/claude-code-linux-x64/claude'),
      join(modules, '@anthropic-ai/claude-code/bin/claude.exe'),
      ...runtime,
      join(vendor, 'bin/codex'),
      join(vendor, 'codex-resources/bwrap'),
      join(vendor, 'codex-path/rg'),
    ].map((path) => [path, '--version']),
    [join(vendor, 'bin/codex-code-mode-host'), '--help'],
  ];
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
export async function admit_runtime(
  root: string,
  manifest: AppManifest,
  child_processes: OwnedChildProcesses,
): Promise<void> {
  if (manifest.runtime_contract === null)
    throw new AppValidationError('candidate lacks a runtime contract; republish the app');
  const deadline = performance.now() / 1000 + RUNTIME_ADMISSION_TIMEOUT_SECONDS;
  root = await realpath(root);
  // The caller validates artifact bytes. Admission separately rebinds receipt, contract and inventory before code.
  const bound = await validate_app_manifest(join(root, 'manifest.json'), {
    expected_sha: manifest.app_sha,
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
      await mkdir(join(state, name), { mode: 0o700 });
    await writeFile(join(state, 'config.yaml'), '{}\n', { mode: 0o600 });
    const contract = manifest.runtime_contract;
    const node = await verify_host(contract, state, deadline, child_processes);
    current(deadline);
    if (contract.version === 2) {
      const report = await run_runtime_json_probe(
        [
          node,
          '--experimental-import-meta-resolve',
          '--input-type=module',
          '-e',
          NATIVE_NODE_PROBE,
          root,
        ],
        state,
        deadline,
        child_processes,
      );
      await validate_node_probe_report(root, contract, report);
    } else {
      await run_runtime_probe(
        [
          await candidate_tool_path(root, join(root, '.venv/bin/python'), true),
          '-I',
          '-B',
          '-c',
          NATIVE_PYTHON_PROBE,
          root,
          contract.python_version,
          JSON.stringify(contract.external_libraries),
        ],
        state,
        deadline,
        child_processes,
      );
    }
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
