import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer as http_server } from 'node:http';
import { shell_quote } from '../conversation/worker_unit/logic.ts';
import {
  existsSync,
  linkSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, test, vi } from 'vitest';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { healthy } from '../environments/updates/installer/health.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { install_home, check_home_port } from './home.ts';
import { crash_restart_jobs, home_install_fixture as fixture } from './testing/home_fixture.ts';
import type { HomeInstallDependencies, HomeServicePlan } from './contracts.ts';
import { HomeCleanupUnsettled } from './contracts.ts';
import { write_home_job } from '../environments/updates/darwin/jobs.ts';
import {
  create_darwin_home_io,
  DarwinHomeService,
} from '../environments/updates/darwin/service.ts';
import { create_darwin_home_service } from './darwin.ts';
import { create_linux_home_service } from './linux.ts';
import { initialize_candidate_home_database } from './database.ts';
import { require_application_schema } from '../core/database/admission.ts';
import { createReadAccess } from '../core/database/index.ts';

// File-mode fixtures and assertions choose their own mask, independent of the runner.
let inherited_mask: number;
beforeEach(() => {
  inherited_mask = process.umask(0o022);
});
afterEach(() => {
  process.umask(inherited_mask);
});

test('Mac job conversion refuses pre-existing and late hard links before either file write', async () => {
  const f = await fixture(),
    path = join(f.account, 'installer.plist');
  const job = {
    Label: 'com.panels.test',
    ProgramArguments: ['/unused/node'],
    WorkingDirectory: f.account,
    EnvironmentVariables: {},
  };
  let converted = false;
  const io = create_darwin_home_io(f.deps.execution, f.deps.environment, {
    command: async () => {
      converted = true;
      linkSync(path, join(f.account, 'late-link'));
      return { code: 0, stdout: '<plist/>', stderr: '' };
    },
  });
  try {
    linkSync(f.foreign, path);
    await assert.rejects(write_home_job(path, job, io, readFileSync(f.foreign)), /no longer owns/);
    assert.equal(converted, false);
    assert.equal(readFileSync(f.foreign, 'utf8'), 'existing account data');
    rmSync(path);
    await assert.rejects(write_home_job(path, job, io), /replacement changed/);
    assert.equal(converted, true);
    assert.equal(readFileSync(join(f.account, 'late-link'), 'utf8'), JSON.stringify(job) + '\n');
    const installed_plist = join(f.request.root, 'agents/home.plist'),
      alias = join(f.account, 'installation-alias');
    await assert.rejects(
      install_home(f.request, {
        ...f.deps,
        service: async () => ({
          ...f.service,
          links: [],
          files: [
            {
              path: installed_plist,
              content: JSON.stringify(job) + '\n',
              mode: 0o600,
              format: 'plist-json',
            },
          ],
        }),
        command: async () => {
          linkSync(installed_plist, alias);
          return { code: 0, stdout: '<converted-plist/>', stderr: '' };
        },
      }),
      /Retained/,
    );
    assert.equal(readFileSync(alias, 'utf8'), JSON.stringify(job) + '\n');
    assert(existsSync(f.request.root));
  } finally {
    await f.close();
  }
});

