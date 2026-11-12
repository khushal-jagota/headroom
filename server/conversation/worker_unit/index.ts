/** One Linux Worker child and its descendants, for the child's whole lifetime. */
import {
  accessSync,
  chmodSync,
  closeSync,
  constants,
  fchmodSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { randomUUID } from 'node:crypto';
import { relative } from 'node:path';
import type { ChildProcess } from 'node:child_process';
import type { OwnedChildProcesses } from '../../lifecycle/contracts.ts';
import { control } from './control.ts';
import { SLICE_CPU_WEIGHT_MAX, WORKER_SLICE, WORKER_TEMP_ROOT } from './configuration.ts';
import { WorkerUnitError } from './contracts.ts';
import type { WorkerUnitValues } from './contracts.ts';
import {
  absolute_path,
  environment_file,
  integer_weight,
  properties,
  shell_quote,
} from './logic.ts';

export { WorkerUnitError } from './contracts.ts';
export { environment_file } from './logic.ts';

/** Reject a memory filesystem, including a home directory mounted on tmpfs. */
function disk_directory(path: string): void {
  const resolved = realpathSync(path);
  const mounts: { depth: number; kind: string }[] = [];
  for (const line of readFileSync('/proc/self/mountinfo', 'utf8').trimEnd().split('\n')) {
    const [before, after] = line.split(' - ');
    const mount = before
      .split(' ')[4]
      .replace(/\\([0-7]{3})/g, (_, octal: string) => String.fromCharCode(parseInt(octal, 8)));
    const descendant = relative(mount, resolved);
    if (descendant === '' || (!descendant.startsWith('../') && descendant !== '..')) {
      mounts.push({ depth: mount.split('/').filter(Boolean).length, kind: after.split(' ')[0] });
    }
  }
  mounts.sort(
    (left, right) =>
      left.depth - right.depth || (left.kind < right.kind ? -1 : left.kind > right.kind ? 1 : 0),
  );
  const kind = mounts.at(-1)?.kind;
  if (kind === undefined || kind === 'tmpfs' || kind === 'ramfs') {
    throw new WorkerUnitError(`Worker temporary storage must use disk: ${path}`);
  }
}

function executable_file(path: string): boolean {
  try {
    if (!statSync(path).isFile()) return false;
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

export class WorkerUnit {
  readonly name: string;
  readonly directory: string;
  readonly environment: Record<string, string>;
  readonly working_directory: string;
  readonly slice_name: string;
  readonly finished: Promise<void>;
  private resolve_finished!: () => void;
  private readonly child_processes: OwnedChildProcesses;
  private cleanup_finished = false;
  private stop_requested = false;
  private stopping: Promise<void> | null = null;
  private launching = false;
  private wrapper_ready: Promise<void> | null = null;
  private publish_wrapper: (() => void) | null = null;
  private wrapper: { child: ChildProcess; exited: Promise<void>; closed: Promise<void> } | null =
    null;

  constructor(values: WorkerUnitValues) {
    this.child_processes = values.child_processes;
    this.finished = new Promise<void>((resolve) => {
      this.resolve_finished = resolve;
    });
    this.name = values.name;
    this.directory = values.directory;
    this.environment = values.environment;
    this.working_directory = values.working_directory;
    this.slice_name = values.slice_name;
  }

  static async create(
    environment: Readonly<Record<string, string>>,
    working_directory: string,
    options: { child_processes: OwnedChildProcesses; slice_name?: string },
  ): Promise<WorkerUnit | null> {
    if (process.platform !== 'linux' || !environment.PLAN_TICKET_ID) return null;
    const slice_name = options.slice_name ?? WORKER_SLICE;
    if (!/^[A-Za-z0-9_.-]+\.slice(?![\s\S])/.test(slice_name)) {
      throw new WorkerUnitError('Invalid Worker slice name');
    }
    const [code, text] = await control(
      options.child_processes,
      'show',
      slice_name,
      '--property=LoadState',
      '--property=CPUWeight',
    );
    const found = properties(text);
    const weight = integer_weight(found.CPUWeight);
    if (code || found.LoadState !== 'loaded' || !(weight > 0 && weight <= SLICE_CPU_WEIGHT_MAX)) {
      throw new WorkerUnitError(
        `Worker slice ${slice_name} is unavailable or lacks its lower CPU weight. ` +
          'Install the Worker slice before starting Workers.',
      );
    }
    if (slice_name === WORKER_SLICE) {
      const [parent_code, parent_text] = await control(
        options.child_processes,
        'show',
        'panels.slice',
        '--property=LoadState',
        '--property=CPUWeight',
      );
      const parent = properties(parent_text);
      const parent_weight = integer_weight(parent.CPUWeight);
      if (
        parent_code ||
        parent.LoadState !== 'loaded' ||
        !(parent_weight > 0 && parent_weight <= SLICE_CPU_WEIGHT_MAX)
      ) {
        throw new WorkerUnitError('Install panels.slice with its lower CPU weight before Workers.');
      }
    }
    const root = WORKER_TEMP_ROOT === '~' ? homedir() : WORKER_TEMP_ROOT;
    disk_directory(root);
    const directory = mkdtempSync(`${root}/.pw-`);
    try {
      chmodSync(directory, 0o700);
      if (statSync(directory).uid !== process.getuid!()) {
        throw new WorkerUnitError('Worker temporary directory has a different owner');
      }
      const temporary = `${directory}/t`;
      mkdirSync(temporary, { mode: 0o700 });
      const child_environment = {
        ...environment,
        TMPDIR: temporary,
        TMP: temporary,
        TEMP: temporary,
      };
      const file = openSync(`${directory}/environment`, 'wx');
      try {
        fchmodSync(file, 0o600);
        writeFileSync(file, environment_file(child_environment), 'utf8');
      } finally {
        closeSync(file);
      }
      const unit = new WorkerUnit({
        child_processes: options.child_processes,
        name: `panels-worker-${randomUUID().replaceAll('-', '')}.service`,
        directory,
        environment: child_environment,
        working_directory,
        slice_name,
      });
      options.child_processes.retain_worker_unit(unit);
      return unit;
    } catch (error) {
      rmSync(directory, { recursive: true });
      throw error;
    }
  }

  argv(original: readonly string[]): readonly string[] {
    if (this.stop_requested) throw new WorkerUnitError('Worker launch admission is closed');
    if (!original.length) throw new WorkerUnitError('The Worker command is empty');
    let executable = original[0];
    if (executable.includes('/')) {
      executable = absolute_path(executable, this.working_directory);
      if (!executable_file(executable)) {
        throw new WorkerUnitError(`Worker executable is unavailable: ${original[0]}`);
      }
    } else {
      const found = (this.environment.PATH ?? '/bin:/usr/bin')
        .split(':')
        .map((entry) => {
          const directory = absolute_path(entry, this.working_directory);
          return `${directory.endsWith('/') ? directory : directory + '/'}${executable}`;
        })
        .find(executable_file);
      if (found === undefined) {
        throw new WorkerUnitError(`Worker executable is unavailable: ${original[0]}`);
      }
      executable = found;
    }
    const directory = this.directory.replaceAll('%', '%%');
    return [
      'systemd-run',
      '--user',
      '--quiet',
      '--wait',
      '--pipe',
      '--service-type=exec',
      '--expand-environment=no',
      `--unit=${this.name}`,
      `--slice=${this.slice_name}`,
      '--property=OOMPolicy=continue',
      '--property=KillMode=control-group',
      '--property=NoNewPrivileges=yes',
      `--property=EnvironmentFile=${directory}/environment`,
      `--property=WorkingDirectory=${this.working_directory.replaceAll('%', '%%')}`,
      `--property=ExecStopPost=/bin/rm -rf -- ${shell_quote(directory)}`,
      '--',
      executable,
      ...original.slice(1),
    ];
  }

  begin_launch(): void {
    if (this.stop_requested) throw new WorkerUnitError('Worker launch admission is closed');
    if (this.launching) throw new WorkerUnitError('Worker unit already owns a launcher');
    this.launching = true;
    this.wrapper_ready = new Promise<void>((resolve) => {
      this.publish_wrapper = resolve;
    });
  }
  launch_failed_before_spawn(): void {
    this.resolve_finished(); // The reserved spawn threw without returning any child.
    this.publish_wrapper?.();
  }
  track_wrapper(child: ChildProcess): void {
    let exit!: () => void, close!: () => void;
    const exited = new Promise<void>((resolve) => {
      exit = resolve;
    });
    const closed = new Promise<void>((resolve) => {
      close = resolve;
    });
    child.once('exit', (code, signal) => {
      if (code === 0 && signal === null) this.resolve_finished();
      exit();
    });
    child.on('error', () => {
      if (child.pid === undefined) {
        this.resolve_finished();
        exit();
      }
    });
    child.once('close', close);
    this.wrapper = { child, exited, closed };
    this.publish_wrapper?.();
  }
  stop(): Promise<void> {
    this.stop_requested = true;
    if (this.cleanup_finished) return Promise.resolve();
    if (this.stopping) return this.stopping;
    const actual = Promise.resolve().then(() => this.stop_once());
    this.stopping = actual;
    void actual.catch(() => {
      if (this.stopping === actual) this.stopping = null;
    });
    return actual;
  }
  private async stop_once(): Promise<void> {
    if (this.wrapper_ready) await this.wrapper_ready;
    let [code, text] = await control(
      this.child_processes,
      'show',
      this.name,
      '--property=LoadState',
    );
    if (
      ((!code && properties(text).LoadState !== 'loaded') ||
        (code && (text.includes('not found') || text.includes('not loaded')))) &&
      this.wrapper
    ) {
      // An earlier not-loaded result is insufficient while the launcher can create its unit.
      const { child, exited, closed } = this.wrapper;
      if (child.exitCode === null && child.signalCode === null && child.pid !== undefined)
        child.kill('SIGKILL');
      await exited;
      child.stdin?.destroy();
      child.stdout?.destroy();
      child.stderr?.destroy();
      await closed;
      [code, text] = await control(this.child_processes, 'show', this.name, '--property=LoadState');
    }
    if (!code && properties(text).LoadState === 'loaded') {
      const [stop_code, stop_text] = await control(this.child_processes, 'stop', this.name);
      if (stop_code)
        throw new WorkerUnitError(`Cannot stop Worker unit ${this.name}: ${stop_text}`);
      this.resolve_finished(); // Confirmed inactive, even if later bookkeeping fails.
      await control(this.child_processes, 'reset-failed', this.name);
    } else if (code && !text.includes('not found') && !text.includes('not loaded')) {
      throw new WorkerUnitError(`Cannot inspect Worker unit ${this.name}: ${text}`);
    } else this.resolve_finished();
    try {
      rmSync(this.directory, { recursive: true, force: true });
    } catch {
      // Python's rmtree(ignore_errors=True) does not turn file cleanup into stop failure.
    }
    this.cleanup_finished = true;
  }
}
