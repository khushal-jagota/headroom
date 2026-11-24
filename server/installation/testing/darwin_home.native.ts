/** Native-only fixture payloads use real sealed archives and the retained installer. */
import assert from 'node:assert/strict';
import { chmod, lstat, readFile, rename, writeFile } from 'node:fs/promises';
import { realpathSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { build_mac_home } from '../../environments/release/mac_home_build.ts';
import type { MacHomeBuildResult } from '../../environments/release/mac_home_contracts.ts';
import { extract_git_export, extract_app_archive } from '../../environments/release/archive.ts';
import { MAC_HOME } from '../../environments/release/contracts.ts';
import type { ReleaseExecution } from '../../environments/release/contracts.ts';
import { run_release_command } from '../../environments/release/process.ts';
import { admit_runtime } from '../../environments/release/admission.ts';
import { release_path } from '../../environments/updates/installer/selection.ts';
import { create_darwin_home_io } from '../../environments/updates/darwin/service.ts';
import { read_home_job } from '../../environments/updates/darwin/jobs.ts';
import { authenticate_loaded_job } from '../../environments/updates/darwin/logic/launchd.ts';
import { healthy } from '../../environments/updates/installer/health.ts';
import { DarwinProcessInspector } from '../../previews/darwin.ts';
import type { DarwinWorkerProcesses } from '../../platform_runtime/darwin/worker_processes.ts';
import type { WorkerProcess } from '../../worker_supervision/contracts.ts';

const cli = `#!/usr/bin/env node
import {run_server_supervisor} from './lifecycle/supervisor.ts';
import {native_control_socket_path} from './lifecycle/paths.ts';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
if(process.argv[2]==='serve') {
  const root=process.cwd(),app=fileURLToPath(new URL('../',import.meta.url));
  process.exitCode=await run_server_supervisor({mode:{kind:'foreground'},application:{node_executable:process.execPath,entrypoint:join(app,'server/native_probe_app.mjs'),launch_root:root,environment:{...process.env}},control_socket_path:native_control_socket_path(join(root,'data/control.sock')),lease_path:join(root,'data/lease'),port:Number(process.env.PLAN_PORT)});
} else {
  const {run}=await import('./cli/main.ts');const result=await run(process.argv.slice(2));
  if(result.stdout)process.stdout.write(result.stdout);if(result.stderr)process.stderr.write(result.stderr);process.exitCode=result.exit_code;
}
`;
const app = `import{createServer}from'node:http';
import{readFileSync,writeFileSync,existsSync,realpathSync}from'node:fs';
import{join}from'node:path';import{fileURLToPath}from'node:url';
import{createDatabase}from'./core/database/index.ts';
import{ChildProcesses}from'./lifecycle/owned_processes.ts';
import{current_darwin_home}from'./environments/updates/darwin/launch.ts';
import{create_installer_operations}from'./environments/updates/installation.ts';
import{FileUpdateAttemptStore,utc_now}from'./environments/updates/attempts.ts';
import{UpdateBarrier}from'./environments/update_barrier.ts';
const root=process.cwd(),application=fileURLToPath(new URL('../',import.meta.url)),data=join(root,'data'),database=process.env.PLAN_DB_PATH;
const revision=JSON.parse(readFileSync(join(application,'manifest.json'),'utf8')).app_sha;
const seed=JSON.parse(readFileSync(join(application,'server/core/database/application_schema.json'),'utf8'));
if(!existsSync(database))throw Error('The first-home installer did not initialize its database');
const db=createDatabase(database);
if(db.read(sql=>sql.get('SELECT version_num FROM alembic_version'))?.version_num!==seed.alembicRevision)throw Error('The installed database schema differs from its payload');
if(!db.read(sql=>sql.get("SELECT id FROM projects WHERE id='native-proof'"))){
 if(existsSync(join(data,'update-attempt.json')))throw Error('The fixture project disappeared during update');
 db.write(tx=>tx.run("INSERT INTO projects(id,name,created_at,updated_at) VALUES('native-proof','sentinel',0,0)"));
}
const policy=()=>{try{return JSON.parse(readFileSync(join(data,'probe-policy.json'),'utf8'));}catch{return{};}};
if(policy().fail_sha===revision)db.write(tx=>tx.run("UPDATE projects SET name='candidate-write' WHERE id='native-proof'"));
const children=new ChildProcesses(),controller=new AbortController();let launching=false;
const server=createServer(async(req,res)=>{
 try {
  if(req.method==='POST'&&req.url==='/native-update'){
   if(launching)throw Error('One fixture update at a time');launching=true;
   const input=JSON.parse(readFileSync(join(data,'probe-request.json'),'utf8'));
   const scope={child_processes:children,signal:controller.signal,deadline:performance.now()/1000+120};
   const home=await current_darwin_home(application,scope,process.env);
   const ops=create_installer_operations({application_root:application,child_processes:children,signal:controller.signal,environment:{...process.env},darwin_home:home});
   if(ops.requires_snapshot!==true)throw Error('Mac home updater requires its snapshot');
   await ops.node_schema_steps_pending(database,join(root,'.app-next-'+input.manifest.app_sha));
   const now=utc_now();
   const release={publication_run_id:1,artifact_id:1,proof_artifact_id:2,manifest:input.manifest,archive_sha256:input.archive_sha256,schema_floor:seed.alembicRevision,expires_at:'2099-01-01T00:00:00+00:00',mac:null};
   new FileUpdateAttemptStore(join(data,'update-attempt.json'),()=>{}).create({attempt_id:input.attempt_id,from_sha:revision,to_sha:input.manifest.app_sha,mode:'now',confirm_activity:false,state:'installing',created_at:now,updated_at:now,release,detail:null,deadline_at:null,backup:null,snapshot_required:true,steps:{},mac_attempt_id:null,extra:{}});
   new UpdateBarrier(join(data,'update-barrier.json')).freeze(input.attempt_id);
   const command=ops.prepare({current_root:root,database,data_directory:data,backup_directory:join(data,'backups'),attempt_id:input.attempt_id,service_unit:home.identity.domain+'/'+home.identity.label,health_url:'http://127.0.0.1:'+process.env.PLAN_PORT+'/api/health',health_timeout_seconds:8});
   writeFileSync(join(data,'probe-launch-'+input.attempt_id+'.json'),JSON.stringify({command,app_pid:process.pid,supervisor_pid:process.ppid}));
   res.end(JSON.stringify({prepared:true}));
   void ops.launch(input.attempt_id,command).catch(error=>writeFileSync(join(root,'logs/probe-launch-error-'+input.attempt_id+'.log'),String(error.stack||error)));
  }else{
   const current=policy(),ready=current.fail_sha!==revision&&current.hold_sha!==revision;
   res.statusCode=ready?200:503;res.setHeader('content-type','application/json');
   // The live-upgrade gate checks the real local machine. This fixture controls
   // that observation to fault the independent native installer on demand.
   const marker=join(data,'update-barrier.json'),prepared_attempt_id=ready&&existsSync(marker)?readFileSync(marker,'utf8').trim():null;
   res.end(JSON.stringify({app_sha:revision,ready,prepared_attempt_id,project_name:db.read(sql=>sql.get("SELECT name FROM projects WHERE id='native-proof'").name)}));
  }
 }catch(error){res.statusCode=500;res.end(String(error.stack||error));}
});
server.listen(Number(process.env.PLAN_PORT),'127.0.0.1');
process.once('SIGTERM',()=>{controller.abort();children.seal();children.force_kill(performance.now()/1000+5);server.closeAllConnections();server.close(async()=>{await children.settled();db.close();process.exit(0);});});
`;
async function wait<T>(read: () => Promise<T | undefined>, seconds = 30): Promise<T> {
  const until = performance.now() + seconds * 1000;
  while (performance.now() < until) {
    const value = await read();
    if (value !== undefined) return value;
    await delay(25);
  }
  throw new Error('Native retained installer observation timed out');
}
export async function build_native_mini_homes(base: string, execution: ReleaseExecution) {
  const inputs = process.env.MAC_HOME_PRODUCTION_INPUTS,
    metadata = process.env.MAC_HOME_BUILD_METADATA;
  if (!inputs || !metadata)
    throw new Error('Prepared Mac production inputs and build metadata are required');
  const production = JSON.parse(await readFile(metadata, 'utf8')) as MacHomeBuildResult;
  assert.equal(production.manifest.runtime_contract?.system, 'darwin');
  const repository = fileURLToPath(new URL('../../../', import.meta.url)),
    source = join(base, 'mini-source');
  const environment = {
    PATH: process.env.PATH,
    HOME: join(base, 'account'),
    GIT_CONFIG_NOSYSTEM: '1',
    GIT_CONFIG_GLOBAL: '/dev/null',
  };
  const command = async (args: string[], cwd = repository) =>
    (
      await run_release_command(args, { cwd, environment, timeout_seconds: 60 }, execution)
    ).stdout.trim();
  const archive = join(base, 'mini-source.tar');
  await command(['git', 'archive', '--format=tar', '--output=' + archive, 'HEAD']);
  await extract_git_export(archive, source, execution);
  await writeFile(join(source, 'server/native_probe_cli.mjs'), cli, { mode: 0o755 });
  await writeFile(join(source, 'server/native_probe_app.mjs'), app);
  await command(['git', 'init', '--template=', source]);
  const tracked = await run_release_command(
    ['git', 'ls-files', '-z'],
    { cwd: repository, environment, timeout_seconds: 60 },
    execution,
  );
  const pathspec = join(base, 'mini-source.pathspec');
  await writeFile(
    pathspec,
    tracked.stdout +
      'server/native_probe_cli.mjs\0server/native_probe_app.mjs\0NATIVE_PROBE_REVISION\0',
    { flag: 'wx', mode: 0o600 },
  );
  const builds: MacHomeBuildResult[] = [],
    phases: Record<string, unknown>[] = [];
  for (const name of ['A', 'B']) {
    const started = performance.now();
    await writeFile(join(source, 'NATIVE_PROBE_REVISION'), name + '\n');
    // Preserve the original tracked export, including ignored domain data/ source directories.
    await command([
      'git',
      '--literal-pathspecs',
      '-C',
      source,
      'add',
      '--force',
      '--pathspec-from-file=' + pathspec,
      '--pathspec-file-nul',
    ]);
    await command([
      'git',
      '-C',
      source,
      '-c',
      'user.name=Native proof',
      '-c',
      'user.email=native@panels.invalid',
      '-c',
      'commit.gpgsign=false',
      '-c',
      'core.hooksPath=/dev/null',
      'commit',
      '-qm',
      'native mini ' + name,
    ]);
    const sha = await command(['git', '-C', source, 'rev-parse', 'HEAD']);
    const output = join(base, 'mini-build-' + name);
    const result = await build_mac_home(
      {
        source_root: source,
        requested_sha: sha,
        output_directory: output,
        production_node_modules: join(inputs, 'server/node_modules'),
        agent_backends_root: join(inputs, 'agent_backends'),
        web_dist: join(inputs, 'web/dist'),
      },
      { public_cli_entrypoint: 'server/native_probe_cli.mjs' },
      execution,
    );
    builds.push(result);
    phases.push({ name, seconds: (performance.now() - started) / 1000, ...result });
    await rename(join(output, 'app'), join(base, 'retired-build-' + name));
    await chmod(join(base, 'retired-build-' + name), 0);
  }
  await rename(source, join(base, 'retired-mini-source'));
  await chmod(join(base, 'retired-mini-source'), 0);
  return { a: builds[0]!, b: builds[1]!, phases, production_revision: production.manifest.app_sha };
}
export async function native_retained_updates(options: {
  root: string;
  account: string;
  label: string;
  port: number;
  base: string;
  builds: Awaited<ReturnType<typeof build_native_mini_homes>>;
  execution(): ReleaseExecution;
  primitives: DarwinWorkerProcesses;
  births: WorkerProcess[];
  record: Record<string, unknown>;
}) {
  const { root, account, label, port, builds, execution, primitives, births, record } = options;
  // This IO spans both archive admissions and updates. Each command needs a fresh scope.
  const domain = `gui/${process.getuid!()}`,
    io = create_darwin_home_io(execution(), { HOME: account });
  const inspector = new DarwinProcessInspector(),
    planned: { target: string; command?: string[]; birth?: WorkerProcess }[] = [];
  const control = async (args: string[], allow_nonzero = false) =>
    await io.command(['/bin/launchctl', ...args], execution(), 30, allow_nonzero);
  const json = async (path: string) =>
    JSON.parse(await readFile(path, 'utf8')) as Record<string, any>;
  const policy = join(root, 'data/probe-policy.json'),
    attempt_path = join(root, 'data/update-attempt.json');
  const identity = async () => {
    const listener = await inspector.discover(port, root),
      snapshot = await primitives.snapshot(performance.now() / 1000 + 5);
    const app = snapshot.get(listener.pid),
      supervisor = app && snapshot.get(app.parent);
    assert(app && supervisor);
    births.push(app, supervisor);
    return { app, supervisor };
  };
  const cleanup = async (item: (typeof planned)[number], intervention = false) => {
    const observed = await control(['print', item.target], true);
    if (observed.code !== 0) {
      assert(observed.stderr.includes('Could not find service'));
      return;
    }
    assert(item.command, 'An updater job exists before its prepared command is known');
    const path = join(root, 'data/update-agent/installer.plist'),
      job = await read_home_job(path, io, execution());
    assert.equal(job.Label, item.target.slice(domain.length + 1));
    assert.deepEqual(job.ProgramArguments, item.command);
    authenticate_loaded_job(observed.stdout, path, job, ['HOME', 'PATH'], realpathSync);
    await control(['bootout', item.target]);
    const after = await control(['print', item.target], true);
    assert(after.code !== 0 && after.stderr.includes('Could not find service'));
    if (item.birth)
      await wait(async () =>
        (await primitives.is_absent(item.birth!, performance.now() / 1000 + 5)) ? true : undefined,
      );
    if (intervention) record.cleanup_intervention = true;
  };
  const phases: Record<string, unknown>[] = [];
  record.updates = phases;
  try {
    for (const [name, candidate] of [
      ['success', builds.b],
      ['rollback', builds.a],
    ] as const) {
      record.update_phase = name;
      const started = performance.now(),
        old = await identity(),
        attempt = 'native-' + name;
      const request_dir = join(root, 'data', 'update-installer-' + attempt);
      const retained = release_path(
        root,
        name === 'success' ? builds.a.manifest.app_sha : builds.b.manifest.app_sha,
      );
      const item: { target: string; command?: string[]; birth?: WorkerProcess } = {
        target: `${domain}/${label}.update-${attempt}`,
        command: [
          join(retained, 'node/bin/node'),
          join(retained, 'server/environments/updates/installer/main.ts'),
          join(request_dir, 'request.json'),
        ],
      };
      planned.push(item);
      let trial_birth: WorkerProcess | undefined;
      const staged = join(root, '.app-next-' + candidate.manifest.app_sha);
      await extract_app_archive(
        candidate.archive,
        staged,
        candidate.archive_sha256,
        candidate.manifest.app_sha,
        execution().child_processes,
        undefined,
        execution(),
        MAC_HOME,
      );
      await admit_runtime(staged, candidate.manifest, execution().child_processes);
      await writeFile(
        policy,
        JSON.stringify(
          name === 'success'
            ? { hold_sha: candidate.manifest.app_sha }
            : { fail_sha: candidate.manifest.app_sha },
        ),
      );
      await writeFile(
        join(root, 'data/probe-request.json'),
        JSON.stringify({
          attempt_id: attempt,
          manifest: candidate.manifest,
          archive_sha256: candidate.archive_sha256,
        }),
      );
      const response = await fetch(`http://127.0.0.1:${port}/native-update`, {
        method: 'POST',
        signal: AbortSignal.timeout(30_000),
      }).catch(() => null);
      if (response && response.status !== 200)
        throw new Error('Mini APP refused update: ' + (await response.text()));
      const launch = await json(join(root, 'data/probe-launch-' + attempt + '.json'));
      assert.deepEqual(launch.command, item.command);
      assert.equal(launch.app_pid, old.app.pid);
      assert.equal(launch.supervisor_pid, old.supervisor.pid);
      const check_launch = async () => {
        const failure = await readFile(
          join(root, 'logs/probe-launch-error-' + attempt + '.log'),
          'utf8',
        ).catch((error: NodeJS.ErrnoException) => {
          if (error.code !== 'ENOENT') throw error;
          return null;
        });
        if (failure) throw new Error(failure);
      };
      record.update_observation = 'updater process';
      item.birth = await wait(async () => {
        await check_launch();
        const value = await control(['print', item.target], true);
        if (value.code !== 0) {
          if (value.stderr.includes('Could not find service')) return undefined;
          throw new Error('Cannot observe the native updater: ' + value.stderr);
        }
        const path = join(root, 'data/update-agent/installer.plist'),
          job = await read_home_job(path, io, execution());
        assert.deepEqual(job.ProgramArguments, item.command);
        const pid = authenticate_loaded_job(
          value.stdout,
          path,
          job,
          ['HOME', 'PATH'],
          realpathSync,
        );
        if (pid === null) return undefined;
        const snapshot = await primitives.snapshot(performance.now() / 1000 + 5),
          birth = snapshot.get(pid);
        if (birth) {
          assert.notEqual(birth.group, old.app.group);
          births.push(birth);
          return birth;
        }
        return undefined;
      });
      record.update_observation = 'app swap';
      await wait(async () => {
        await check_launch();
        const state = await json(attempt_path);
        return state.steps?.app_swapped ? true : undefined;
      });
      if (name === 'success') {
        assert.equal(
          realpathSync(join(root, 'app')),
          release_path(root, candidate.manifest.app_sha),
        );
        assert((await lstat(retained)).isDirectory());
        await writeFile(policy, '{}');
      } else {
        record.update_observation = 'failed candidate health';
        await wait(async () => {
          try {
            const response = await fetch(`http://127.0.0.1:${port}/api/health`, {
              signal: AbortSignal.timeout(1000),
            });
            const value = (await response.json()) as any;
            return response.status === 503 &&
              value.app_sha === candidate.manifest.app_sha &&
              value.project_name === 'candidate-write'
              ? true
              : undefined;
          } catch {
            return undefined;
          }
        });
      }
      record.update_observation = 'candidate process';
      if (name === 'rollback') trial_birth = (await identity()).app;
      record.update_observation = 'terminal attempt';
      const state = await wait(async () => {
        const value = await json(attempt_path);
        return ['done', 'failed'].includes(value.state) ? value : undefined;
      }, 90);
      record.update_observation = 'restoration assertions';
      assert.equal(state.state, name === 'success' ? 'done' : 'failed');
      assert(
        await lstat(join(root, 'data/update-barrier.json')).then(
          () => false,
          (error: NodeJS.ErrnoException) => {
            if (error.code === 'ENOENT') return true;
            throw error;
          },
        ),
      );
      assert(name === 'success' ? state.steps.healthy : state.steps.rollback_database_restored);
      await wait(async () =>
        (await primitives.is_absent(old.app, performance.now() / 1000 + 5)) ? true : undefined,
      );
      if (trial_birth)
        await wait(async () =>
          (await primitives.is_absent(trial_birth!, performance.now() / 1000 + 5))
            ? true
            : undefined,
        );
      const health = await fetch(`http://127.0.0.1:${port}/api/health`, {
        signal: AbortSignal.timeout(1000),
      });
      const value = (await health.json()) as any;
      assert.equal(health.status, 200);
      assert.equal(value.app_sha, builds.b.manifest.app_sha);
      assert.equal(value.project_name, 'sentinel');
      const current = await identity();
      assert.notEqual(current.app.birth, old.app.birth);
      for (const suffix of ['', '.backup', '.maintenance', '.stall-watchdog'])
        assert.equal((await control(['print', `${domain}/${label}${suffix}`], true)).code, 0);
      record.update_observation = 'updater exit';
      await wait(
        async () =>
          (await primitives.is_absent(item.birth!, performance.now() / 1000 + 5))
            ? true
            : undefined,
        30,
      );
      await cleanup(item);
      phases.push({
        name,
        seconds: (performance.now() - started) / 1000,
        attempt: state,
        updater: item.birth,
        old_app: old.app,
        candidate_app: trial_birth,
        new_app: current.app,
        retained_command: item.command,
      });
    }
  } finally {
    for (const item of [...planned].reverse()) await cleanup(item, true);
  }
}