test('Mac rollback boots out the authenticated failed candidate while launchd has no supervisor PID', async () => {
  const f = await fixture();
  try {
    const { root, service: label } = f.request;
    mkdirSync(join(root, 'app/node/bin'), { recursive: true });
    writeFileSync(join(root, 'app/node/bin/node'), '', { mode: 0o700 });
    const plan = await create_darwin_home_service(f.request, f.deps, false);
    for (const file of plan.files) {
      mkdirSync(dirname(file.path), { recursive: true });
      writeFileSync(file.path, file.content, { mode: file.mode });
    }
    const target = `gui/${f.deps.uid}/${label}`;
    const plist = join(root, 'agents', label + '.plist');
    const program = join(root, 'app/bin/panels');
    let loaded = true,
      foreign = false,
      bootouts = 0;
    const ok = { code: 0, stdout: '', stderr: '' };
    const io = create_darwin_home_io(f.deps.execution, f.deps.environment, {
      processes: () => new Map(),
      process: () => {
        throw new Error('This launchd job has no process');
      },
      async command(argv) {
        if (argv[0] === '/usr/bin/plutil')
          return { ...ok, stdout: readFileSync(argv.at(-1)!, 'utf8') };
        if (argv[1] === 'print') {
          if (!loaded || argv[2] !== target)
            return { code: 113, stdout: '', stderr: 'Could not find service' };
          const fields = [
            `path = ${plist}`,
            `program = ${foreign ? '/foreign' : program}`,
            `working directory = ${root}`,
            'arguments = {',
            program,
            'serve',
            '}',
            'environment = {',
            `PLAN_DB_PATH => ${root}/data/planner.db`,
            `PLAN_CONFIG_PATH => ${root}/data/config.yaml`,
            '}',
          ];
          return { ...ok, stdout: fields.join('\n') };
        }
        if (argv[1] === 'bootstrap') {
          assert.equal(argv[3], plist);
          loaded = true;
          return ok;
        }
        assert.deepEqual(argv, ['/bin/launchctl', 'bootout', target]);
        bootouts++;
        loaded = false;
        return ok;
      },
    });
    const home = new DarwinHomeService(root, target, io);
    await assert.rejects(home.validate_running_home(), /no running supervisor/);
    await assert.rejects(home.stop(), /no running supervisor/);
    loaded = false;
    await home.start();
    foreign = true;
    await assert.rejects(home.stop(), /different program/);
    assert.equal(bootouts, 0);
    foreign = false;
    await home.stop();
    assert.equal(bootouts, 1);
    assert.equal(loaded, false);
  } finally {
    await f.close();
  }
});

// Distinct gap: a scheduled job's own process churn during stop failed release PR #1326's update.
test('Mac stop captures again when the stall watchdog starts during it, but not when the home changes', async () => {
  const f = await fixture();
  try {
    const { root, service: label } = f.request;
    mkdirSync(join(root, 'app/node/bin'), { recursive: true });
    writeFileSync(join(root, 'app/node/bin/node'), '', { mode: 0o700 });
    // A Darwin host plans the full auxiliary environment that launchd reports back.
    const plan = await create_darwin_home_service(
      f.request,
      { ...f.deps, system: 'darwin' },
      false,
    );
    for (const file of plan.files) {
      mkdirSync(dirname(file.path), { recursive: true });
      writeFileSync(file.path, file.content, { mode: file.mode });
    }
    const domain = `gui/${f.deps.uid}`,
      watchdog = label + '.stall-watchdog',
      maintenance = label + '.maintenance';
    const uid = process.getuid!(),
      home_process = { pid: 100, parent: 1, birth: 'home', uid },
      watchdog_process = { pid: 200, parent: 1, birth: 'watchdog', uid };
    const loaded = new Set([label]),
      running = new Map([[100, home_process]]),
      pids = new Map([[label, 100]]),
      bootouts: string[] = [],
      // A booted-out job that launchd still lists until its next read.
      tearing_down = new Set<string>();
    let snapshots = 0,
      watchdog_reads = 0,
      start_watchdog_on_read = 0;
    const ok = { code: 0, stdout: '', stderr: '' };
    const overrides = {
      // The first inventory misses whatever the watchdog starts after it.
      processes: () => (snapshots++ === 0 ? new Map([[100, home_process]]) : new Map(running)),
      process: (pid: number) => running.get(pid) ?? null,
      async command(argv: readonly string[]) {
        if (argv[0] === '/usr/bin/plutil')
          return { ...ok, stdout: readFileSync(argv.at(-1)!, 'utf8') };
        const job_label = argv[2]!.slice(domain.length + 1);
        if (argv[1] === 'print') {
          if (job_label === watchdog && ++watchdog_reads === start_watchdog_on_read) {
            running.set(201, { ...watchdog_process, pid: 201, birth: 'second watchdog' });
            pids.set(watchdog, 201);
          }
          if (tearing_down.delete(job_label)) loaded.delete(job_label);
          else if (!loaded.has(job_label))
            return { code: 113, stdout: '', stderr: 'Could not find service' };
          const plist = join(root, 'agents', job_label + '.plist'),
            job = JSON.parse(readFileSync(plist, 'utf8')),
            pid = pids.get(job_label);
          return {
            ...ok,
            stdout: [
              `path = ${plist}`,
              `program = ${job.ProgramArguments[0]}`,
              `working directory = ${job.WorkingDirectory}`,
              'arguments = {',
              ...job.ProgramArguments,
              '}',
              'environment = {',
              ...Object.entries(job.EnvironmentVariables).map(
                ([key, value]) => `${key} => ${value}`,
              ),
              '}',
              ...(pid === undefined ? [] : [`pid = ${pid}`]),
            ].join('\n'),
          };
        }
        assert.equal(argv[1], 'bootout');
        bootouts.push(job_label);
        if (job_label === maintenance) tearing_down.add(job_label);
        else loaded.delete(job_label);
        running.delete(pids.get(job_label)!);
        pids.delete(job_label);
        return ok;
      },
    };
    // The home process stays strict: a home PID outside the inventory refuses before any bootout.
    const strict = create_darwin_home_io(f.deps.execution, f.deps.environment, {
      ...overrides,
      processes: () => new Map(),
    });
    await assert.rejects(
      new DarwinHomeService(root, `${domain}/${label}`, strict).stop(),
      /Loaded Mac job process identity is unavailable/,
    );
    assert.deepEqual(bootouts, []);
    // The watchdog started after the first inventory. The stop captures again and completes.
    loaded.add(watchdog);
    running.set(200, watchdog_process);
    pids.set(watchdog, 200);
    const io = create_darwin_home_io(f.deps.execution, f.deps.environment, overrides);
    await new DarwinHomeService(root, `${domain}/${label}`, io).stop();
    assert.equal(snapshots, 2);
    assert.deepEqual(bootouts, [watchdog, label]);
    assert.equal(loaded.size, 0);
    // A watchdog starts between its first read and its read before bootout, after maintenance
    // was already booted out. The next pass does not boot maintenance out a second time.
    bootouts.length = 0;
    snapshots = 1;
    for (const job of [label, maintenance, watchdog]) loaded.add(job);
    running.set(100, home_process);
    pids.set(label, 100);
    start_watchdog_on_read = watchdog_reads + 2;
    await new DarwinHomeService(root, `${domain}/${label}`, io).stop();
    assert.deepEqual(bootouts, [maintenance, watchdog, label]);
    assert.equal(snapshots, 3);
    assert.equal(loaded.size, 0);
  } finally {
    await f.close();
  }
});

