/** The shared answer and model shapes every driver's catalog reader builds. */
import type { CatalogAnswer, BackendModel } from '../contracts.ts';
export function answer(overrides: Partial<CatalogAnswer> = {}): CatalogAnswer {
  return {
    models: [],
    reasoning_effort_options: [],
    default_model_id: null,
    default_reasoning_effort: null,
    diagnoses: [],
    complete: false,
    identity: null,
    ...overrides,
  };
}
export function model(
  overrides: Pick<BackendModel, 'model_id'> & Partial<BackendModel>,
): BackendModel {
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
