/** Antigravity's card: its recipe and the models a throwaway agy session offers. */
import { probe_card } from '../../../conversation/snapshot/probe.ts';
import type { DriverStatus } from '../../core/status.ts';
import { antigravity_driver } from '../driver.ts';
import { antigravity_manifest } from '../manifest.ts';
import { antigravity_models } from '../models.ts';
import type { AntigravityCatalogSource } from './contracts.ts';
import { antigravity_catalog_reader } from './status_readers.ts';

export function create_antigravity_status(sources: {
  /** Null: the card lists no models and says agy did not answer. */
  readonly catalog: AntigravityCatalogSource | null;
}): DriverStatus {
  const read_catalog = antigravity_catalog_reader(sources.catalog);
  return {
    version_detects_upgrade: true,
    probe: (context) =>
      probe_card(
        {
          driver_kind: antigravity_driver.driver_kind,
          manifest: antigravity_manifest,
          models: antigravity_models,
          read_identity: null,
          read_catalog,
        },
        context,
      ),
  };
}
