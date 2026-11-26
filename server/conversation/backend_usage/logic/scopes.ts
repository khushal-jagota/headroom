/** A card shows a model-scoped allowance on the catalog models the provider's scope names. */
import type { BackendSnapshot } from '../../snapshot/contracts.ts';
import type { BackendUsageWindow } from '../contracts.ts';
/**
 * The models a window's scope names: the models it names exactly, else every model of that
 * family, so a `Fable` window covers Fable 5, 5.1 and 5.2. Empty for an account-wide window.
 */
export function usage_window_model_ids(
  window: BackendUsageWindow,
  models: BackendSnapshot['available_models'],
): string[] {
  if (window.model_scope === null) return [];
  const wanted = window.model_scope.toLowerCase().trim();
  const exact = models.filter((model) =>
    [model.model_id.toLowerCase(), (model.display_name ?? '').toLowerCase()].includes(wanted),
  );
  const named = exact.length
    ? exact
    : models.filter(
        (model) =>
          (model.display_name ?? '').toLowerCase().trimStart().split(/\s+/, 1)[0] === wanted ||
          model.model_id.toLowerCase().split('[', 1)[0] === wanted,
      );
  return named.map((model) => model.model_id);
}
