/** Hermes model ids are provider-qualified. Each id is its own family; none carries a version. */
import type { DriverModelRules } from '../core/driver.ts';

export const hermes_models: DriverModelRules = {
  variant: (model_id) => model_id,
  version: () => [],
};
