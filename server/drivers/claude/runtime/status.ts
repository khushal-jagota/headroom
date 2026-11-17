/** Claude's card: its recipe, its `auth status` answer and its local model catalog. */
import { probe_card } from '../../../conversation/snapshot/probe.ts';
import type { ClaudeModelCatalogSource } from './contracts.ts';
import type { DriverStatus } from '../../core/status.ts';
import { claude_driver } from '../driver.ts';
import { claude_manifest } from '../manifest.ts';
import { claude_models } from '../models.ts';
import { claude_catalog_reader, read_claude_identity } from './status_readers.ts';

export function create_claude_status(sources: {
  /** Null: the card lists no models and says Claude did not answer. */
  readonly catalog: ClaudeModelCatalogSource | null;
}): DriverStatus {
  const read_catalog = claude_catalog_reader(sources.catalog);
  return {
    version_detects_upgrade: true,
    probe: (context) =>
      probe_card(
        {
          driver_kind: claude_driver.driver_kind,
          manifest: claude_manifest,
          models: claude_models,
          read_identity: read_claude_identity,
          read_catalog,
        },
        context,
      ),
  };
}
