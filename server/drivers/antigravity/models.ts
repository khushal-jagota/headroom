/** Antigravity model ids are agy families such as gemini-4-argon. Each id is its own family; none
 * carries a version. */
import type { DriverModelRules } from '../core/driver.ts';

export const antigravity_models: DriverModelRules = {
  variant: (model_id) => model_id,
  version: () => [],
};
