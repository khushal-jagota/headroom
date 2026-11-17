/** Adapt provider-owned local catalogs into the one Panels card contract. */
import type { CatalogAnswer, CatalogReader, BackendModel } from '../contracts.ts';
import type {
  CodexModelCatalogSource,
  ClaudeModelCatalogSource,
  HermesModelCatalogSource,
} from '../provider_contracts.ts';
import { claude_catalog_reader } from '../../../drivers/claude/runtime/status_readers.ts';
import { codex_catalog_reader } from '../../../drivers/codex/runtime/status_readers.ts';
import { hermes_catalog_reader } from '../../../drivers/hermes/runtime/status_readers.ts';
export function answer(overrides: Partial<CatalogAnswer> = {}): CatalogAnswer {
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
export function create_catalog_readers(sources: {
  readonly codex: CodexModelCatalogSource | null;
  readonly claude: ClaudeModelCatalogSource | null;
  readonly hermes: HermesModelCatalogSource | null;
}): Readonly<Record<string, CatalogReader>> {
  return {
    codex: codex_catalog_reader(sources.codex),
    claude: claude_catalog_reader(sources.claude),
    hermes: hermes_catalog_reader(sources.hermes),
  };
}
