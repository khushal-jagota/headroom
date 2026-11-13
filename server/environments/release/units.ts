/** Installed runtime selection uses only Node builtins and never runs candidate code. */
export type ClassifiedRuntime = 'node-v2' | 'python-v1' | 'python-legacy-null';

/** One executable source, embedded unchanged into BOTH unit actions.
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
  if (admitted_null_sha && !matches(/^[0-9a-f]{40}$/, admitted_null_sha)) fail('invalid admitted legacy SHA');
  const root = fs.realpathSync(input);
  const sources = new WeakMap();
  function regular(name, executable = false) {
    const file = path.join(root, name);
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
  for (const name of ['server/environments/release/launcher_main.ts', 'server/lifecycle/foreground_main.ts', 'server/lifecycle/managed_main.ts', 'server/lifecycle/application.ts', 'server/lifecycle/stop_main.ts']) regular(name);
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
