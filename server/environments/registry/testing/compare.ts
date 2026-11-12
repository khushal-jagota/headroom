/** Export-only registry/fixture sequence. All locations come from the private driver. */
import { cp, mkdir, readFile, readdir, rename, stat, writeFile, unlink } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { createHash } from 'node:crypto';
import { configured_worker_runtime_definitions } from '../../../worker_types/configuration.ts';
import { acquire_server_lifecycle_lease } from '../../../lifecycle/lease.ts';
import { startServer } from '../../../core/start.ts';
import {
  prepare_environment_instance,
  inspect_environment_instance,
  reset_environment_instance,
  remove_environment_instance,
} from '../materialize.ts';
import {
  environment_lifecycle_lease_path,
  environment_lifecycle_lease_is_held,
  instance_from_manifest,
  manifest_to_json_dict,
} from '../store.ts';
import {
  parse_environment_file,
  validate_environment_values,
  validate_repository_roots,
} from '../logic.ts';
import { build_test_environment_run_env, provision_environment_skills } from '../launch.ts';

interface Request {
  root: string;
  repository: string;
  credentials: string;
  output: string;
}
async function error_of(action: () => unknown): Promise<string> {
  try {
    await action();
    return 'NO ERROR';
  } catch (error) {
    return (error as Error).message;
  }
}
const digest = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
export async function registry_probe(request: Request): Promise<unknown> {
  const invalid_repository = join(dirname(request.root), 'invalid-repository');
  await mkdir(join(invalid_repository, 'gitdirX'), { recursive: true });
  await writeFile(join(invalid_repository, '.git'), 'gitdirX');
  await writeFile(join(invalid_repository, 'gitdirX/HEAD'), 'ref: refs/heads/main\n');
  const invalid_git_marker = await error_of(() =>
    validate_repository_roots([invalid_repository], [invalid_repository]),
  );
  await writeFile(join(invalid_repository, '.git'), '\ufeffgitdir: gitdirX\n');
  const bom_git_marker = await error_of(() =>
    validate_repository_roots([invalid_repository], [invalid_repository]),
  );
  const bom_credentials_path = join(dirname(request.root), 'bom.env');
  await writeFile(bom_credentials_path, '\ufeffOPENAI_API_KEY=fake\n');
  const bom_credentials = await error_of(() =>
    parse_environment_file(bom_credentials_path, 'staging'),
  );
  const options = {
    kind: 'staging' as const,
    environment_root: request.root,
    repository_roots: [request.repository],
    credentials_env_file: request.credentials,
  };
  const previous = configured_worker_runtime_definitions();
  const before = await inspect_environment_instance(options);
  const prepared = await prepare_environment_instance({ ...options, now: 1_800_000_000 });
  const restored_registry = configured_worker_runtime_definitions() === previous;
  const disk = await readFile(join(prepared.instance_root, 'manifest.json'), 'utf8');
  const tampered = JSON.parse(disk);
  tampered.db_path = join(request.output, 'outside.db');
  await writeFile(join(prepared.instance_root, 'manifest.json'), JSON.stringify(tampered));
  let invalid_manifest: string;
  try {
    invalid_manifest = await error_of(() => inspect_environment_instance(options));
  } finally {
    await writeFile(join(prepared.instance_root, 'manifest.json'), disk);
  }
  const forbidden_credentials = await error_of(() =>
    validate_environment_values({ PLAN_DB_PATH: '/forbidden' }, 'staging'),
  );
  const idempotent = await prepare_environment_instance({ ...options, now: 1_800_000_001 });
  await mkdir(request.output, { recursive: true });
  await cp(dirname(prepared.db_path), join(request.output, 'initial'), { recursive: true });
  const hash = digest(await readFile(prepared.db_path));
  const fail_fixture = await error_of(() =>
    reset_environment_instance(options, {
      build_fixture: () => {
        throw new Error('injected fixture failure');
      },
      rename,
    }),
  );
  const fixture_unchanged = hash === digest(await readFile(prepared.db_path));
  let injected = false;
  const fail_rename = await error_of(() =>
    reset_environment_instance(options, {
      build_fixture: (path) => cp(dirname(prepared.db_path), dirname(path), { recursive: true }),
      rename: async (source, destination) => {
        if (
          !injected &&
          destination === dirname(prepared.db_path) &&
          !source.includes('-backup-')
        ) {
          injected = true;
          throw new Error('injected rename failure');
        }
        await rename(source, destination);
      },
    }),
  );
  const rename_unchanged = hash === digest(await readFile(prepared.db_path));
  const lease = acquire_server_lifecycle_lease(environment_lifecycle_lease_path(prepared), 0);
  let running_reset: string, running_remove: string;
  try {
    running_reset = await error_of(() => reset_environment_instance(options));
    running_remove = await error_of(() => remove_environment_instance(options));
  } finally {
    lease.close();
  }
  // The lifecycle lease is deliberately FREE: the real direct server owns its DB lock.
  const fields = {
    PLAN_DB_PATH: prepared.db_path,
    PLAN_CONFIG_PATH: join(request.output, 'server-config.yaml'),
    PLAN_PORT: '0',
    PLAN_TEST_MODE: '1',
    PLAN_DISPATCH_ENABLED: 'false',
    PLAN_SERVER_URL: 'http://127.0.0.1:0',
    PLAN_SERVER_LIFECYCLE_LEASE_PATH: environment_lifecycle_lease_path(prepared),
    PLAN_SERVER_CONTROL_SOCKET: prepared.server_control_socket_path,
  };
  const saved = Object.fromEntries(
    [...Object.keys(fields), 'PLAN_SERVER_LISTENER_FD'].map((key) => [key, process.env[key]]),
  );
  let server_owner: unknown;
  Object.assign(process.env, fields);
  delete process.env.PLAN_SERVER_LISTENER_FD;
  try {
    const server = await startServer();
    try {
      server_owner = {
        running: environment_lifecycle_lease_is_held(prepared),
        reset: await error_of(() => reset_environment_instance(options)),
        remove: await error_of(() => remove_environment_instance(options)),
      };
    } finally {
      await server.close();
    }
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
  await unlink(prepared.db_path);
  const missing_database_status = environment_lifecycle_lease_is_held(prepared);
  const reset = await reset_environment_instance({ ...options, now: 1_800_000_002 });
  await cp(dirname(reset.db_path), join(request.output, 'reset'), { recursive: true });
  const run_env = build_test_environment_run_env(instance_from_manifest(reset), {
    credentials: await parse_environment_file(request.credentials, 'staging'),
    ambient: {
      HOME: '/synthetic/home',
      PATH: '/synthetic/bin',
      LANG: 'C',
      LC_ALL: 'C',
      PLAN_DB_PATH: '/forbidden',
      SECRET: 'forbidden',
    },
    hermes_python: '/synthetic/python',
    runtime_port: 32123,
  });
  const homes = ['hermes', 'codex', 'claude'].map((name) => join(request.output, name));
  provision_environment_skills({
    database_parent: dirname(reset.db_path),
    hermes_home: homes[0]!,
    codex_home: homes[1]!,
    claude_home: homes[2]!,
  });
  const provisioned = await Promise.all(
    homes.map(async (home) =>
      Object.fromEntries(
        await Promise.all(
          (await readdir(join(home, 'skills')))
            .sort()
            .map(async (name) => [
              name,
              digest(await readFile(join(home, 'skills', name, 'SKILL.md'))),
            ]),
        ),
      ),
    ),
  );
  await unlink(reset.db_path);
  await remove_environment_instance(options);
  return {
    before: manifest_to_json_dict(before),
    prepared: manifest_to_json_dict(prepared),
    idempotent: manifest_to_json_dict(idempotent),
    reset: manifest_to_json_dict(reset),
    disk,
    restored_registry,
    server_owner,
    missing_database_status,
    invalid_git_marker,
    bom_git_marker,
    bom_credentials,
    invalid_manifest: invalid_manifest!,
    forbidden_credentials,
    fail_fixture,
    fixture_unchanged,
    fail_rename,
    rename_unchanged,
    injected,
    running_reset: running_reset!,
    running_remove: running_remove!,
    run_env,
    provisioned,
    removed: await stat(prepared.instance_root).then(
      () => false,
      () => true,
    ),
  };
}
