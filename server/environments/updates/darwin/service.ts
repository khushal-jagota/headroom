/** A home stops only authenticated launchd jobs and waits for their captured native births. */
import { lstatSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { isDeepStrictEqual } from 'node:util';
import { setTimeout as pause } from 'node:timers/promises';
import {
  darwin_process_bytes,
  darwin_worker_process_snapshot,
} from '../../../executor/mac/platform.ts';
import { run_release_command, assert_release_execution } from '../../release/process.ts';
import type { ReleaseExecution } from '../../release/contracts.ts';
import { read_home_identity } from './identity.ts';
import {
  read_home_job,
  write_home_job,
  darwin_schedule_jobs,
  persistent_home_link,
  check_home_login_link,
  ensure_home_login_link,
} from './jobs.ts';
import { authenticate_loaded_job } from './logic/launchd.ts';
import {
  DARWIN_HOME_COMMAND_SECONDS,
  DARWIN_HOME_POLL_MILLISECONDS,
  DARWIN_HOME_SCHEDULE_SUFFIXES,
  DARWIN_HOME_AUXILIARY_ENVIRONMENT,
} from './configuration.ts';
import type {
  DarwinHomeIO,
  DarwinHomeIdentity,
  DarwinHomeJob,
  DarwinHomeProcess,
} from './contracts.ts';

export function create_darwin_home_io(
  execution: ReleaseExecution,
  environment: Readonly<NodeJS.ProcessEnv>,
  overrides: Partial<DarwinHomeIO> = {},
): DarwinHomeIO {
  const uid = process.getuid!(),
    account_home = realpathSync(environment.HOME || process.env.HOME!);
  const inspect = (pid: number): DarwinHomeProcess | null => {
    try {
      const bytes = darwin_process_bytes(pid, 3, 136);
      return {
        pid,
        uid: bytes.readUInt32LE(20),
        parent: bytes.readUInt32LE(16),
        birth: `${bytes.readBigUInt64LE(120)}:${bytes.readBigUInt64LE(128)}`,
      };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ESRCH') return null;
      throw error;
    }
  };
  return {
    execution,
    environment,
    uid,
    account_home,
    command: (argv, scope, timeout_seconds, allow_nonzero = false) =>
      run_release_command(
        argv,
        {
          timeout_seconds,
          allow_nonzero,
          environment: { HOME: account_home, PATH: '/opt/homebrew/bin:/usr/bin:/bin' },
        },
        scope,
      ),
    process: inspect,
    processes: (deadline) =>
      new Map(
        [...darwin_worker_process_snapshot(deadline)].map(([pid, value]) => [
          pid,
          { pid, parent: value.parent, birth: value.birth, uid },
        ]),
      ),
    ...overrides,
  };
}
export class DarwinHomeService {
  readonly identity: DarwinHomeIdentity;
  readonly io: DarwinHomeIO;
  readonly resume_labels = new Set<string>();
  stopped = false;
  constructor(root: string, service: string, io: DarwinHomeIO) {
    this.io = io;
    this.identity = read_home_identity(root, service, io.uid);
  }
  private scope(): ReleaseExecution & { deadline: number } {
    const until = Math.min(
      this.io.execution.deadline ?? Infinity,
      performance.now() / 1000 + DARWIN_HOME_COMMAND_SECONDS,
    );
    return {
      ...this.io.execution,
      deadline: until,
      signal: AbortSignal.any([
        this.io.execution.signal,
        AbortSignal.timeout(Math.max(1, Math.ceil((until - performance.now() / 1000) * 1000))),
      ]),
    };
  }
  private current(): void {
    const current = read_home_identity(
      this.identity.root,
      `${this.identity.domain}/${this.identity.label}`,
      this.io.uid,
    );
    if (!isDeepStrictEqual(current, this.identity))
      throw new Error('Mac home installation identity changed');
  }
  plist_path(label: string): string {
    if (
      ![
        this.identity.label,
        this.identity.label + '.backup',
        ...DARWIN_HOME_SCHEDULE_SUFFIXES.map((suffix) => this.identity.label + suffix),
      ].includes(label)
    )
      throw new Error('Mac job label differs from this installation');
    return join(this.identity.root, 'agents', label + '.plist');
  }
  async job(label: string, scope = this.scope()): Promise<string | null> {
    this.plist_path(label);
    const result = await this.io.command(
      ['/bin/launchctl', 'print', `${this.identity.domain}/${label}`],
      scope,
      Math.max(0, scope.deadline! - performance.now() / 1000),
      true,
    );
    if (result.code === 0) return result.stdout;
    if (result.stderr.includes('Could not find service')) return null;
    throw new Error(`Cannot inspect Mac job ${label}: ${result.stderr.trim()}`);
  }
  async plist(label: string, scope = this.scope()): Promise<DarwinHomeJob> {
    this.current();
    const value = await read_home_job(this.plist_path(label), this.io, scope),
      root = this.identity.root;
    const environment = value.EnvironmentVariables;
    if (Object.hasOwn(environment, 'PLAN_APP_SHA'))
      throw new Error('Mac home cannot pin a release through its environment');
    const auxiliary = DARWIN_HOME_SCHEDULE_SUFFIXES.some(
      (suffix) => label === this.identity.label + suffix,
    );
    if (auxiliary) {
      const home = await this.plist(this.identity.label, scope);
      const expected = darwin_schedule_jobs(
        root,
        this.identity.label,
        [join(root, 'app/bin/panels')],
        home.EnvironmentVariables,
      )[label]!;
      const { EnvironmentVariables: _actual_environment, ...body } = value;
      const { EnvironmentVariables: _expected_environment, ...binding } = expected;
      if (
        !isDeepStrictEqual(body, binding) ||
        DARWIN_HOME_AUXILIARY_ENVIRONMENT.some(
          (key) => environment[key] !== home.EnvironmentVariables[key],
        )
      )
        throw new Error('Mac auxiliary plist does not bind the expected installation');
    } else {
      const expected =
        label === this.identity.label
          ? [join(root, 'app/bin/panels'), 'serve']
          : [
              join(root, 'app/bin/panels'),
              'environment',
              'backup-current',
              '--source-db',
              join(root, 'data/planner.db'),
              '--backup-dir',
              join(root, 'data/backups'),
              '--current-app',
              join(root, 'app'),
            ];
      if (
        value.Label !== label ||
        !isDeepStrictEqual(value.ProgramArguments, expected) ||
        value.WorkingDirectory !== root ||
        environment.PLAN_DB_PATH !== join(root, 'data/planner.db') ||
        environment.PLAN_CONFIG_PATH !== join(root, 'data/config.yaml')
      )
        throw new Error('Mac service plist does not bind the expected installation');
    }
    return value;
  }
  async authenticate(
    label: string,
    required: boolean,
    scope = this.scope(),
  ): Promise<{ pid: number | null; text: string | null }> {
    const path = this.plist_path(label),
      optional = DARWIN_HOME_SCHEDULE_SUFFIXES.some(
        (suffix) => label === this.identity.label + suffix,
      );
    if (optional) {
      try {
        lstatSync(path);
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
        if ((await this.job(label, scope)) !== null)
          throw new Error('Loaded Mac auxiliary job has no installation plist');
        return { pid: null, text: null };
      }
    }
    const plist = await this.plist(label, scope),
      text = await this.job(label, scope);
    if (text === null) {
      if (required) throw new Error('The installed Mac home is not loaded');
      return { pid: null, text: null };
    }
    const pid = authenticate_loaded_job(
      text,
      path,
      plist,
      optional ? DARWIN_HOME_AUXILIARY_ENVIRONMENT : ['PLAN_DB_PATH', 'PLAN_CONFIG_PATH'],
      realpathSync,
    );
    if (required && pid === null)
      throw new Error('The installed Mac home has no running supervisor');
    return { pid, text };
  }
  async validate_running_home(parent?: number): Promise<void> {
    const { pid } = await this.authenticate(this.identity.label, true);
    const process = pid === null ? null : this.io.process(pid);
    if (!process || process.uid !== this.io.uid || (parent !== undefined && pid !== parent))
      throw new Error('Mac home supervisor identity differs from this process');
  }
  async stop(): Promise<void> {
    const scope = this.scope(),
      inventory = this.io.processes(scope.deadline),
      captured = new Map<number, DarwinHomeProcess>();
    const loaded: string[] = [];
    for (const label of [
      ...DARWIN_HOME_SCHEDULE_SUFFIXES.map((suffix) => this.identity.label + suffix),
      this.identity.label + '.backup',
      this.identity.label,
    ]) {
      const { pid, text } = await this.authenticate(
        label,
        label === this.identity.label && !this.stopped,
        scope,
      );
      if (text === null) continue;
      loaded.push(label);
      if (label !== this.identity.label) this.resume_labels.add(label);
      if (pid !== null) {
        const original = inventory.get(pid),
          current = this.io.process(pid);
        if (
          !original ||
          !current ||
          current.uid !== this.io.uid ||
          current.birth !== original.birth
        )
          throw new Error('Loaded Mac job process identity is unavailable');
        const pending = [pid],
          selected = new Set<number>();
        while (pending.length) {
          assert_release_execution(scope);
          const next = pending.pop()!;
          if (selected.has(next)) continue;
          selected.add(next);
          const info = inventory.get(next)!;
          captured.set(next, info);
          for (const child of inventory.values())
            if (child.parent === next) pending.push(child.pid);
        }
      }
    }
    for (const label of loaded) {
      // Rebind every label immediately before its control operation, including its current birth.
      const { pid } = await this.authenticate(label, false, scope);
      if (pid !== null && this.io.process(pid)?.birth !== captured.get(pid)?.birth)
        throw new Error('Mac job process identity changed before stop');
      await this.io.command(
        ['/bin/launchctl', 'bootout', `${this.identity.domain}/${label}`],
        scope,
        Math.max(0, scope.deadline - performance.now() / 1000),
      );
    }
    for (;;) {
      assert_release_execution(scope);
      const jobs_gone = (await Promise.all(loaded.map((label) => this.job(label, scope)))).every(
        (text) => text === null,
      );
      const processes_gone = [...captured].every(
        ([pid, original]) => this.io.process(pid)?.birth !== original.birth,
      );
      if (jobs_gone && processes_gone) {
        this.stopped = true;
        return;
      }
      await pause(DARWIN_HOME_POLL_MILLISECONDS, undefined, { signal: scope.signal });
    }
  }
  async start(): Promise<void> {
    const scope = this.scope();
    await this.plist(this.identity.label, scope);
    if ((await this.job(this.identity.label, scope)) !== null)
      throw new Error('Refuse to replace an already loaded Mac job');
    await this.io.command(
      ['/bin/launchctl', 'bootstrap', this.identity.domain, this.identity.home_plist],
      scope,
      DARWIN_HOME_COMMAND_SECONDS,
    );
    this.stopped = false;
  }
  async provision_schedules(): Promise<void> {
    const home = await this.plist(this.identity.label),
      jobs = darwin_schedule_jobs(
        this.identity.root,
        this.identity.label,
        [join(this.identity.root, 'app/bin/panels')],
        home.EnvironmentVariables,
      );
    const persistent = await persistent_home_link(
      this.io.account_home,
      this.identity.label,
      this.identity.home_plist,
    );
    for (const label of Object.keys(jobs)) {
      await this.authenticate(label, false);
      if (persistent)
        await check_home_login_link(this.io.account_home, label, this.plist_path(label));
    }
    for (const [label, job] of Object.entries(jobs)) {
      let exists = true;
      try {
        lstatSync(this.plist_path(label));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
        exists = false;
      }
      if (!exists) {
        await write_home_job(this.plist_path(label), job, this.io);
        this.resume_labels.add(label);
      }
      if (persistent)
        await ensure_home_login_link(
          this.io.account_home,
          label,
          this.plist_path(label),
          this.io.execution,
        );
    }
  }
  async resume_auxiliaries(): Promise<void> {
    const errors: string[] = [];
    for (const label of [...this.resume_labels].sort()) {
      try {
        const { text } = await this.authenticate(label, false);
        if (text === null)
          await this.io.command(
            ['/bin/launchctl', 'bootstrap', this.identity.domain, this.plist_path(label)],
            this.scope(),
            DARWIN_HOME_COMMAND_SECONDS,
          );
      } catch (error) {
        errors.push(`${label}: ${String(error)}`);
      }
    }
    if (errors.length) throw new Error(errors.join('; '));
  }
}

/** The installer and its direct/native witnesses share this Darwin completion policy. */
export function create_darwin_installer_effects(
  home: () => Promise<DarwinHomeService>,
  healthy: (sha: string) => Promise<boolean>,
) {
  return {
    async validate_service() {
      await (await home()).validate_running_home();
    },
    async service(action: 'stop' | 'start') {
      const service = await home();
      if (action === 'stop') await service.stop();
      else await service.start();
    },
    async recover_unchanged(sha: string) {
      const service = await home();
      if (service.stopped) return false;
      await service.validate_running_home();
      if (!(await healthy(sha)))
        throw new Error('The unchanged home did not pass health after its stop failed');
      await service.resume_auxiliaries();
      return true;
    },
    async finish_success() {
      const service = await home(),
        errors: string[] = [];
      try {
        await service.provision_schedules();
      } catch (error) {
        errors.push(`schedule setup: ${String(error)}`);
      }
      try {
        await service.resume_auxiliaries();
      } catch (error) {
        errors.push(String(error));
      }
      return errors.length
        ? 'The release passed health, but its auxiliary jobs did not restart: ' + errors.join('; ')
        : null;
    },
    async finish_rollback() {
      try {
        await (await home()).resume_auxiliaries();
        return null;
      } catch (error) {
        return `Its auxiliary jobs did not restart: ${String(error)}`;
      }
    },
  };
}
