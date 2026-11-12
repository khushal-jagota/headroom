/** Function-only environment commands. No argv parser, installed wrapper or process IO. */
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { getSystemErrorMap } from 'node:util';
import koffi from 'koffi';
import { jsonFailure } from '../core/http/json.ts';
import { JsonNumber, stringify_python_json } from '../core/http/numeric_json.ts';
import { create_database_backup } from './backup/capture.ts';
import { restore_database_snapshot } from './backup/restore.ts';
import { AppManifestValidationFailure } from './command_contracts.ts';
import { is_object, parse_attempt_json } from './updates/codec.ts';
import type {
  CurrentAppCommandDependencies,
  EnvironmentCommandArguments as Args,
  EnvironmentCommandResult as Result,
  HostEnvironmentCommandDependencies,
} from './command_contracts.ts';
import { provision_environment_skills } from './registry/launch.ts';
import { EnvironmentValidationError } from './registry/contracts.ts';
import type { EnvironmentManifest } from './registry/contracts.ts';
import {
  prepare_environment_instance,
  inspect_environment_instance,
  reset_environment_instance,
  remove_environment_instance,
} from './registry/materialize.ts';
import { manifest_to_json } from './registry/store.ts';
import { collect_vps_status } from './vps_status/collect.ts';
import {
  apply_cleanup_inventory,
  collect_cleanup_inventory,
  inventory_body,
} from './vps_status/cleanup.ts';
import {
  layout_for,
  read_ticket_states_from,
  collect_ticket_scratch,
} from './ticket_scratch/collect.ts';
import { remove_ticket_scratch } from './ticket_scratch/apply.ts';
import type { ScratchResult, TicketScratch } from './ticket_scratch/contracts.ts';

const success = (stdout: string, stderr = ''): Result => ({
  exit_code: 0,
  stdout: stdout + '\n',
  stderr,
});
const failed = (error: Error, stderr = ''): Result => ({
  exit_code: 1,
  stdout: '',
  stderr: stderr + `Error: ${error_message(error)}\n`,
});
const json = (value: unknown) =>
  stringify_python_json(value, { sort_keys: true, ensure_ascii: true });
const os_error_codes = new Set([...getSystemErrorMap().values()].map(([name]) => name));
const os_error_numbers = new Map(
  [...getSystemErrorMap()].map(([number, [name]]) => [name, Math.abs(number)]),
);
let native_strerror: ((number: number) => string) | undefined;
function path_repr(path: string): string {
  const quote = path.includes("'") && !path.includes('"') ? '"' : "'";
  let result = quote;
  for (const character of path) {
    const point = character.codePointAt(0)!;
    if (character === quote || character === '\\') result += `\\${character}`;
    else if (character === '\n') result += '\\n';
    else if (character === '\r') result += '\\r';
    else if (character === '\t') result += '\\t';
    else if (character !== ' ' && /[\p{C}\p{Z}]/u.test(character)) {
      const width = point <= 0xff ? 2 : point <= 0xffff ? 4 : 8;
      result += `\\${width === 2 ? 'x' : width === 4 ? 'u' : 'U'}${point.toString(16).padStart(width, '0')}`;
    } else result += character;
  }
  return result + quote;
}
function error_message(error: Error): string {
  if (!('code' in error) || typeof error.code !== 'string') return error.message;
  const known_number = os_error_numbers.get(error.code);
  if (known_number === undefined) return error.message;
  const number =
    'errno' in error && typeof error.errno === 'number' ? Math.abs(error.errno) : known_number;
  native_strerror ??= koffi
    .load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6')
    .func('const char *strerror(int error)');
  let message = `[Errno ${number}] ${native_strerror(number)}`;
  if ('path' in error && typeof error.path === 'string') {
    message += `: ${path_repr(error.path)}`;
    if ('dest' in error && typeof error.dest === 'string')
      message += ` -> ${path_repr(error.dest)}`;
  }
  return message;
}
const os_error = (error: unknown): boolean =>
  error instanceof Error &&
  (('code' in error && typeof error.code === 'string' && os_error_codes.has(error.code)) ||
    error.name === 'OSError' ||
    error.name === 'FileNotFoundError');
// These domain helpers use plain Error for Python RuntimeError/ValueError refusals.
const domain_error = (error: unknown): error is Error =>
  error instanceof Error &&
  (os_error(error) ||
    error.constructor === Error ||
    ['RuntimeError', 'ValueError'].includes(error.name));