test('fresh database initialization closes its child before activation and failed install cleanup retains foreign data', async () => {
  const f = await fixture();
  const candidate = fileURLToPath(new URL('../../', import.meta.url)),
    path = join(f.base, 'fresh.sqlite'),
    link = join(f.base, 'dangling.sqlite'),
    absent = join(f.base, 'must-not-be-created.sqlite');
  const spawn = f.owner.spawn.bind(f.owner);
  let spawns = 0,
    closes = 0;
  f.owner.spawn = ((...args: Parameters<ChildProcesses['spawn']>) => {
    const child = spawn(...args);
    spawns++;
    child.once('close', () => {
      closes++;
    });
    return child;
  }) as ChildProcesses['spawn'];
  const began = performance.now();
  try {
    const machine = join(f.base, 'machine-data');
    mkdirSync(machine);
    await initialize_candidate_home_database(
      candidate,
      path,
      f.deps.execution,
      process.execPath,
      machine,
      'Asia/Tokyo',
    );
    const identity = JSON.parse(readFileSync(join(machine, 'identity.json'), 'utf8'));
    const credential_file = join(machine, 'machine-credential.json');
    const credential = JSON.parse(readFileSync(credential_file, 'utf8')).credential;
    assert.equal(statSync(credential_file).mode & 0o777, 0o600);
    assert.match(credential, /^[A-Za-z0-9_-]{43}$/);
    assert.equal(spawns, 1);
    assert.equal(closes, 1);
    require_application_schema(path);
    const reader = createReadAccess(path);
    try {
      reader.read((sql) => {
        assert.equal(
          sql.get<{ owner_time_zone: string }>(
            'SELECT owner_time_zone FROM attention_history_metadata',
          )?.owner_time_zone,
          'Asia/Tokyo',
        );
        // A fresh home starts with no projects.
        assert.deepEqual(sql.all('SELECT name FROM projects'), []);
        assert(
          (sql.get<{ count: number }>('SELECT count(*) AS count FROM managed_skills')?.count ?? 0) >
            0,
        );
        assert(
          (sql.get<{ count: number }>('SELECT count(*) AS count FROM worker_types')?.count ?? 0) >
            0,
        );
        assert(sql.get('SELECT 1 FROM chief_page WHERE singleton=1'));
        const registered = sql.get<{ credential_sha256: string; is_home: number }>(
          'SELECT credential_sha256,is_home FROM machine_executor_credentials JOIN machines USING(machine_id) WHERE machine_id=?',
          [identity.machine_id],
        );
        assert.equal(
          registered?.credential_sha256,
          createHash('sha256').update(credential).digest('hex'),
        );
        // The first machine is the server's own machine, with the home identity.
        assert.equal(identity.machine_id, HOME_MACHINE_ID);
        assert.equal(registered?.is_home, 1);
      });
    } finally {
      reader.close();
    }
    const bytes = readFileSync(path);
    await assert.rejects(
      initialize_candidate_home_database(candidate, path, f.deps.execution, process.execPath),
      /already exists/,
    );
    assert.deepEqual(readFileSync(path), bytes);
    symlinkSync(absent, link);
    await assert.rejects(
      initialize_candidate_home_database(candidate, link, f.deps.execution, process.execPath),
      /already exists/,
    );
    assert.equal(readlinkSync(link), absent);
    assert(!existsSync(absent));
    assert.equal(spawns, 3);
    assert.equal(closes, 3);
    console.log(
      JSON.stringify({
        proof: 'candidate fresh database seed/bootstrap and exclusive refusal',
        spawns,
        closes,
        seconds: (performance.now() - began) / 1000,
      }),
    );
    await assert.rejects(
      install_home(f.request, {
        ...f.deps,
        healthy: async () => {
          f.controller.abort(new Error('operator interrupt'));
          f.owner.seal();
          return false;
        },
      }),
      /did not report ready/,
    );
    assert.deepEqual(f.events, ['initialize', 'start', 'stop']);
    assert(!existsSync(f.request.root));
    assert(!existsSync(join(f.base, 'new-parent')));
    assert(!existsSync(f.unit));
    // The command link and the account folders made for it leave with the rest.
    assert(!existsSync(join(f.account, '.local')));
    assert.equal(readFileSync(f.foreign, 'utf8'), 'existing account data');
  } finally {
    await f.close();
  }
});

