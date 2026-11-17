/**
 * The executor half of each built-in driver that an executor machine can run, loaded on demand.
 * A driver absent here runs on the home only.
 */
import type { ExecutorDriver } from './core/executor.ts';

export const BUILT_IN_EXECUTORS: Readonly<Record<string, () => Promise<ExecutorDriver>>> = {
  hermes: async () => (await import('./hermes/executor.ts')).hermes_executor,
  codex: async () => (await import('./codex/executor.ts')).codex_executor,
  claude: async () => (await import('./claude/executor.ts')).claude_executor,
};
