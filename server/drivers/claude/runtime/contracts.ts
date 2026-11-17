/** Claude's card boundaries. */
export interface ClaudeModel {
  readonly model_id: string;
  readonly display_name: string;
  readonly resolved_model_id: string;
  readonly reasoning_effort_options: readonly string[];
}
export interface ClaudeModelCatalog {
  readonly models: readonly ClaudeModel[];
  readonly reasoning_effort_options: readonly string[];
  readonly default_model_id: string | null;
}
export interface ClaudeModelCatalogSource {
  (claude_executable: string): Promise<ClaudeModelCatalog>;
}
export class ClaudeModelCatalogUnavailable extends Error {}
