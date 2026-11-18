/** One card probe for every driver: its recipe, executable rule, identity and catalog readers. */
import type {
  BackendIdentity,
  BackendProbeEnvironment,
  CatalogReader,
  CommandOutcome,
} from './contracts.ts';
import type { DriverModelRules } from '../../drivers/core/driver.ts';
import type { DriverManifest } from '../../drivers/core/manifest.ts';
import type { BackendSnapshotDraft, StatusProbeContext } from '../../drivers/core/status.ts';
import { BUILT_IN_MANIFESTS } from '../../drivers/built_in_manifests.ts';
import { parse_version } from './logic/versions.ts';
import { update_advisory } from './advisory.ts';
import { VERSION_PROBE_TIMEOUT_SECONDS, IDENTITY_PROBE_TIMEOUT_SECONDS } from './configuration.ts';
import { cli_version_recorder } from '../backends/cli_compatibility.ts';

/** What one driver gives the card probe. */
export interface CardProbeParts {
  readonly driver_kind: string;
  /** Its executable rule and status recipe. */
  readonly manifest: DriverManifest;
  readonly models?: DriverModelRules;
  /** Reads the answer to the recipe's identity command. Null: no command; the catalog may tell. */
  readonly read_identity: ((outcome: CommandOutcome) => BackendIdentity) | null;
  readonly read_catalog: CatalogReader;
}
/** The executable a card reports: beside its configured file when the manifest says so, else PATH. */
export function card_executable_path(
  executable: DriverManifest['executable'],
  environment: BackendProbeEnvironment,
): string | null {
  return executable.configured_beside === undefined
    ? environment.executable_path(executable.name)
    : environment.configured_executable_path(executable.name);
}
/** The first non-empty numerical version any built-in driver reads from the model id. */
export function built_in_model_version(model_id: string): readonly number[] {
  for (const driver of BUILT_IN_MANIFESTS) {
    const version = driver.models?.version(model_id) ?? [];
    if (version.length) return version;
  }
  return [];
}
export async function probe_card(
  parts: CardProbeParts,
  context: StatusProbeContext,
): Promise<BackendSnapshotDraft> {
  const { environment } = context;
  const recipe = parts.manifest.status_recipe;
  const executable_path = card_executable_path(parts.manifest.executable, environment);
  if (executable_path === null)
    return {
      installed: false,
      executable_path: null,
      version: null,
      identity: null,
      available_models: [],
      reasoning_effort_options: [],
      default_model_id: null,
      default_reasoning_effort: null,
      update_advisory: null,
      diagnoses: [recipe.missing_binary_diagnosis],
      models_complete: false,
    };
  const diagnoses: string[] = [];
  // The same read keeps the feature-floor verdict that later launches use without a spawn.
  const record_cli_version = await cli_version_recorder(parts.driver_kind, executable_path);
  const outcome = await environment.run([executable_path, '--version'], {
    timeout_seconds: VERSION_PROBE_TIMEOUT_SECONDS,
  });
  record_cli_version(outcome.standard_output, outcome.exit_code);
  const version = parse_version(outcome.standard_output + outcome.standard_error);
  if (version === null)
    diagnoses.push(
      `\`${recipe.executable_name} --version\` did not report a version, so Panels cannot tell which one is installed.`,
    );
  let identity: BackendIdentity | null = null;
  if (recipe.identity_arguments !== null && parts.read_identity !== null) {
    const answer = await environment.run([executable_path, ...recipe.identity_arguments], {
      timeout_seconds: IDENTITY_PROBE_TIMEOUT_SECONDS,
    });
    identity = { ...parts.read_identity(answer), login_command: recipe.login_command };
  }
  if (identity?.status === 'unauthenticated')
    diagnoses.push(
      `\`${recipe.executable_name}\` is not signed in.` +
        (recipe.login_command ? ` Run \`${recipe.login_command}\` in a terminal.` : ''),
    );
  if (identity?.status === 'unknown')
    diagnoses.push(
      identity.detail || `Panels could not read who \`${recipe.executable_name}\` is signed in as.`,
    );
  context.signal?.throwIfAborted();
  const catalog = await parts.read_catalog({
    environment,
    executable_path,
    version,
    refresh: context.refresh,
  });
  diagnoses.push(...catalog.diagnoses);
  if (identity === null && catalog.identity !== null)
    identity = { ...catalog.identity, login_command: recipe.login_command };
  const advisory = context.discover_update_advisory
    ? await update_advisory(recipe, executable_path, version, environment)
    : null;
  const models = catalog.models.map((model) => {
    const resolved = model.resolved_model_id || model.model_id;
    return {
      ...model,
      selected_variant: parts.models?.variant(resolved) ?? null,
      version: context.model_version(resolved),
      resolved_model_id: resolved,
    };
  });
  return {
    installed: true,
    executable_path,
    version,
    identity,
    available_models: models,
    reasoning_effort_options: catalog.reasoning_effort_options,
    default_model_id: catalog.default_model_id,
    default_reasoning_effort: catalog.default_reasoning_effort,
    update_advisory: advisory,
    diagnoses,
    models_complete: catalog.complete,
  };
}