test('replacement root and changed service files retain their trees before any unlink', async () => {
  const f = await fixture();
  try {
    const deps = {
      ...f.deps,
      healthy: async () => {
        renameSync(f.request.root, f.request.root + '-original');
        mkdirSync(f.request.root);
        writeFileSync(join(f.request.root, 'foreign'), 'replacement root');
        return false;
      },
    };
    await assert.rejects(install_home(f.request, deps), /Retained/);
    assert.equal(readFileSync(join(f.request.root, 'foreign'), 'utf8'), 'replacement root');
    assert(existsSync(join(f.request.root + '-original', 'app/sealed')));
    assert(existsSync(f.unit));
  } finally {
    await f.close();
  }
  const changed = await fixture();
  try {
    await assert.rejects(
      install_home(changed.request, {
        ...changed.deps,
        healthy: async () => {
          writeFileSync(changed.unit, 'foreign unit');
          return false;
        },
      }),
      /Retained/,
    );
    assert(existsSync(join(changed.request.root, 'app/sealed')));
    assert.equal(readFileSync(changed.unit, 'utf8'), 'foreign unit');
  } finally {
    await changed.close();
  }
  const darwin = await fixture(),
    controls: string[][] = [];
  let loaded: string | undefined,
    plan: HomeServicePlan | undefined,
    foreign = true,
    stopping = false,
    persistent = false,
    expired = false,
    polls = 0;
  try {
    await assert.rejects(
      install_home(darwin.request, {
        ...darwin.deps,
        system: 'darwin',
        architecture: 'arm64',
        service: async (request, host) =>
          (plan = await create_darwin_home_service(request, host, true, crash_restart_jobs)),
        async command(argv) {
          if (argv[0] === '/usr/bin/plutil')
            return { code: 0, stdout: readFileSync(argv.at(-1)!, 'utf8'), stderr: '' };
          if (argv[1] === 'print') {
            if (loaded && stopping) {
              polls++;
              if (persistent) expired = true;
              else if (polls > 1) loaded = undefined;
            }
            if (!loaded) return { code: 1, stdout: '', stderr: 'Could not find service' };
            const job = JSON.parse(readFileSync(loaded, 'utf8'));
            return {
              code: 0,
              stdout: foreign
                ? `path = ${loaded}\nprogram = /foreign/idle-program\n`
                : `path = ${loaded}\nprogram = ${job.ProgramArguments[0]}\nworking directory = ${job.WorkingDirectory}\narguments = {\n${job.ProgramArguments.join('\n')}\n}\nenvironment = {\n${Object.entries(
                    job.EnvironmentVariables,
                  )
                    .map(([key, value]) => `${key} => ${value}`)
                    .join('\n')}\n}\n`,
              stderr: '',
            };
          }
          controls.push([...argv]);
          if (argv[1] === 'bootstrap') loaded = argv.at(-1)!;
          if (argv[1] === 'bootout') stopping = true;
          return { code: 0, stdout: '', stderr: '' };
        },
      }),
      /Retained/,
    );
    assert(loaded);
    assert(!controls.some((argv) => argv[1] === 'bootout'));
    assert(existsSync(join(darwin.request.root, 'app/sealed')));
    assert(existsSync(loaded));
    const path = loaded,
      bytes = readFileSync(path);
    assert.deepEqual(JSON.parse(bytes.toString()).KeepAlive, { SuccessfulExit: false });
    const assert_owned = async (current: string) => {
      assert.equal(current, path);
      assert.deepEqual(readFileSync(current), bytes);
    };
    foreign = false;
    controls.length = 0;
    await plan!.stop(darwin.deps.execution, assert_owned);
    assert.equal(polls, 2);
    assert.equal(controls.filter((argv) => argv[1] === 'bootout').length, 1);
    assert(existsSync(path));
    loaded = path;
    stopping = false;
    persistent = true;
    controls.length = 0;
    const actual_now = performance.now.bind(performance);
    const clock = vi
      .spyOn(performance, 'now')
      .mockImplementation(() => actual_now() + (expired ? 1000 : 0));
    try {
      await assert.rejects(
        plan!.stop({ ...darwin.deps.execution, deadline: actual_now() / 1000 + 0.5 }, assert_owned),
        /time budget/,
      );
      assert.equal(controls.filter((argv) => argv[1] === 'bootout').length, 1);
      assert(existsSync(path));
    } finally {
      clock.mockRestore();
    }
  } finally {
    await darwin.close();
  }
});

