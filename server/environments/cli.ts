/** Function-only environment commands. No argv parser, installed wrapper or process IO. */
import { readFile } from 'node:fs/promises';
import { isAbsolute } from 'node:path';
import { create_database_backup } from './backup/capture.ts';
import { restore_database_snapshot } from './backup/restore.ts';
import { AppManifestValidationFailure } from './command_contracts.ts';
import { domain_error } from './operator_errors.ts';
import { is_object } from './updates/codec.ts';
import type {
  CurrentAppCommandDependencies,
  EnvironmentCommandArguments as Args,
  EnvironmentCommandResult as Result,
  HostEnvironmentCommandDependencies,
  OperatorPathInputs,
} from './command_contracts.ts';
import { provision_environment_skills } from './registry/launch.ts';
import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';
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
import { layout_for } from './ticket_scratch/collect.ts';
import { clean_for_database } from '../ticket_workspaces/cleanup.ts';
import { snapshot_ticket_scratch, prune_ticket_scratch } from '../conversation/worker_scratch.ts';
import { dirname, join } from 'node:path';
import { filesRoot } from '../files/logic/paths.ts';

const success = (stdout: string, stderr = ''): Result => ({
  exit_code: 0,
  stdout: stdout + '\n',
  stderr,
});
const failed = (error: Error, stderr = ''): Result => ({
  exit_code: 1,
  stdout: '',
  failure_message: error.message,
  stderr_prefix: stderr,
});
const json = (value: unknown) => JSON.stringify(value);

