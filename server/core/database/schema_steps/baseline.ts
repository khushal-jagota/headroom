/** The frozen Python baseline seed this release's step chain starts from. */
import { readFileSync } from 'node:fs';
import type { ApplicationSchemaSeed } from '../contracts.ts';

export const PYTHON_BASELINE_SEED = JSON.parse(
  readFileSync(new URL('../python_baseline/application_schema.json', import.meta.url), 'utf8'),
) as ApplicationSchemaSeed;
