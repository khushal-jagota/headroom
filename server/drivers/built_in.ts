/** The one list of drivers this build carries. The only door into a driver package. */
import type { AnyAgentDriver } from './core/driver.ts';
import { claude_driver } from './claude/driver.ts';
import { codex_driver } from './codex/driver.ts';
import { hermes_driver } from './hermes/driver.ts';

/** Card order and the order of the unknown-backend error detail. */
export const BUILT_IN_DRIVERS: readonly AnyAgentDriver[] = [
  hermes_driver,
  codex_driver,
  claude_driver,
];
