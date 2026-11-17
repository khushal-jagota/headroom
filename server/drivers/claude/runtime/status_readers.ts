/** Claude's card readers: its `auth status` answer and its local model catalog. */
import type {
  BackendIdentity,
  CatalogReader,
  CommandOutcome,
} from '../../../conversation/snapshot/contracts.ts';
import type { ClaudeModelCatalogSource } from '../../../conversation/snapshot/provider_contracts.ts';
import { ClaudeModelCatalogUnavailable } from '../../../conversation/snapshot/provider_contracts.ts';
import { answer, model } from '../../../conversation/snapshot/catalogs/readers.ts';
import { identity } from '../../../conversation/snapshot/identity.ts';
const CLAUDE_FALLBACK_REASONING_EFFORT_OPTIONS = ['low', 'medium', 'high', 'xhigh', 'max'];
const optional_text = (value: unknown): string | null =>
  typeof value === 'string' && value.trim() ? value.trim() : null;
export function read_claude_identity(outcome: CommandOutcome): BackendIdentity {
  if (outcome.exit_code !== 0) return identity('unknown');
  let reported: unknown;
  try {
    reported = JSON.parse(outcome.standard_output);
  } catch {
    return identity('unknown');
  }
  if (reported === null || typeof reported !== 'object' || Array.isArray(reported))
    return identity('unknown');
  const value = reported as Record<string, unknown>;
  if (typeof value.loggedIn !== 'boolean') return identity('unknown');
  if (!value.loggedIn) return identity('unauthenticated');
  const subscription = optional_text(value.subscriptionType);
  const method = optional_text(value.authMethod);
  const detail =
    subscription !== null && method !== null
      ? `${subscription} plan via ${method}`
      : subscription || method;
  return identity('authenticated', optional_text(value.email), detail);
}
export function claude_catalog_reader(source: ClaudeModelCatalogSource | null): CatalogReader {
  return async (request) => {
    try {
      if (source === null) throw new ClaudeModelCatalogUnavailable();
      const catalog = await source(request.executable_path);
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
  };
}