test('a Linux home installs every scheduled job, writes the panels command, and keeps the update token out of its units', async () => {
  const f = await fixture(),
    commands: string[] = [];
  try {
    const root = join(realpathSync(f.base), 'new-parent', 'home'),
      unit_directory = join(f.account, '.config/systemd/user'),
      command_link = join(f.account, '.local/bin/panels');
    const deps: HomeInstallDependencies = {
      ...f.deps,
      service: create_linux_home_service,
      healthy: async () => true,
      async command(argv) {
        commands.push(argv.join(' '));
        const stdout = argv.includes('--property=Linger')
          ? 'yes\n'
          : argv.includes('--property=LoadState')
            ? 'not-found\n'
            : '';
        return { code: 0, stdout, stderr: '' };
      },
    };
    assert.equal(await install_home(f.request, deps), `http://127.0.0.1:${f.request.port}`);
    const units = Object.fromEntries(
      readdirSync(unit_directory).map((name) => [
        name,
        readFileSync(join(unit_directory, name), 'utf8'),
      ]),
    );
    assert.deepEqual(Object.keys(units).sort(), [
      'com.panels.test.backup.service',
      'com.panels.test.backup.timer',
      'com.panels.test.machine.service',
      'com.panels.test.maintenance.service',
      'com.panels.test.maintenance.timer',
      'com.panels.test.service',
      'com.panels.test.stall-watchdog.service',
      'com.panels.test.stall-watchdog.timer',
    ]);
    for (const timer of ['backup', 'maintenance', 'stall-watchdog'])
      assert(commands.includes(`systemctl --user enable --now com.panels.test.${timer}.timer`));
    assert.match(units['com.panels.test.service']!, /^Restart=always$/m);
    assert.match(units['com.panels.test.service']!, /^Delegate=yes$/m);
    const panels = JSON.stringify(join(root, 'app/bin/panels'));
    assert(
      units['com.panels.test.maintenance.service']!.includes(
        `\nExecStart=${panels} "environment" "cleanup" "--apply"\n`,
      ),
    );
    assert.match(units['com.panels.test.maintenance.timer']!, /^OnCalendar=\*-\*-\* 03:15:00$/m);
    assert(
      units['com.panels.test.stall-watchdog.service']!.includes(
        `\nExecStart=${panels} "stalls" "watchdog" "--db" `,
      ),
    );
    assert.match(units['com.panels.test.stall-watchdog.timer']!, /^OnUnitActiveSec=30s$/m);
    const token_file = join(root, 'data/secrets/update-token');
    for (const [name, content] of Object.entries(units)) {
      assert(!content.includes(f.request.update_token), name);
      if (name === 'com.panels.test.machine.service') {
        assert(!content.includes('PLAN_UPDATE_TOKEN_FILE'));
        assert(content.includes(`PLAN_EXECUTOR_ROOT=${join(root, 'machine/data')}`));
        assert(content.includes(join(root, 'machine/providers')));
        assert(!content.includes(join(root, 'app/agent_backends')));
      } else if (name.endsWith('.service')) {
        assert(!content.includes('CODEX_HOME'));
        assert(!content.includes('CLAUDE_CONFIG_DIR'));
        assert(content.includes('Environment="PLAN_UPDATE_REPOSITORY=khushal-jagota/headroom"'));
        assert(content.includes(`Environment="PLAN_UPDATE_TOKEN_FILE=${token_file}"`));
      }
    }
    assert.equal(readFileSync(token_file, 'utf8'), f.request.update_token + '\n');
    assert.equal(statSync(token_file).mode & 0o777, 0o600);
    assert.equal(statSync(dirname(token_file)).mode & 0o777, 0o700);
    const wrapper = `#!/bin/sh\nexec ${shell_quote(join(root, 'app/bin/panels'))} "$@"\n`;
    assert.equal(readFileSync(command_link, 'utf8'), wrapper);
    assert.equal(statSync(command_link).mode & 0o777, 0o755 & ~process.umask());
    // A second home refuses the existing command before it writes anything.
    const second = { ...f.request, root: join(f.base, 'second'), service: 'com.panels.other' };
    await assert.rejects(
      install_home(second, { ...deps, service: async () => f.service }),
      /panels command already exists/,
    );
    assert(!existsSync(second.root));
    assert.equal(readFileSync(command_link, 'utf8'), wrapper);
  } finally {
    await f.close();
  }
});

