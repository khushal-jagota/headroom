/** A card shows a model-scoped allowance on the catalog models the provider's scope names. */
import type { BackendSnapshot } from '../../snapshot/contracts.ts';
import type { BackendUsageWindow } from '../contracts.ts';
type Models = BackendSnapshot['available_models'];
/**
 * The catalog models each window covers, in window order. A window covers the models its scope
 * names exactly, else every model of that family that no other window names exactly: a `Fable`
 * window covers Fable 5, 5.1 and 5.2, and a `Gemini` window leaves Gemini 4 Argon to its own.
 * An account-wide window covers none.
 */
export function usage_window_model_ids(
  windows: readonly BackendUsageWindow[],
  models: Models,
): string[][] {
  const exact = windows.map((window) => exactly_named(window.model_scope, models));
  const named_elsewhere = new Set(exact.flat());
  return windows.map((window, index) => {
    if (window.model_scope === null || exact[index]!.length) return exact[index]!;
    const family = window.model_scope.toLowerCase().trim();
    return models
      .filter(
        (model) =>
          !named_elsewhere.has(model.model_id) &&
          ((model.display_name ?? '').toLowerCase().trimStart().split(/\s+/, 1)[0] === family ||
            model.model_id.toLowerCase().split('[', 1)[0] === family),
      )
      .map((model) => model.model_id);
  });
}
function exactly_named(scope: string | null, models: Models): string[] {
  if (scope === null) return [];
  const wanted = scope.toLowerCase().trim();
  return models
    .filter((model) =>
      [model.model_id.toLowerCase(), (model.display_name ?? '').toLowerCase()].includes(wanted),
    )
    .map((model) => model.model_id);
}
