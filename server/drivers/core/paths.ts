/** Paths a manifest names through the environment. Each caller keeps its own existing rule. */
import { join } from 'node:path';
import type { DriverManifest } from './manifest.ts';

export type DriverHome = NonNullable<DriverManifest['home']>;
type Environment = Readonly<Record<string, string | undefined>>;

export interface HomePathRule {
  /** '||': an empty value counts as unset. '??': only an absent value does. */
  readonly unset: '||' | '??';
  /** Also expand a leading '~' in a configured value. The default path is always expanded. */
  readonly expand_configured: boolean;
}
function expand(path: string, account_home: string): string {
  return path === '~'
    ? account_home
    : path.startsWith('~/')
      ? join(account_home, path.slice(2))
      : path;
}
/** The home directory, with '~' resolved against account_home as the rule says. */
export function home_path(
  home: DriverHome,
  environment: Environment,
  account_home: string,
  rule: HomePathRule,
): string {
  const configured = environment[home.environment_name];
  const unset = rule.unset === '||' ? !configured : configured === undefined;
  if (unset) return expand(home.default_path, account_home);
  return rule.expand_configured ? expand(configured!, account_home) : configured!;
}