test('a test home preserves the account command on success and first-machine rollback', async () => {
  // Normal install coverage refuses a shared command. It does not prove the explicit
  // opt-out leaves that command outside successful installation and failed cleanup.
  for (const machine_failure of [false, true]) {
    const f = await fixture();
    try {
      const command = join(f.account, '.local/bin/panels');
      mkdirSync(dirname(command), { recursive: true });
      writeFileSync(command, 'existing live command\n', { mode: 0o755 });
      const before = statSync(command);
      const install = install_home(
        { ...f.request, skip_command_link: true },
        {
          ...f.deps,
          healthy: async () => true,
          machine_healthy: async () => {
            if (machine_failure) throw new Error('test machine refused');
          },
        },
      );
      if (machine_failure) {
        await assert.rejects(install, /test machine refused/);
        assert(!existsSync(f.request.root));
      } else {
        assert.equal(await install, `http://127.0.0.1:${f.request.port}`);
        assert(existsSync(f.request.root));
      }
      assert.equal(readFileSync(command, 'utf8'), 'existing live command\n');
      const after = statSync(command);
      assert.equal(after.ino, before.ino);
      assert.equal(after.mode, before.mode);
      assert.equal(after.mtimeMs, before.mtimeMs);
    } finally {
      await f.close();
    }
  }
});

test('a refused first-machine connection rolls back the complete new installation', async () => {
  const f = await fixture();
  try {
    await assert.rejects(
      install_home(f.request, {
        ...f.deps,
        healthy: async () => true,
        machine_healthy: async () => {
          throw new Error('first machine refused');
        },
      }),
      /first machine refused/,
    );
    assert.deepEqual(f.events, ['initialize', 'start', 'machine', 'stop']);
    assert(!existsSync(f.request.root));
    assert(!existsSync(f.unit));
    assert(!existsSync(join(f.account, '.local/bin/panels')));
    assert.equal(readFileSync(f.foreign, 'utf8'), 'existing account data');
  } finally {
    await f.close();
  }
});

