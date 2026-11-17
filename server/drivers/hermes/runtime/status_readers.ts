/** Hermes' card reader: validate the whole inventory before any of it enters a card. */
import { is_record as object } from '../../../core/records.ts';
import type { BackendModel, CatalogReader } from '../../../conversation/snapshot/contracts.ts';
import type { HermesModelCatalogSource } from '../../../conversation/snapshot/provider_contracts.ts';
import { answer } from '../../../conversation/snapshot/catalogs/readers.ts';
const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null;
export function parse_hermes_inventory(
  payload: unknown,
):
  | readonly [
      readonly BackendModel[],
      string | null,
      'not_configured' | 'default_unavailable' | 'runnable',
    ]
  | null {
  if (!object(payload) || payload.schemaVersion !== 1) return null;
  if (!['status', 'defaultModelId', 'providers'].every((key) => key in payload)) return null;
  const status = payload.status;
  if (status !== 'not_configured' && status !== 'default_unavailable' && status !== 'runnable')
    return null;
  if (payload.defaultModelId !== null && text(payload.defaultModelId) === null) return null;
  const default_model_id = text(payload.defaultModelId);
  if (!Array.isArray(payload.providers)) return null;
  const models: BackendModel[] = [];
  const seen_providers = new Set<string>();
  const seen_models = new Set<string>();
  for (const provider of payload.providers) {
    if (!object(provider) || !['id', 'displayName', 'models'].every((key) => key in provider))
      return null;
    const provider_id = text(provider.id);
    const provider_name = text(provider.displayName);
    if (
      provider_id === null ||
      provider_name === null ||
      seen_providers.has(provider_id) ||
      !Array.isArray(provider.models) ||
      provider.models.length === 0
    )
      return null;
    seen_providers.add(provider_id);
    const prefix = provider_id + ':';
    for (const entry of provider.models) {
      if (!object(entry) || !['id', 'displayName', 'detail'].every((key) => key in entry))
        return null;
      const model_id = text(entry.id);
      const display_name = text(entry.displayName);
      const detail = text(entry.detail);
      if (
        model_id === null ||
        display_name === null ||
        detail === null ||
        seen_models.has(model_id) ||
        !model_id.startsWith(prefix) ||
        model_id === prefix
      )
        return null;
      seen_models.add(model_id);
      models.push({
        model_id,
        display_name,
        detail,
        reasoning_effort_options: null,
        selected_variant: null,
        version: [],
        resolved_model_id: null,
      });
    }
  }
  if (status === 'not_configured') {
    if (default_model_id !== null) return null;
  } else if (status === 'default_unavailable') {
    if (default_model_id === null || seen_models.has(default_model_id)) return null;
  } else if (default_model_id === null || !seen_models.has(default_model_id)) return null;
  return [models, default_model_id, status];
}
export function hermes_catalog_reader(source: HermesModelCatalogSource | null): CatalogReader {
  return async (request) => {
    const outcome = await source?.({ refresh: request.refresh });
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
        diagnoses: ["Hermes' model inventory returned an invalid answer, so no models are listed."],
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
  };
}
