/** One install command for Settings and the CLI; the credential stays outside the shell. */
import { shell_quote } from '../conversation/worker_unit/logic.ts';

export function require_released_revision(revision: unknown): string {
  if (typeof revision !== 'string' || !/^[0-9a-f]{40}$/.test(revision))
    throw new Error('Install a released home before you issue a join command');
  return revision;
}

export function join_command(
  home_url: string,
  revision: string,
  machine_id: string,
  credential: unknown,
): string {
  require_released_revision(revision);
  if (typeof credential !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(credential))
    throw new Error('The home returned an invalid machine credential');
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(machine_id))
    throw new Error('The home returned an invalid machine identity');
  const route = `repos/khushal-jagota/headroom/contents/scripts/panels_install.py?ref=${revision}`;
  const arguments_ = [
    'join',
    '--home',
    home_url,
    '--machine-id',
    machine_id.toLowerCase(),
    '--revision',
    revision,
  ];
  return (
    'panels_join_script=$(mktemp) && gh api -H ' +
    shell_quote('Accept: application/vnd.github.raw+json') +
    ' ' +
    shell_quote(route) +
    ' > "$panels_join_script" && python3 "$panels_join_script" ' +
    arguments_.map(shell_quote).join(' ')
  );
}
