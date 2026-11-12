/** Adapt provider-owned local catalogs into the one Panels card contract. */
import type { CatalogAnswer, CatalogReader, BackendModel } from '../contracts.ts';
import type { ConversationBackendKey } from '../../contracts.ts';
import type {
  CodexModelCatalogSource,
  ClaudeModelCatalogSource,
  HermesModelCatalogSource,
} from '../provider_contracts.ts';
import {
  CodexModelCatalogUnavailable,
  ClaudeModelCatalogUnavailable,
} from '../provider_contracts.ts';
import { parse_hermes_inventory } from './hermes.ts';
const CLAUDE_FALLBACK_REASONING_EFFORT_OPTIONS = ['low', 'medium', 'high', 'xhigh', 'max'];
function answer(overrides: Partial<CatalogAnswer> = {}): CatalogAnswer {
  return {
    models: [],
    reasoning_effort_options: [],
    default_model_id: null,
    default_reasoning_effort: null,
    diagnoses: [],
    complete: false,
    ...overrides,
  };
}
function model(overrides: Pick<BackendModel, 'model_id'> & Partial<BackendModel>): BackendModel {
  return {
    display_name: null,
    detail: null,
    reasoning_effort_options: null,
    selected_variant: null,
    version: [],
    resolved_model_id: null,
    ...overrides,
  };
}
export function create_catalog_readers(sources: {
  readonly codex: CodexModelCatalogSource | null;
  readonly claude: ClaudeModelCatalogSource | null;
  readonly hermes: HermesModelCatalogSource | null;
}): Readonly<Record<ConversationBackendKey, CatalogReader>> {
  return {
    async codex(request) {
      try {
        if (sources.codex === null) throw new CodexModelCatalogUnavailable();
        const catalog = await sources.codex(request.executable_path);
        return answer({
          complete: catalog.complete,
          models: catalog.models.map((value) =>
            model({
              model_id: value.model_id,
              display_name: value.display_name,
              reasoning_effort_options: value.reasoning_effort_options,
            }),
          ),
          reasoning_effort_options: catalog.reasoning_effort_options,
          default_model_id: catalog.default_model_id,
          default_reasoning_effort: catalog.default_reasoning_effort,
        });
      } catch (error) {
        if (!(error instanceof CodexModelCatalogUnavailable)) throw error;
        return answer({
          diagnoses: [
            'Codex is installed but did not answer when asked what it can run, so no models are listed. Check that `codex app-server` starts from a terminal.',
          ],
        });
      }
    },
    async claude(request) {
      try {
        if (sources.claude === null) throw new ClaudeModelCatalogUnavailable();
        const catalog = await sources.claude(request.executable_path);
        return answer({
          complete: true,
          models: catalog.models.map((value) =>
            model({
              model_id: value.resolved_model_id,
              display_name: value.display_name,
              detail: `${value.model_id} → ${value.resolved_model_id}`,
              resolved_model_id: value.resolved_model_id,
              reasoning_effort_options: value.reasoning_effort_options,
            }),
          ),
          reasoning_effort_options: catalog.reasoning_effort_options.length
            ? catalog.reasoning_effort_options
            : CLAUDE_FALLBACK_REASONING_EFFORT_OPTIONS,
          default_model_id: catalog.default_model_id,
        });
      } catch (error) {
        if (!(error instanceof ClaudeModelCatalogUnavailable)) throw error;
        return answer({
          reasoning_effort_options: CLAUDE_FALLBACK_REASONING_EFFORT_OPTIONS,
          diagnoses: [
            'Claude is installed but did not answer when asked what it can run, so no models are listed. Check that `claude` starts from a terminal.',
          ],
        });
      }
    },
    async hermes(request) {
      const outcome = await sources.hermes?.({ refresh: request.refresh });
      if (outcome === undefined || outcome.exit_code !== 0)
        return answer({
          diagnoses: [
            "Hermes' model catalog could not be read from its install, so no models are listed. Check that hermes runs from a terminal.",
          ],
        });
      let payload: unknown;
      try {
        payload = JSON.parse(outcome.standard_output);
      } catch {
        payload = null;
      }
      const inventory = parse_hermes_inventory(payload);
      if (inventory === null)
        return answer({
          diagnoses: [
            "Hermes' model inventory returned an invalid answer, so no models are listed.",
          ],
        });
      const [models, configured_default, status] = inventory;
      const diagnoses =
        status === 'not_configured'
          ? [
              'Hermes is installed but has no default provider and model configured. Run `hermes model` in a terminal.',
            ]
          : status === 'default_unavailable'
            ? [
                "Hermes' configured default does not have usable credentials or is not in its configured model inventory. Run `hermes model` in a terminal.",
              ]
            : [];
      return answer({
        models,
        default_model_id: status === 'runnable' ? configured_default : null,
        diagnoses,
      });
    },
  };
}
