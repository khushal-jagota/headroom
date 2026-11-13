/** New-unit producers. The installed bootstrap imports only Node builtins. */
import { readFileSync } from 'node:fs';
import { isAbsolute, join } from 'node:path';
import { environment_file, shell_quote } from '../../conversation/worker_unit/logic.ts';
import { TYPESCRIPT_SERVICE_NAME } from '../../lifecycle/service_identity.ts';
import { CAPABILITY_FILENAME, UNIT_TIMEOUT_STOP_SECONDS } from '../../lifecycle/configuration.ts';
import type { ManagedUnitFiles, WatchdogUnitFiles } from '../../lifecycle/contracts.ts';
import { resolved_path } from '../paths.ts';
import { inside } from '../ticket_scratch/logic.ts';

export type ClassifiedRuntime = 'node-v2' | 'python-v1' | 'python-legacy-null';
export interface ManagedUnitOptions {
  readonly current_root: string;
  readonly node_executable: string;
  readonly config_path: string;
  readonly control_socket_path: string;
  readonly lease_path: string;
  readonly environment_file_path: string;
  readonly runtime_directory_name: string;
  readonly admitted_legacy_null_sha: string | null;
  readonly environment: Readonly<Record<string, string>>;
}
export interface WatchdogUnitOptions {
  readonly current_root: string;
  readonly node_executable: string;
  readonly config_path: string;
  readonly database_path: string;
  readonly state_directory: string;
  readonly environment_file_path: string;
  readonly environment: Readonly<Record<string, string>>;
}

/** One executable source shared with installed-runtime consumers.
 * This is structural routing. The actual launcher retains complete admission.
 * Node22.18 ships JSON reviver source context (V8 HARMONY_SHIPPING_BASE).
 */
export const INSTALLED_RUNTIME_CLASSIFIER_SOURCE = String.raw`
import * as fs from 'node:fs';
import * as path from 'node:path';
function classify_installed_runtime(input, admitted_null_sha) {
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const matches = (pattern, value) => typeof value === 'string' && pattern.exec(value)?.[0] === value;
  const fail = message => { throw new Error(message); };
  if (!path.isAbsolute(input)) fail('application root must be absolute');
  if (fs.lstatSync(input.replace(/\/+$/, '') || '/').isSymbolicLink()) fail('application root must not be a symlink');
  if (admitted_null_sha && !matches(/^[0-9a-f]{40}$/, admitted_null_sha)) fail('invalid admitted legacy SHA');
  const root = fs.realpathSync(input);
  const sources = new WeakMap();
  function regular(name, executable = false) {
    const file = input + path.sep + name;
    const actual = fs.realpathSync(file);
    const suffix = path.relative(root, actual);
    if (suffix === '..' || suffix.startsWith('..' + path.sep) || path.isAbsolute(suffix)) fail('runtime file escapes application');
    if (!fs.statSync(actual).isFile()) fail('runtime file is missing: ' + name);
    if (executable) fs.accessSync(actual, fs.constants.X_OK);
    return file;
  }
  function read_json(name) {
    const file = regular(name);
    if (fs.lstatSync(file).isSymbolicLink()) fail('identity file must be regular: ' + name);
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(fs.readFileSync(file));
    return JSON.parse(text, function(key, value, context) {
      if (typeof value === 'number') {
        let fields = sources.get(this);
        if (!fields) sources.set(this, fields = new Map());
        fields.set(key, context?.source);
      }
      return value;
    });
  }
  function equal(a, b) {
    if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((value, index) => equal(value, b[index]));
    if (object(a) || object(b)) return object(a) && object(b) && equal(Object.keys(a).sort(), Object.keys(b).sort()) && Object.keys(a).every(key => equal(a[key], b[key]));
    return a === b;
  }
  function version(value) {
    if (!object(value) || (value.version !== 1 && value.version !== 2) || sources.get(value)?.get('version') !== String(value.version)) fail('unsupported runtime contract version');
    return value.version;
  }
  const manifest = read_json('manifest.json');
  if (!object(manifest) || manifest.format !== 'panels-app-v1') fail('unsupported application manifest');
  if (!matches(/^[0-9a-f]{40}$/, manifest.app_sha) || !matches(/^[0-9a-f]{64}$/, manifest.source_digest) || !matches(/^[0-9a-f]{64}$/, manifest.artifact_digest)) fail('invalid application identity');
  regular('bin/panels', true);
  regular('bin/panels-launcher', true);
  const contract = manifest.runtime_contract;
  if (contract === null || contract === undefined) {
    if (!admitted_null_sha || manifest.app_sha !== admitted_null_sha) fail('legacy null runtime is not admitted');
    regular('.venv/bin/python', true);
    return 'python-legacy-null';
  }
  const selected = version(contract);
  const payload = read_json('runtime/contract.json');
  if (version(payload) !== selected || !equal(contract, payload)) fail('manifest runtime contract differs from payload');
  if (selected === 1) {
    regular('.venv/bin/python', true);
    return 'python-v1';
  }
  for (const name of ['server/environments/release/launcher_main.ts', 'server/lifecycle/foreground_main.ts', 'server/lifecycle/managed_main.ts', 'server/lifecycle/application.ts', 'server/lifecycle/stop_main.ts', 'server/stalls/watchdog_main.ts', 'server/environments/updates/interruption_capture_main.ts', 'server/environments/updates/restore_main.ts', 'runtime/installer-files.json', 'server/environments/updates/installer/main.ts']) regular(name);
  return 'node-v2';
}
try {
  if (process.argv.length !== 3) throw new Error('expected application root and admitted legacy SHA');
  process.stdout.write(classify_installed_runtime(process.argv[1], process.argv[2]) + '\n');
} catch (error) {
  process.stderr.write('Panels runtime classification failed: ' + (error instanceof Error ? error.message : 'invalid identity') + '\n');
  process.exitCode = 1;
}
`;

