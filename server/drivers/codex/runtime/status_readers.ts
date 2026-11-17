/** Codex's card readers: its `login status` answer with the saved login, and its model catalog. */
import type {
  BackendIdentity,
  CatalogReader,
  CommandOutcome,
} from '../../../conversation/snapshot/contracts.ts';
import type {
  CodexLoginStanding,
  CodexModelCatalogSource,
} from '../../../conversation/snapshot/provider_contracts.ts';
import { CodexModelCatalogUnavailable } from '../../../conversation/snapshot/provider_contracts.ts';
import { answer, model } from '../../../conversation/snapshot/catalogs/readers.ts';
import { identity } from '../../../conversation/snapshot/identity.ts';
export function read_codex_identity(
  outcome: CommandOutcome,
  standing: CodexLoginStanding | null,
  now: Date,
): BackendIdentity {
  const reported = outcome.standard_output.trim() || outcome.standard_error.trim();
  const cli_line = reported ? reported.split(/[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/)[0] : null;
  if (outcome.exit_code !== 0 || reported.toLowerCase().includes('not logged in'))
    return identity('unauthenticated');
  if (standing === null)
    return reported ? identity('authenticated', cli_line) : identity('unknown');
  const stored = standing.stored;
  const label = stored.account_label || cli_line;
  if (!stored.present) return identity('unauthenticated');
  if (standing.rejected)
    return identity(
      'unauthenticated',
      label,
      'This saved login was rejected the last time codex used it.',
    );
  if (!stored.readable)
    return identity(
      'unknown',
      label,
      'Panels could not read the saved codex login, so it cannot say more.',
    );
  if (stored.access_token_has_expired({ now }))
    return identity(
      'unknown',
      label,
      'This saved login expired and codex has not refreshed it yet. Panels cannot confirm it works until a turn runs.',
    );
  if (!reported) return identity('unknown', label);
  return identity('authenticated', label);
}
export function codex_catalog_reader(source: CodexModelCatalogSource | null): CatalogReader {
  return async (request) => {
    try {
      if (source === null) throw new CodexModelCatalogUnavailable();
      const catalog = await source(request.executable_path);
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
  };
}
