/** Codex inherits the account environment. A copied rotating credential is refused. */
import { readlinkSync, realpathSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { CodexLoginIsDuplicated } from './errors.ts';
import { codex_home, logins_hold_the_same_credential } from './login.ts';

export function child_environment(
  options: {
    overrides?: readonly (readonly [string, string])[];
    identity?: readonly (readonly [string, string])[];
  } = {},
): Record<string, string> {
  const environment = Object.fromEntries(
    Object.entries(process.env).filter(
      (entry): entry is [string, string] => entry[1] !== undefined,
    ),
  );
  Object.assign(environment, Object.fromEntries(options.overrides ?? []));
  Object.assign(environment, Object.fromEntries(options.identity ?? []));
  refuse_a_duplicated_codex_login(environment);
  return environment;
}

// Path.resolve() in Python resolves existing symlinks even when the final path is absent.
function resolved_home(path: string): string {
  const absolute = resolve(path);
  try {
    return realpathSync(absolute);
  } catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'ENOENT') throw error;
    try {
      return resolved_home(resolve(dirname(absolute), readlinkSync(absolute)));
    } catch (link_error) {
      if (
        !(link_error instanceof Error) ||
        !('code' in link_error) ||
        !['ENOENT', 'EINVAL'].includes(String(link_error.code))
      )
        throw link_error;
    }
    const parent = dirname(absolute);
    return parent === absolute ? absolute : join(resolved_home(parent), basename(absolute));
  }
}

export function refuse_a_duplicated_codex_login(
  environment: Readonly<Record<string, string>>,
): void {
  const home = codex_home(environment);
  const real_home = codex_home(process.env);
  try {
    if (resolved_home(home) === resolved_home(real_home)) return;
  } catch {
    // An unresolved path is not evidence that the credentials differ.
    // Continue to the file comparison, which warns when it cannot read them.
  }
  const shared = logins_hold_the_same_credential(home, real_home);
  if (shared === null) {
    console.warn(
      `cannot tell whether the codex login under CODEX_HOME=${home} copies the one in ${real_home}, so the child is allowed to start`,
    );
    return;
  }
  if (shared) {
    throw new CodexLoginIsDuplicated(
      `CODEX_HOME=${home} holds the same codex credential as ${real_home}. Two homes sharing one rotating refresh token lose the rotation, so this child will not start. Give that home its own login, or use the real home.`,
    );
  }
}