function valid_text(value: string): string {
  if (value.includes('\0')) throw new Error('Unit values must not contain NUL');
  return value;
}
function absolute(value: string): string {
  if (!isAbsolute(valid_text(value))) throw new Error('Unit paths must be absolute');
  return value;
}
function unit_value(value: string): string {
  return JSON.stringify(valid_text(value)).replaceAll('%', '%%');
}
function template(name: string, values: Readonly<Record<string, string>>): string {
  const source = readFileSync(
    new URL('../../../ops/panels-environments/' + name + '.in', import.meta.url),
    'utf8',
  );
  return source.replace(/\{\{([A-Z_]+)\}\}/g, (_match, key: string) => {
    if (!Object.hasOwn(values, key)) throw new Error('Unknown unit template field: ' + key);
    return values[key]!;
  });
}
function shell_command(script: string, runtime?: string): string {
  return (
    '/bin/sh -c ' +
    unit_value(script).replaceAll('$', () => '$$') +
    ' panels-unit' +
    (runtime ? ' "%t/' + runtime + '"' : '')
  );
}
function guarded_prefix(): string {
  return (
    'set -euf\nunset NODE_OPTIONS NODE_PATH\n' +
    'for panels_environment_word in $(export -p); do\n' +
    '  panels_environment_name=${panels_environment_word%%=*}\n' +
    '  case "$panels_environment_name" in LD_*)\n' +
    '    case "$panels_environment_name" in *[!A-Za-z0-9_]*) ;; *) unset "$panels_environment_name";; esac;;\n' +
    '  esac\ndone\nunset panels_environment_word panels_environment_name\n'
  );
}
function environment_bundle(
  options: Pick<ManagedUnitOptions, 'environment_file_path' | 'environment' | 'config_path'>,
  addresses: Readonly<Record<string, string>> = {},
): ManagedUnitFiles['environment_file'] {
  const values: Record<string, string> = {
    ...options.environment,
    PLAN_CONFIG_PATH: absolute(options.config_path),
    PLAN_REFUSE_UNIDENTIFIED_AGENT_CALLS: '1',
    ...addresses,
  };
  delete values.RUNTIME_DIRECTORY;
  delete values.MAINPID;
  return { path: absolute(options.environment_file_path), contents: environment_file(values) };
}
async function shared_address(value: string): Promise<string> {
  const path = await resolved_path(absolute(value));
  for (const temporary of ['/tmp', '/var/tmp'])
    if (inside(path, await resolved_path(temporary)))
      throw new Error('Unit control and lease paths must be outside private temporary directories');
  return path;
}
function start_prefix(current: string, node: string, sha: string): string {
  const app = join(absolute(current), 'app');
  return (
    guarded_prefix() +
    'if [ -L ' +
    shell_quote(app) +
    ' ]; then echo "Panels application root must not be a symlink" >&2; exit 1; fi\n' +
    'cd -P -- ' +
    shell_quote(app) +
    ' || exit\n' +
    'runtime=$(' +
    shell_quote(node) +
    ' --input-type=module -e ' +
    shell_quote(INSTALLED_RUNTIME_CLASSIFIER_SOURCE) +
    ' "/proc/$$/cwd/." ' +
    shell_quote(sha) +
    ')\n'
  );
}
const RUNNING_STOP_SOURCE = String.raw`
import * as fs from 'node:fs';
import * as path from 'node:path';
import { pathToFileURL } from 'node:url';
const runtime = process.argv[1], main = process.argv[2];
let record;
try {
  record = JSON.parse(fs.readFileSync(path.join(runtime, ${JSON.stringify(CAPABILITY_FILENAME)}), 'utf8'));
} catch (error) {
  if (error && error.code === 'ENOENT') process.exit(0);
  throw new Error('Could not read the private managed stop capability');
}
const pid = Number(main);
if (/^[1-9][0-9]*$/.exec(main)?.[0] !== main || !Number.isSafeInteger(pid) ||
    !record || record.supervisor_pid !== pid)
  throw new Error('Managed stop capability differs from the running supervisor');
const entry = '/proc/' + pid + '/cwd/server/lifecycle/stop_main.ts';
process.argv = [process.execPath, entry, runtime];
await import(pathToFileURL(entry).href);
`;
export async function render_managed_unit(options: ManagedUnitOptions): Promise<ManagedUnitFiles> {
  const node = absolute(options.node_executable),
    name = options.runtime_directory_name;
  if (/^[A-Za-z0-9_-]+$/.exec(name)?.[0] !== name)
    throw new Error('Runtime directory must be one safe component');
  const sha = options.admitted_legacy_null_sha;
  if (sha !== null && /^[0-9a-f]{40}$/.exec(sha)?.[0] !== sha)
    throw new Error('Invalid admitted legacy SHA');
  const [control, lease] = await Promise.all([
    shared_address(options.control_socket_path),
    shared_address(options.lease_path),
  ]);
  const environment = environment_bundle(options, {
    PLAN_SERVER_CONTROL_SOCKET: control,
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: lease,
  });
  const start =
    start_prefix(options.current_root, node, sha ?? '') +
    'case "$runtime" in\n' +
    'node-v2) exec ' +
    shell_quote(node) +
    ' server/environments/release/launcher_main.ts serve --managed-linux-runtime-directory "$1";;\n' +
    'python-v1|python-legacy-null) exec ./bin/panels-launcher serve;;\n' +
    '*) exit 1;;\nesac\n';
  const stop =
    guarded_prefix() +
    'exec ' +
    shell_quote(node) +
    ' --input-type=module -e ' +
    shell_quote(RUNNING_STOP_SOURCE) +
    ' "$1" "${MAINPID-}"\n';
  return {
    service: template(TYPESCRIPT_SERVICE_NAME, {
      ENVIRONMENT_FILE: unit_value(environment.path),
      RUNTIME_DIRECTORY: name,
      EXEC_START: shell_command(start, name),
      EXEC_STOP: shell_command(stop, name),
      TIMEOUT_STOP: String(UNIT_TIMEOUT_STOP_SECONDS),
    }),
    environment_file: environment,
  };
}
export async function render_watchdog_units(
  options: WatchdogUnitOptions,
): Promise<WatchdogUnitFiles> {
  const node = absolute(options.node_executable),
    environment = environment_bundle(options);
  const script =
    start_prefix(options.current_root, node, '') +
    '[ "$runtime" = node-v2 ] || exit 1\nexec ' +
    shell_quote(node) +
    ' server/stalls/watchdog_main.ts ' +
    shell_quote(absolute(options.database_path)) +
    ' ' +
    shell_quote(absolute(options.state_directory)) +
    '\n';
  const name = TYPESCRIPT_SERVICE_NAME.replace(/\.service$/, '-stall-watchdog.service');
  return {
    service: template(name, {
      ENVIRONMENT_FILE: unit_value(environment.path),
      EXEC_START: shell_command(script),
    }),
    timer: template(name.replace(/\.service$/, '.timer'), { WATCHDOG_SERVICE: name }),
    environment_file: environment,
  };
}
