/** Hermes' card: its recipe and the inventory its retained Python catalog script reports. */
import { probe_card } from '../../../conversation/snapshot/probe.ts';
import type { HermesModelCatalogSource } from '../../../conversation/snapshot/provider_contracts.ts';
import type { DriverStatus } from '../../core/status.ts';
import { hermes_driver } from '../driver.ts';
import { hermes_manifest } from '../manifest.ts';
import { hermes_models } from '../models.ts';
import { hermes_catalog_reader } from './status_readers.ts';

export function create_hermes_status(sources: {
  /** Null: the card lists no models and says the catalog could not be read. */
  readonly catalog: HermesModelCatalogSource | null;
}): DriverStatus {
  const read_catalog = hermes_catalog_reader(sources.catalog);
  return {
    // Hermes is not on PATH: it sits beside the configured Python. A machine card keeps its
    // version until the backstop.
    version_detects_upgrade: false,
    probe: (context) =>
      probe_card(
        {
          driver_kind: hermes_driver.driver_kind,
          manifest: hermes_manifest,
          models: hermes_models,
          read_identity: null,
          read_catalog,
        },
        context,
      ),
  };
}