export async function environment_status(
  args: Args['status'],
  deps: HostEnvironmentCommandDependencies,
): Promise<Result> {
  return deps.with_resources(async (resources) => {
    const config = deps.load_config();
    config.db_path = deps.admit_path(config.db_path);
    const payload = await collect_vps_status(config, resources.status_options);
    resources.require_active();
    return success(args.json ? json(payload) : `${payload.overall_state}: ${payload.collected_at}`);
  });
}
export async function environment_cleanup(
  args: Args['cleanup'],
  deps: HostEnvironmentCommandDependencies,
): Promise<Result> {
  return deps.with_resources(async (resources) => {
    const config = deps.load_config();
    config.db_path = deps.admit_path(config.db_path);
    const inventory = await collect_cleanup_inventory(config, resources.status_options);
    resources.require_active();
    const payload: Record<string, unknown> = { dry_run: !args.apply, ...inventory_body(inventory) };
    const selected_layout = await layout_for(
      config.ticket_scratch_root,
      config.ticket_scratch_database,
      config.db_path,
      deps.home,
      deps.admit_path,
    );
    const layout =
      selected_layout === null
        ? null
        : {
            repository: deps.admit_path(selected_layout.repository),
            folders: selected_layout.folders.map((path) => deps.admit_path(path)),
          };
    resources.require_active();
    const scratch_result = await clean_for_database(config.db_path, Math.floor(deps.now_unix()), {
      ...resources.scratch_options,
      extra_roots: layout?.folders ?? [],
      apply: args.apply,
      measure_size: true,
      snapshot_ticket_scratch: (ticket) => snapshot_ticket_scratch(ticket, deps.home),
      prune_ticket_scratch: (ticket, names) => prune_ticket_scratch(ticket, names, deps.home),
    });
    const manifest_path = deps.admit_path(
      join(dirname(filesRoot(config.db_path, 'ticket')), 'ticket-workspaces-cleanup.json'),
    );
    payload.ticket_workspaces = JSON.parse(await readFile(manifest_path, 'utf8'));
    payload.ticket_workspaces_result = scratch_result;
    if (args.apply)
      payload.result = await apply_cleanup_inventory(inventory, resources.signal, deps.admit_path);
    resources.require_active();
    if (args.json) return success(json(payload));
    const lines = [args.apply ? 'applied cleanup' : 'cleanup dry run'];
    for (const candidate of inventory.candidates)
      lines.push(`${candidate.kind}: ${candidate.path}`);
    lines.push(`Ticket workspace inventory: ${manifest_path}`);
    for (const kept of scratch_result?.skipped ?? [])
      lines.push(`kept: ${kept.path} (${kept.reason})`);
    return success(lines.join('\n'));
  });
}
export async function environment_backup(
  args: Args['backup'],
  deps: OperatorPathInputs,
): Promise<Result> {
  try {
    const source = deps.admit_path(deps.expand_path(args.source_db)),
      backup = deps.admit_path(deps.expand_path(args.backup_dir));
    return success(await create_database_backup(source, backup, args.deployed_revision));
  } catch (error) {
    if (!domain_error(error)) throw error;
    return failed(error);
  }
}
function manifest_json(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    throw new AppManifestValidationFailure('value', error.message);
  }
}
export async function environment_backup_current(
  args: Args['backup-current'],
  deps: CurrentAppCommandDependencies,
): Promise<Result> {
  let warning = '';
  try {
    const manifest_input = `${args.current_app || '.'}/manifest.json`;
    let manifest_path: string;
    let source: Buffer;
    try {
      const literal = isAbsolute(manifest_input)
        ? manifest_input
        : process.cwd() + '/' + manifest_input;
      manifest_path = deps.admit_path(literal);
      source = await readFile(manifest_path);
    } catch (error) {
      // Checked paths own I/O. Python reports the original Click path on read failure.
      if (error instanceof Error && 'path' in error) Object.assign(error, { path: manifest_input });
      throw error;
    }
    const value = manifest_json(
      new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(source),
    );
    if (!is_object(value)) throw new TypeError('app manifest must be a mapping');
    if (!Object.hasOwn(value, 'app_sha')) throw new TypeError('app manifest is missing app_sha');
    const revision = (value as Record<string, unknown>).app_sha;
    if (typeof revision !== 'string') throw new TypeError('app manifest app_sha must be a string');
    if (!/^[0-9a-f]{40}$/.test(revision) || revision.length !== 40)
      throw new AppManifestValidationFailure(
        'value',
        'app SHA must be a 40-character lowercase Git SHA',
      );
    try {
      await deps.validate_app_manifest(manifest_path);
    } catch (error) {
      if (!domain_error(error, { runtime: false, plain: false })) throw error;
      warning = `Warning: ${error.message}. Backup will continue.\n`;
      if (deps.write_warning) {
        const output = warning;
        warning = '';
        await deps.write_warning(output);
      }
    }
    // Backup failures do not become manifest warnings.
    try {
      const source = deps.admit_path(deps.expand_path(args.source_db)),
        backup = deps.admit_path(deps.expand_path(args.backup_dir));
      return success(await create_database_backup(source, backup, revision), warning);
    } catch (error) {
      if (!domain_error(error)) throw error;
      return failed(error, warning);
    }
  } catch (error) {
    if (!domain_error(error, { plain: false })) throw error;
    return failed(error, warning);
  }
}
/** Each driver with a home, by its label: 'A, B, and C'. */
const PROVISIONED_DRIVER_LABELS = new Intl.ListFormat('en', { type: 'conjunction' }).format(
  BUILT_IN_MANIFESTS.filter(({ manifest }) => manifest.home).map(({ client }) => client.label),
);
export async function environment_provision_skills(
  args: Args['provision-skills'],
  deps: OperatorPathInputs,
): Promise<Result> {
  try {
    const warning = provision_environment_skills(args, deps);
    return warning
      ? { exit_code: 0, stdout: '', stderr: warning + '\n' }
      : success(`provisioned Panels skills for ${PROVISIONED_DRIVER_LABELS}`);
  } catch (error) {
    if (!domain_error(error)) throw error;
    return failed(error);
  }
}
export async function environment_restore(
  args: Args['restore'],
  deps: OperatorPathInputs,
): Promise<Result> {
  try {
    const snapshot = args.live_stopped
      ? deps.admit_path(deps.expand_path(args.snapshot))
      : args.snapshot;
    const destination = args.live_stopped
      ? deps.admit_path(deps.expand_path(args.destination_db))
      : args.destination_db;
    await restore_database_snapshot(snapshot, destination, {
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
export const environment_prepare = (
  args: Args['prepare'],
  deps: OperatorPathInputs,
): Promise<Result> =>
  registry_result(() => prepare_environment_instance(args, undefined, deps.admit_path), args.json);
export const environment_inspect = (args: Args['inspect']): Promise<Result> =>
  registry_result(() => inspect_environment_instance(args), args.json);
export const environment_reset = (args: Args['reset'], deps: OperatorPathInputs): Promise<Result> =>
  registry_result(() => reset_environment_instance(args, undefined, deps.admit_path), args.json);
export const environment_remove = (
  args: Args['remove'],
  deps: OperatorPathInputs,
): Promise<Result> =>
  registry_result(
    () => remove_environment_instance(args, undefined, deps.admit_path),
    args.json,
    false,
  );
