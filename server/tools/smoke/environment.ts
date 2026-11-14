/** Private production configuration with the existing external Codex wire fixture. */
import assert from 'node:assert/strict';
import { mkdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { isAbsolute, join, relative, sep } from 'node:path';

/** Bootstrap the unchanged Python comparison seed without ambient account or runtime inputs. */
export async function create_seed_environment(
  root: string,
  source: string,
): Promise<Record<string, string>> {
  const bootstrap = join(root, 'bootstrap');
  for (const directory of [
    'home',
    'codex',
    'claude',
    'hermes',
    'logs',
    'backups',
    'workspace',
    'run',
  ])
    await mkdir(join(bootstrap, directory), { recursive: true, mode: 0o700 });
  await writeFile(join(bootstrap, 'config.yaml'), '{}\n', { flag: 'wx', mode: 0o600 });
  return {
    PATH: '/usr/bin:/bin',
    LANG: 'C.UTF-8',
    HOME: join(bootstrap, 'home'),
    CODEX_HOME: join(bootstrap, 'codex'),
    CLAUDE_CONFIG_DIR: join(bootstrap, 'claude'),
    HERMES_HOME: join(bootstrap, 'hermes'),
    PLAN_HERMES_HOME: join(bootstrap, 'hermes'),
    TMPDIR: root,
    TMP: root,
    TEMP: root,
    PYTHONPATH: join(source, 'scripts'),
    PYTHONDONTWRITEBYTECODE: '1',
    PLAN_CONFIG_PATH: join(bootstrap, 'config.yaml'),
    PLAN_DB_PATH: join(bootstrap, 'planning.db'),
    PLAN_LOGS_DIR: join(bootstrap, 'logs'),
    PLAN_BACKUP_DIR: join(bootstrap, 'backups'),
    PLAN_WORKSPACE_ROOT: join(bootstrap, 'workspace'),
    PLAN_CREDENTIAL_KEY_PATH: join(bootstrap, 'credential-key'),
    PLAN_DISPATCHER_LOCK_PATH: join(bootstrap, 'run/dispatcher.lock'),
    PLAN_SERVER_CONTROL_SOCKET: join(bootstrap, 'run/control.sock'),
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(bootstrap, 'run/lifecycle.lock'),
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    PLAN_PORT: '0',
    PLAN_TEST_MODE: '1',
    PLAN_DISPATCH_ENABLED: 'false',
  };
}

/** The caller admits the populated seed's readonly quiescence before server startup. */
export async function create_environment(
  root: string,
  source: string,
  app_root: string,
  base: string,
  sha: string,
): Promise<Record<string, string>> {
  root = await realpath(root);
  source = await realpath(source);
  app_root = await realpath(app_root);
  assert.match(sha, /^[a-f0-9]{40}$/);
  const url = new URL(base);
  assert.equal(url.protocol, 'http:');
  assert.equal(url.hostname, '127.0.0.1');
  assert.ok(url.port && url.port !== '0');
  assert.equal(url.pathname, '/');
  assert.equal(url.username + url.password + url.search + url.hash, '');
  const homes = JSON.parse(await readFile(join(root, 'homes.json'), 'utf8')) as Record<
    string,
    string
  >;
  const environment: Record<string, string> = {};
  for (const name of [
    'HOME',
    'CODEX_HOME',
    'CLAUDE_CONFIG_DIR',
    'HERMES_HOME',
    'PLAN_HERMES_HOME',
  ]) {
    const value = homes[name];
    assert.ok(typeof value === 'string');
    const home = await realpath(value),
      suffix = relative(root, home);
    assert.ok(suffix && suffix !== '..' && !suffix.startsWith('..' + sep) && !isAbsolute(suffix));
    environment[name] = home;
  }
  for (const name of [
    'bin',
    'run',
    'tmp',
    'logs',
    'backups',
    'workspace',
    'config',
    'cache',
    'state',
  ])
    await mkdir(join(root, name), { recursive: true, mode: 0o700 });
  await writeFile(join(root, 'config.yaml'), '{}\n', {
    flag: 'wx',
    mode: 0o600,
  });
  const script = join(root, 'codex-script.json');
  const producer = JSON.stringify(
    join(source, 'tests/support/conversation_codex_scripted_app_server.py'),
  );
  const actions = [
    { do: 'agent_delta', item_id: 'smoke-answer', text: 'Production smoke reply.' },
    {
      do: 'item_completed',
      item: { type: 'agentMessage', id: 'smoke-answer', text: 'Production smoke reply.' },
    },
    { do: 'complete', status: 'completed' },
  ];
  await writeFile(
    script,
    JSON.stringify({
      transcript_path: join(root, 'codex-transcript.jsonl'),
      thread_id: 'production-smoke-thread',
      turns: [{ actions }],
    }) + '\n',
    { flag: 'wx', mode: 0o600 },
  );
  await writeFile(
    join(root, 'bin/codex'),
    `#!${join(source, '.venv/bin/python')}\n` +
      'import runpy, sys\n' +
      "if sys.argv[1:] == ['--version']:\n" +
      "    print('codex-cli 0.148.0')\n" +
      'else:\n' +
      `    runpy.run_path(${producer}, run_name='__main__')\n`,
    { flag: 'wx', mode: 0o700 },
  );
  return {
    ...environment,
    PATH: join(root, 'bin') + ':/usr/bin:/bin',
    LANG: 'C.UTF-8',
    TMPDIR: join(root, 'tmp'),
    TMP: join(root, 'tmp'),
    TEMP: join(root, 'tmp'),
    XDG_CONFIG_HOME: join(root, 'config'),
    XDG_CACHE_HOME: join(root, 'cache'),
    XDG_DATA_HOME: join(root, 'data'),
    XDG_STATE_HOME: join(root, 'state'),
    XDG_RUNTIME_DIR: join(root, 'run'),
    PYTHONDONTWRITEBYTECODE: '1',
    PLAN_CONFIG_PATH: join(root, 'config.yaml'),
    PLAN_DB_PATH: join(root, 'data/planning.db'),
    PLAN_APP_ROOT: app_root,
    PLAN_APP_SHA: sha,
    PLAN_PORT: url.port,
    PLAN_SERVER_URL: url.origin,
    PLAN_LOGS_DIR: join(root, 'logs'),
    PLAN_BACKUP_DIR: join(root, 'backups'),
    PLAN_CREDENTIAL_KEY_PATH: join(root, 'data/credential-key'),
    PLAN_WORKSPACE_ROOT: join(root, 'workspace'),
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_TEST_MODE: '0',
    PLAN_DISPATCHER_LOCK_PATH: join(root, 'run/dispatcher.lock'),
    PLAN_SERVER_CONTROL_SOCKET: join(root, 'run/control.sock'),
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: join(root, 'run/lifecycle.lock'),
    PLAN_CLAUDE_EXECUTABLE: join(root, 'bin/absent-claude'),
    PLAN_HERMES_PYTHON: join(root, 'bin/absent-hermes'),
    PANELS_CODEX_SCRIPTED_APP_SERVER_SCRIPT: script,
  };
}
