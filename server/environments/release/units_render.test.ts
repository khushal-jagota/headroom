/** Gap: classifier tests do not prove rendered service policy or private inputs. */
import { expect, test } from 'vitest';
import { render_managed_unit, render_watchdog_units } from './units.ts';

test('renders the managed policy and watchdog without private stop inputs in their environment', async () => {
  const root = '/nonexistent/panels-unit-proof';
  const options = {
    current_root: root + '/current',
    node_executable: process.execPath,
    config_path: root + '/config.json',
    environment: { RUNTIME_DIRECTORY: '/private-stop-root' },
  };
  const managed = await render_managed_unit({
    ...options,
    runtime_directory_name: 'panels-proof',
    admitted_legacy_null_sha: null,
    control_socket_path: root + '/control/server.sock',
    lease_path: root + '/run/lifecycle.lock',
    environment_file_path: root + '/managed.env',
  });
  const watchdog = await render_watchdog_units({
    ...options,
    database_path: root + '/database.sqlite',
    state_directory: root + '/watchdog',
    environment_file_path: root + '/watchdog.env',
  });
  for (const directive of [
    'KillMode=mixed',
    'OOMPolicy=continue',
    'OOMScoreAdjust=-500',
    'MemoryLow=1G',
    'Restart=on-failure',
    'NoNewPrivileges=yes',
    'PrivateTmp=yes',
  ])
    expect(managed.service.split('\n')).toContain(directive);
  expect(managed.service.split('\n').find((line) => line.startsWith('ExecStop='))).toContain(
    'stop_main.ts',
  );
  for (const rendered of [managed, watchdog])
    expect(
      rendered.environment_file.contents +
        rendered.service
          .split('\n')
          .filter((line) => line.startsWith('Environment='))
          .join('\n'),
    ).not.toMatch(/RUNTIME_DIRECTORY|capability/i);
});
