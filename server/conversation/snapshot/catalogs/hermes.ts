/** Validate the whole inventory before any of it enters a card. */
import type { BackendModel } from '../contracts.ts';
const text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null;
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
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
