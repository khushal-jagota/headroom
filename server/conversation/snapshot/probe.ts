/** One machine probe uses explicit command and catalog adapters for all backends. */
import type { ConversationBackendKey } from '../contracts.ts';
import type { BackendProbe, BackendIdentity, CatalogReader } from './contracts.ts';
import { RECIPES } from './recipes.ts';
import { parse_version } from './logic/versions.ts';
import { numerical_version, recognize_variant } from '../model_resolution/logic/variants.ts';
import { update_advisory } from './advisory.ts';
import { read_claude_identity, read_codex_identity } from './identity.ts';
import { VERSION_PROBE_TIMEOUT_SECONDS, IDENTITY_PROBE_TIMEOUT_SECONDS } from './configuration.ts';
export function create_backend_probe(
  readers: Readonly<Record<ConversationBackendKey, CatalogReader>>,
): BackendProbe {
  return async (backend_key, environment, options) => {
    const recipe = RECIPES[backend_key];
    const executable_path =
      backend_key === 'hermes'
        ? environment.configured_executable_path(recipe.executable_name)
        : environment.executable_path(recipe.executable_name);
    if (executable_path === null)
      return {
        backend_key,
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
    const outcome = await environment.run([executable_path, '--version'], {
      timeout_seconds: VERSION_PROBE_TIMEOUT_SECONDS,
    });
    const version = parse_version(outcome.standard_output + outcome.standard_error);
    if (version === null)
      diagnoses.push(
        `\`${recipe.executable_name} --version\` did not report a version, so Panels cannot tell which one is installed.`,
      );
    let identity: BackendIdentity | null = null;
    if (recipe.identity_arguments !== null) {
      const answer = await environment.run([executable_path, ...recipe.identity_arguments], {
        timeout_seconds: IDENTITY_PROBE_TIMEOUT_SECONDS,
      });
      identity =
        backend_key === 'claude'
          ? read_claude_identity(answer)
          : read_codex_identity(answer, options.codex_login, options.now);
      identity = { ...identity, login_command: recipe.login_command };
    }
    if (identity?.status === 'unauthenticated')
      diagnoses.push(
        `\`${recipe.executable_name}\` is not signed in.` +
          (recipe.login_command ? ` Run \`${recipe.login_command}\` in a terminal.` : ''),
      );
    if (identity?.status === 'unknown')
      diagnoses.push(
        identity.detail ||
          `Panels could not read who \`${recipe.executable_name}\` is signed in as.`,
      );
    options.signal?.throwIfAborted();
    const catalog = await readers[backend_key]({
      environment,
      executable_path,
      version,
      refresh: options.refresh,
    });
    diagnoses.push(...catalog.diagnoses);
    const advisory = options.discover_update_advisory
      ? await update_advisory(recipe, executable_path, version, environment)
      : null;
    const models = catalog.models.map((model) => {
      const resolved = model.resolved_model_id || model.model_id;
      return {
        ...model,
        selected_variant: recognize_variant(backend_key, resolved)?.name ?? null,
        version: numerical_version(resolved),
        resolved_model_id: resolved,
      };
    });
    return {
      backend_key,
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
  };
}
