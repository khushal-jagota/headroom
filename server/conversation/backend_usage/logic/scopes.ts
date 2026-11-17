/** Allowance windows name models; keep only the windows that name one model on the card. */
import type { BackendSnapshot } from '../../snapshot/contracts.ts';
import type { BackendUsageResult, BackendUsageWindow } from '../contracts.ts';
export function resolve_usage_model_scopes(
  result: BackendUsageResult,
  snapshot: BackendSnapshot,
): BackendUsageResult {
  const windows: BackendUsageWindow[] = [];
  for (const window of result.windows) {
    if (window.model_scope === null && window.scope_label === null) {
      windows.push(window);
      continue;
    }
    const model_id =
      window.model_scope === null ? null : catalogue_model_id(snapshot, window.model_scope);
    if (model_id !== null) windows.push({ ...window, model_scope: model_id });
    else if (window.scope_label !== null)
      windows.push({ ...window, model_scope: window.scope_label });
  }
  return { ...result, windows };
}
function catalogue_model_id(snapshot: BackendSnapshot, scope: string): string | null {
  const wanted = scope.toLowerCase().trim();
  const exact = snapshot.available_models.filter((model) =>
    [model.model_id.toLowerCase(), (model.display_name ?? '').toLowerCase()].includes(wanted),
  );
  if (exact.length === 1) return exact[0].model_id;
  const family = snapshot.available_models.filter(
    (model) =>
      (model.display_name ?? '').toLowerCase().trimStart().split(/\s+/, 1)[0] === wanted ||
      model.model_id.toLowerCase().split('[', 1)[0] === wanted,
  );
  return family.length === 1 ? family[0].model_id : null;
}
