/** Codex's card: its recipe, its `login status` answer with the saved login, and its catalog. */
import { probe_card } from '../../../conversation/snapshot/probe.ts';
import type { CodexLoginStanding, CodexModelCatalogSource } from './contracts.ts';
import type { DriverStatus } from '../../core/status.ts';
import { codex_driver } from '../driver.ts';
import { codex_manifest } from '../manifest.ts';
import { codex_models } from '../models.ts';
import { codex_catalog_reader, read_codex_identity } from './status_readers.ts';

export function create_codex_status(sources: {
  /** Null: the card lists no models and says Codex did not answer. */
  readonly catalog: CodexModelCatalogSource | null;
  /** Null: the card reads only the CLI's own answer. */
  readonly read_login_standing: (() => CodexLoginStanding | null) | null;
}): DriverStatus {
  const read_catalog = codex_catalog_reader(sources.catalog);
  const standing = () => sources.read_login_standing?.() ?? null;
  return {
    version_detects_upgrade: true,
    // A kept card is stale once the saved login it read has changed.
    freshness_key(now) {
      const current = standing();
      return current === null ? null : JSON.stringify(current.everything_a_card_reads({ now }));
    },
    probe(context) {
      const login = standing();
      return probe_card(
        {
          driver_kind: codex_driver.driver_kind,
          manifest: codex_manifest,
          models: codex_models,
          read_identity: (outcome) => read_codex_identity(outcome, login, context.now),
          read_catalog,
        },
        context,
      );
    },
  };
}