test('an expired cleanup observer retains descriptor custody through the actual delayed stop', async () => {
  const f = await fixture(),
    cleanup_abort = new AbortController();
  const original_timeout = AbortSignal.timeout;
  const timeout = vi
    .spyOn(AbortSignal, 'timeout')
    .mockImplementation((milliseconds) =>
      milliseconds === 120000 ? cleanup_abort.signal : original_timeout(milliseconds),
    );
  const admitted = Promise.withResolvers<void>(),
    hold = Promise.withResolvers<void>();
  let checked_after_observer = false;
  const service = {
    ...f.service,
    async stop(_scope: unknown, assert_owned: (path: string) => Promise<void>) {
      admitted.resolve();
      await hold.promise;
      await assert_owned(f.unit);
      checked_after_observer = true;
    },
  };
  let retained: HomeCleanupUnsettled | undefined;
  try {
    const result = install_home(f.request, { ...f.deps, service: async () => service });
    const rejected = assert.rejects(result, (error: unknown) => {
      assert(error instanceof HomeCleanupUnsettled);
      retained = error;
      return true;
    });
    await admitted.promise;
    cleanup_abort.abort(new Error('scripted cleanup deadline'));
    await rejected;
    assert(existsSync(join(f.request.root, 'app/sealed')));
    hold.resolve();
    await assert.rejects(retained!.settled, /scripted cleanup deadline/);
    assert(checked_after_observer);
    assert(existsSync(f.unit));
  } finally {
    hold.resolve();
    timeout.mockRestore();
    await f.close();
  }
});

test('shared health requires exact ready identity for homes while retaining the update default', async () => {
  // The real HTTP boundary proves ready is read from decoded health, not merely a mock return.
  const started = performance.now();
  let body: unknown = { app_sha: 'c'.repeat(40), ready: true };
  const actors: (string | string[] | undefined)[] = [];
  const server = http_server(async (request, response) => {
    actors.push(request.headers['x-plan-actor']);
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify(body));
  });
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
  try {
    const address = server.address();
    assert(address && typeof address !== 'string');
    const url = `http://127.0.0.1:${address.port}/api/health`,
      sha = 'b'.repeat(40),
      signal = new AbortController().signal;
    await assert.rejects(check_home_port(address.port), /busy/);
    assert.equal(await healthy(url, sha, 0, signal, {}, true), false);
    body = { app_sha: sha, ready: false };
    assert.equal(await healthy(url, sha, 0, signal, {}, true), false);
    assert.equal(await healthy(url, sha, 0, signal), true);
    body = { app_sha: sha, ready: true };
    assert.equal(await healthy(url, sha, 0, signal, {}, true), true);
    assert.equal(await healthy(url, sha, 0, signal, {}, true, 'unattributed'), true);
    assert.equal(actors.at(-1), 'unattributed');
    assert(actors.slice(0, -1).every((actor) => actor === undefined));
    body = { app_sha: sha, ready: true, prepared_attempt_id: 'previous' };
    assert.equal(await healthy(url, sha, 0, signal, {}, false, undefined, 'current'), false);
    body = { app_sha: sha, ready: true, prepared_attempt_id: 'current' };
    assert.equal(await healthy(url, sha, 0, signal, {}, false, undefined, 'current'), true);
  } finally {
    await new Promise<void>((done, fail) =>
      server.close((error) => (error ? fail(error) : done())),
    );
    console.log(
      `home-health-boundary seconds=${((performance.now() - started) / 1000).toFixed(3)}`,
    );
  }
});

test('a slowly streaming health response cannot extend the trial deadline', async () => {
  const server = http_server((_request, response) => {
    const tick = setInterval(() => response.write(' '), 10);
    response.once('close', () => clearInterval(tick));
  });
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done));
  try {
    const address = server.address();
    assert(address && typeof address !== 'string');
    assert.equal(
      await healthy(
        `http://127.0.0.1:${address.port}/api/health`,
        'a'.repeat(40),
        0.15,
        new AbortController().signal,
      ),
      false,
    );
  } finally {
    await new Promise<void>((done) => server.close(() => done()));
  }
}, 3000);