export async function environment_status(
  args: Args['status'],
  deps: HostEnvironmentCommandDependencies,
): Promise<Result> {
  const payload = await collect_vps_status(deps.load_config(), deps.status_options);
  // Python round(float, 2) retains its float kind even at an integral percentage.
  const percent = payload.disk.free_percent;
  const document = {
    ...payload,
    disk: {
      ...payload.disk,
      free_percent:
        typeof percent === 'number'
          ? new JsonNumber(String(percent) + (Number.isInteger(percent) ? '.0' : ''))
          : percent,
    },
  };
  return success(args.json ? json(document) : `${payload.overall_state}: ${payload.collected_at}`);
}
export async function environment_cleanup(
  args: Args['cleanup'],
  deps: HostEnvironmentCommandDependencies,
): Promise<Result> {
  const config = deps.load_config();
  const inventory = await collect_cleanup_inventory(config, deps.status_options);
  const payload: Record<string, unknown> = { dry_run: !args.apply, ...inventory_body(inventory) };
  const layout = await layout_for(
    config.ticket_scratch_root,
    config.ticket_scratch_database,
    config.db_path,
  );
  let scratches: TicketScratch[] = [],
    scratch_result: ScratchResult | undefined;
  if (layout !== null) {
    const states = await read_ticket_states_from(config.db_path),
      now = Math.floor(deps.now_unix());
    scratches = await collect_ticket_scratch(layout, states, now);
    payload.ticket_scratch = scratches;
    if (args.apply) {
      payload.result = await apply_cleanup_inventory(inventory);
      scratch_result = await remove_ticket_scratch(layout, scratches, states, now);
      payload.ticket_scratch_result = scratch_result;
    }
  } else if (args.apply) payload.result = await apply_cleanup_inventory(inventory);
  if (args.json) return success(json(payload));
  const lines = [args.apply ? 'applied cleanup' : 'cleanup dry run'];
  for (const candidate of inventory.candidates) lines.push(`${candidate.kind}: ${candidate.path}`);
  for (const scratch of scratches)
    for (const entry of scratch.entries) lines.push(`ticket_scratch: ${entry}`);
  for (const kept of scratch_result?.skipped ?? [])
    lines.push(`kept: ${kept.path} (${kept.reason})`);
  return success(lines.join('\n'));
}
export async function environment_backup(args: Args['backup']): Promise<Result> {
  try {
    return success(
      await create_database_backup(args.source_db, args.backup_dir, args.deployed_revision),
    );
  } catch (error) {
    if (!domain_error(error)) throw error;
    return failed(error);
  }
}
function manifest_json(text: string): unknown {
  try {
    return parse_attempt_json(text);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    const { position, message } = jsonFailure(text, { allow_non_finite: true });
    const prefix = Array.from(text).slice(0, position).join('');
    const line = prefix.split('\n').length,
      column = Array.from(prefix.slice(prefix.lastIndexOf('\n') + 1)).length + 1;
    const detail = text.startsWith('\ufeff')
      ? 'Unexpected UTF-8 BOM (decode using utf-8-sig)'
      : message;
    throw new AppManifestValidationFailure(
      'value',
      `${detail}: line ${line} column ${column} (char ${position})`,
    );
  }
}
export async function environment_backup_current(
  args: Args['backup-current'],
  deps: CurrentAppCommandDependencies,
): Promise<Result> {
  let warning = '';
  try {
    const manifest_path = join(args.current_app, 'manifest.json');
    const value = manifest_json(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
        await readFile(manifest_path),
      ),
    );
    if (!is_object(value)) throw new TypeError('app manifest must be a mapping');
    if (!Object.hasOwn(value, 'app_sha'))
      throw Object.assign(new Error("'app_sha'"), { name: 'KeyError' });
    const revision = (value as Record<string, unknown>).app_sha;
    if (typeof revision !== 'string') throw new TypeError('expected string or bytes-like object');
    if (!/^[0-9a-f]{40}$/.test(revision) || revision.length !== 40)
      throw new AppManifestValidationFailure(
        'value',
        'app SHA must be a 40-character lowercase Git SHA',
      );
    try {
      await deps.validate_app_manifest(manifest_path);
    } catch (error) {
      if (!(
        error instanceof Error &&
        (os_error(error) ||
          (error instanceof AppManifestValidationFailure && error.category === 'value'))
      ))
        throw error;
      warning = `Warning: ${error_message(error)}. Backup will continue.\n`;
    }
    // Backup's domain RuntimeErrors are distinct from unexpected validator errors.
    try {
      return success(
        await create_database_backup(args.source_db, args.backup_dir, revision),
        warning,
      );
    } catch (error) {
      if (!domain_error(error)) throw error;
      return failed(error, warning);
    }
  } catch (error) {
    if (!(
      error instanceof Error &&
      (os_error(error) || error instanceof AppManifestValidationFailure)
    ))
      throw error;
    return failed(error, warning);
  }
}
export async function environment_provision_skills(
  args: Args['provision-skills'],
): Promise<Result> {
  try {
    provision_environment_skills(args);
    return success('provisioned Panels skills for Hermes, Codex, and Claude');
  } catch (error) {
    if (!domain_error(error)) throw error;
    return failed(error);
  }
}
export async function environment_restore(args: Args['restore']): Promise<Result> {
  try {
    await restore_database_snapshot(args.snapshot, args.destination_db, {
      live_stopped: args.live_stopped,
    });
    return success(args.destination_db);
  } catch (error) {
    if (!domain_error(error)) throw error;
    return failed(error);
  }
}
async function registry_result(
  operation: () => Promise<EnvironmentManifest>,
  json_output: boolean,
  running?: boolean,
): Promise<Result> {
  let manifest: EnvironmentManifest;
  try {
    manifest = await operation();
  } catch (error) {
    if (!(error instanceof EnvironmentValidationError)) throw error;
    return failed(error);
  }
  if (!json_output)
    return success(
      `${manifest.kind} ${manifest.instance_id} prepared at ${manifest.instance_root}`,
    );
  try {
    return success(manifest_to_json(manifest, { running }));
  } catch (error) {
    if (!(error instanceof EnvironmentValidationError)) throw error;
    return failed(error);
  }
}
export const environment_prepare = (args: Args['prepare']): Promise<Result> =>
  registry_result(() => prepare_environment_instance(args), args.json);
export const environment_inspect = (args: Args['inspect']): Promise<Result> =>
  registry_result(() => inspect_environment_instance(args), args.json);
export const environment_reset = (args: Args['reset']): Promise<Result> =>
  registry_result(() => reset_environment_instance(args), args.json);
export const environment_remove = (args: Args['remove']): Promise<Result> =>
  registry_result(() => remove_environment_instance(args), args.json, false);
