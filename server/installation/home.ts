/** Only this attempt's retained entries grant deletion authority. */
import {
  constants,
  fchmodSync,
  fstatSync,
  fsyncSync,
  ftruncateSync,
  lstatSync,
  readFileSync,
  readSync,
  writeFileSync,
} from 'node:fs';
import type { Stats } from 'node:fs';
import { lstat, mkdtemp, realpath } from 'node:fs/promises';
import { basename, dirname, join, relative, resolve } from 'node:path';
import { createServer } from 'node:net';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { observe_promise } from '../core/promise_observer.ts';
import { path_is_within } from '../core/path_containment.ts';
import { expand_path } from '../environments/paths.ts';
import { assert_installer_path } from '../environments/updates/installer/configuration.ts';
import { assert_release_execution } from '../environments/release/process.ts';
import { RuntimeProbeUnsettled } from '../environments/release/admission_process.ts';
import type { ReleaseExecution } from '../environments/release/contracts.ts';
import {
  close_native_descriptor,
  directory_open_flags,
  mkdir_relative,
  open_directory_descriptor,
  open_relative,
  read_directory_names,
  readlink_relative,
  rename_relative,
  rmdir_relative,
  symlink_relative,
  unlink_relative,
} from '../executor/mac/platform.ts';
import { BUILT_IN_MANIFESTS } from '../drivers/built_in_manifests.ts';
import { shell_quote } from '../conversation/worker_unit/logic.ts';
import { home_install_request, validate_home_host } from './logic/home.ts';
import {
  HOME_CLEANUP_TIMEOUT_SECONDS,
  HOME_COMMAND,
  HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
  HOME_UPDATE_TOKEN_FILE,
} from './configuration.ts';
import type {
  HomeInstallDependencies,
  HomeInstallRequest,
  HomeInstallSubmission,
  HomeServicePlan,
} from './contracts.ts';
import { HomeCleanupUnsettled } from './contracts.ts';

export interface DirectoryProof {
  readonly path: string;
  readonly fd: number;
  readonly info: Stats;
  readonly parent?: DirectoryProof;
}
interface EntryProof {
  readonly path: string;
  readonly parent: DirectoryProof;
  readonly info: Stats;
  readonly content: Buffer | string;
  readonly kind: 'file' | 'link';
}
export const same_inode = (left: Stats, right: Stats) =>
  left.dev === right.dev && left.ino === right.ino;
function absent(error: unknown): boolean {
  return (error as NodeJS.ErrnoException)?.code === 'ENOENT';
}
export function current_directory(proof: DirectoryProof): void {
  const actual = lstatSync(proof.path);
  if (!actual.isDirectory() || !same_inode(actual, proof.info))
    throw new Error(`Installation directory changed: ${proof.path}`);
}
export async function existing_ancestor(path: string): Promise<string> {
  try {
    const info = await lstat(path);
    if (!info.isDirectory() && !info.isSymbolicLink())
      throw new Error(`Installation ancestor is not a directory: ${path}`);
    return realpath(path);
  } catch (error) {
    if (!absent(error)) throw error;
    return existing_ancestor(dirname(path));
  }
}
/** The canonical spelling of a root that may not exist yet. An existing root is not followed. */
export async function canonical_root(
  path: string,
  home: string,
): Promise<{ readonly path: string; readonly exists: boolean }> {
  path = resolve(expand_path(path, home));
  try {
    await lstat(path);
  } catch (error) {
    if (!absent(error)) throw error;
    const ancestor = await existing_ancestor(dirname(path));
    // Resolve the nearest existing spelling, then append only absent components.
    let original = dirname(path);
    while (true) {
      try {
        await lstat(original);
        break;
      } catch (failure) {
        if (!absent(failure)) throw failure;
        original = dirname(original);
      }
    }
    return { path: join(ancestor, relative(original, path)), exists: false };
  }
  return { path: join(await realpath(dirname(path)), basename(path)), exists: true };
}
export async function canonical_new_root(path: string, home: string): Promise<string> {
  const root = await canonical_root(path, home);
  if (root.exists)
    throw new Error(`The installation root already exists: ${resolve(expand_path(path, home))}`);
  return root.path;
}
/** Like a service file, an existing command belongs to someone else and is never replaced. */
async function require_absent_command(path: string): Promise<void> {
  try {
    await lstat(path);
  } catch (error) {
    if (absent(error)) return;
    throw error;
  }
  throw new Error(`The panels command already exists: ${path}. It will not be replaced.`);
}
/** A private bind is only a refusal check; activation still owns its later bind. */
export async function check_home_port(port: number): Promise<void> {
  const server = createServer();
  try {
    await new Promise<void>((done, fail) => {
      server.once('error', fail);
      server.listen({ host: '127.0.0.1', port, exclusive: true }, done);
    });
  } catch (error) {
    throw new Error(`Port ${port} is busy or unavailable. Select an unused port.`, {
      cause: error,
    });
  } finally {
    if (server.listening)
      await new Promise<void>((done, fail) =>
        server.close((error) => (error ? fail(error) : done())),
      );
  }
}
/** Directory-relative mutation never traverses a replacement installation tree. */
export class InstallationFiles {
  /** A cleanup that outlives its observer keeps the descriptors until it settles. */
  retained = false;
  readonly directories = new Map<string, DirectoryProof>();
  readonly created_directories: DirectoryProof[] = [];
  readonly entries = new Map<string, EntryProof>();
  readonly owned_roots = new Set<string>();
  readonly creation_execution: ReleaseExecution;
  constructor(execution: ReleaseExecution) {
    this.creation_execution = execution;
  }
  directory(path: string): DirectoryProof {
    path = resolve(path);
    const known = this.directories.get(path);
    if (known) {
      current_directory(known);
      return known;
    }
    const fd = open_directory_descriptor(path),
      proof = { path, fd, info: fstatSync(fd) };
    this.directories.set(path, proof);
    return proof;
  }
  async ensure_directory(path: string): Promise<DirectoryProof> {
    path = resolve(path);
    const known = this.directories.get(path);
    if (known) {
      current_directory(known);
      return known;
    }
    if (![...this.owned_roots].some((root) => path_is_within(root, path))) {
      try {
        return this.directory(await realpath(path));
      } catch (error) {
        if (!absent(error)) throw error;
      }
    }
    const parent = await this.ensure_directory(dirname(path));
    current_directory(parent);
    assert_release_execution(this.creation_execution);
    // EEXIST refuses a concurrent entrant instead of adopting its contents.
    mkdir_relative(parent.fd, basename(path), 0o700);
    const fd = open_relative(parent.fd, basename(path), directory_open_flags),
      proof = { path: join(parent.path, basename(path)), fd, info: fstatSync(fd), parent };
    this.directories.set(proof.path, proof);
    this.created_directories.push(proof);
    current_directory(proof);
    return proof;
  }
  async create_root(path: string): Promise<DirectoryProof> {
    const parent = await this.ensure_directory(dirname(path));
    current_directory(parent);
    assert_release_execution(this.creation_execution);
    mkdir_relative(parent.fd, basename(path), 0o700);
    const fd = open_relative(parent.fd, basename(path), directory_open_flags),
      proof = { path, fd, info: fstatSync(fd), parent };
    this.directories.set(path, proof);
    this.owned_roots.add(path);
    current_directory(proof);
    return proof;
  }
  async file(path: string, content: string, mode: number): Promise<void> {
    const parent = await this.ensure_directory(dirname(path));
    current_directory(parent);
    assert_release_execution(this.creation_execution);
    const fd = open_relative(
      parent.fd,
      basename(path),
      constants.O_CREAT | constants.O_EXCL | constants.O_RDWR | constants.O_NOFOLLOW,
      mode,
    );
    try {
      const info = fstatSync(fd);
      this.entries.set(path, { path, parent, info, kind: 'file', content: Buffer.alloc(0) });
      try {
        writeFileSync(fd, content);
        fsyncSync(fd);
      } finally {
        const size = fstatSync(fd).size;
        const contents = Buffer.alloc(size);
        readSync(fd, contents, 0, size, 0);
        this.entries.set(path, { path, parent, info, kind: 'file', content: contents });
      }
    } finally {
      close_native_descriptor(fd);
    }
  }
  async link(path: string, target: string): Promise<void> {
    const parent = await this.ensure_directory(dirname(path));
    current_directory(parent);
    assert_release_execution(this.creation_execution);
    symlink_relative(parent.fd, basename(path), target);
    const info = lstatSync(join(parent.path, basename(path)));
    this.entries.set(path, { path, parent, info, kind: 'link', content: target });
  }
  assert_owned = async (path: string): Promise<void> => {
    const proof = this.entries.get(path);
    if (!proof) throw new Error(`No created installation file proof: ${path}`);
    current_directory(proof.parent);
    if (proof.kind === 'link') {
      const info = lstatSync(join(proof.parent.path, basename(path)));
      if (
        !same_inode(info, proof.info) ||
        readlink_relative(proof.parent.fd, basename(path)) !== proof.content
      )
        throw new Error(`Created installation link changed: ${path}`);
      return;
    }
    const fd = open_relative(
      proof.parent.fd,
      basename(path),
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
    try {
      const info = fstatSync(fd);
      if (
        !info.isFile() ||
        info.uid !== proof.info.uid ||
        info.nlink !== 1 ||
        !same_inode(info, proof.info) ||
        !readFileSync(fd).equals(proof.content as Buffer)
      )
        throw new Error(`Created installation file changed: ${path}`);
    } finally {
      close_native_descriptor(fd);
    }
  };
  async replace_file(path: string, content: string): Promise<void> {
    await this.assert_owned(path);
    const before = this.entries.get(path)!;
    current_directory(before.parent);
    const fd = open_relative(
      before.parent.fd,
      basename(path),
      constants.O_RDWR | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
    try {
      const info = fstatSync(fd);
      if (
        !info.isFile() ||
        info.uid !== before.info.uid ||
        info.nlink !== 1 ||
        !same_inode(info, before.info)
      )
        throw new Error(`Converted installation plist changed: ${path}`);
      assert_release_execution(this.creation_execution);
      if (fstatSync(fd).nlink !== 1)
        throw new Error(`Converted installation plist lost exclusive ownership: ${path}`);
      ftruncateSync(fd, 0);
      writeFileSync(fd, content);
      fsyncSync(fd);
      this.entries.set(path, { ...before, content: Buffer.from(content) });
    } finally {
      close_native_descriptor(fd);
    }
  }
  async remove_entries(execution: ReleaseExecution): Promise<void> {
    for (const proof of [...this.entries.values()].reverse()) {
      assert_release_execution(execution);
      await this.assert_owned(proof.path);
      unlink_relative(proof.parent.fd, basename(proof.path));
    }
  }
  remove_tree(proof: DirectoryProof, execution: ReleaseExecution): void {
    assert_release_execution(execution);
    current_directory(proof);
    fchmodSync(proof.fd, fstatSync(proof.fd).mode | 0o700);
    for (const name of read_directory_names(proof.fd)) {
      assert_release_execution(execution);
      current_directory(proof);
      let fd: number;
      try {
        fd = open_relative(proof.fd, name, directory_open_flags);
      } catch (error) {
        if (!['ENOTDIR', 'ELOOP'].includes((error as NodeJS.ErrnoException).code ?? ''))
          throw error;
        unlink_relative(proof.fd, name);
        continue;
      }
      try {
        this.remove_tree(
          { path: join(proof.path, name), fd, info: fstatSync(fd), parent: proof },
          execution,
        );
      } finally {
        close_native_descriptor(fd);
      }
    }
    if (!proof.parent) throw new Error('Installation cleanup requires a retained parent');
    current_directory(proof.parent);
    current_directory(proof);
    rmdir_relative(proof.parent.fd, basename(proof.path));
  }
  remove_empty_parents(root: string, execution: ReleaseExecution): void {
    for (const proof of [...this.created_directories].reverse()) {
      if (path_is_within(root, proof.path)) continue;
      assert_release_execution(execution);
      current_directory(proof);
      if (!proof.parent) throw new Error('Missing created directory parent');
      current_directory(proof.parent);
      try {
        rmdir_relative(proof.parent.fd, basename(proof.path));
      } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOTEMPTY') throw error;
      }
    }
  }
  close(): void {
    for (const proof of [...this.directories.values()].reverse()) close_native_descriptor(proof.fd);
  }
}
/** `restore` gives back what this attempt borrowed, before the root tree is removed. */
async function cleanup(
  service: HomeServicePlan | undefined,
  files: InstallationFiles,
  root: DirectoryProof | undefined,
  temporary: DirectoryProof | undefined,
  restore?: (execution: ReleaseExecution) => void,
): Promise<void> {
  const child_processes = new ChildProcesses(),
    signal = AbortSignal.timeout(HOME_CLEANUP_TIMEOUT_SECONDS * 1000);
  const execution: ReleaseExecution = {
    child_processes,
    signal,
    deadline: performance.now() / 1000 + HOME_CLEANUP_TIMEOUT_SECONDS,
  };
  let force_failure: unknown;
  const force = () => {
    child_processes.seal();
    try {
      child_processes.force_kill(performance.now() / 1000);
    } catch (error) {
      force_failure = error;
    }
  };
  signal.addEventListener('abort', force, { once: true });
  const actual = Promise.resolve().then(async () => {
    try {
      await service?.stop(execution, files.assert_owned);
      assert_release_execution(execution);
      // Verify all entries before the first unlink, including late service replacements.
      for (const proof of files.entries.values()) await files.assert_owned(proof.path);
      await files.remove_entries(execution);
      if (files.entries.size) await service?.finish_cleanup?.(execution);
      assert_release_execution(execution);
      restore?.(execution);
      if (root) files.remove_tree(root, execution);
      if (temporary) files.remove_tree(temporary, execution);
      files.remove_empty_parents(root?.path ?? '', execution);
    } finally {
      child_processes.seal();
      await child_processes.settled();
      signal.removeEventListener('abort', force);
      if (force_failure) throw force_failure;
    }
  });
  void actual.catch(() => {});
  const observed = await observe_promise(actual, signal);
  if (!observed.completed)
    throw new HomeCleanupUnsettled(
      'Home service cleanup remains unsettled after its time bound',
      actual,
      signal.reason,
    );
}
/** What one installation attempt made, for its cleanup after a failure. */
export interface InstallationAttempt {
  readonly files: InstallationFiles;
  readonly root_path: string;
  service?: HomeServicePlan;
  root?: DirectoryProof;
  temporary?: DirectoryProof;
  restore?: (execution: ReleaseExecution) => void;
}
/** Remove only this attempt's effects, then throw the original failure. */
export async function abandon_installation(
  error: unknown,
  attempt: InstallationAttempt,
): Promise<never> {
  const { files, root_path, service, root, temporary, restore } = attempt;
  if (error instanceof RuntimeProbeUnsettled) {
    files.retained = true;
    const settled = error.settled.finally(() => files.close());
    void settled.catch(() => {});
    throw new HomeCleanupUnsettled(
      `Runtime admission cleanup remains uncertain. Retained ${temporary?.path ?? root_path}`,
      settled,
      error,
    );
  }
  try {
    await cleanup(service, files, root, temporary, restore);
  } catch (failure) {
    if (failure instanceof HomeCleanupUnsettled) {
      files.retained = true;
      const settled = failure.settled.finally(() => files.close());
      void settled.catch(() => {});
      throw new HomeCleanupUnsettled(
        `Home setup cleanup remains uncertain. Retained ${root_path}${temporary ? ` and ${temporary.path}` : ''}`,
        settled,
        new AggregateError([error, failure]),
      );
    }
    throw new AggregateError(
      [error, failure],
      `Home setup failed: ${String(error)}. Cleanup failed: ${String(failure)}. Retained ${root_path}${temporary ? ` and ${temporary.path}` : ''}`,
    );
  }
  throw error;
}
/** A private folder beside the new root receives the release before admission. */
export async function admission_directory(
  files: InstallationFiles,
  root_path: string,
): Promise<DirectoryProof> {
  const ancestor = files.directory(await existing_ancestor(dirname(root_path)));
  const temporary_path = await mkdtemp(join(ancestor.path, '.panels-home-admission-'));
  return { ...files.directory(temporary_path), parent: ancestor };
}
/** Extract and admit the sealed release into `<temporary>/app`. */
export async function admit_release(
  request: HomeInstallRequest,
  temporary: DirectoryProof,
  deps: Pick<HomeInstallDependencies, 'execution' | 'extract' | 'admit'>,
): Promise<void> {
  const app = join(temporary.path, 'app');
  const manifest = await deps.extract(
    request.archive,
    app,
    request.archive_sha256,
    request.revision,
    deps.execution,
  );
  if (manifest.runtime_contract?.version !== 2)
    throw new Error('First home installation requires a sealed Node version-2 release');
  await deps.admit(app, manifest, deps.execution);
  assert_release_execution(deps.execution);
}
/** The server's settings file. Paths are relative to the data folder. */
export function home_settings(root_path: string, extra: Readonly<Record<string, string>> = {}) {
  return (
    JSON.stringify({
      server_root: '.',
      db_path: 'planner.db',
      logs_dir: '../logs',
      backup_dir: 'backups',
      dispatcher_lock_path: 'dispatcher.lock',
      workspace_root: join(root_path, 'workspaces'),
      ...extra,
    }) + '\n'
  );
}
export async function write_service_files(
  service: HomeServicePlan,
  files: InstallationFiles,
  deps: Pick<HomeInstallDependencies, 'execution' | 'command'>,
): Promise<void> {
  for (const file of service.files) {
    assert_release_execution(deps.execution);
    await files.file(file.path, file.content, file.mode);
    if (file.format === 'plist-json') {
      await files.assert_owned(file.path);
      const converted = await deps.command(
        ['/usr/bin/plutil', '-convert', 'xml1', '-o', '-', file.path],
        deps.execution,
        HOME_SERVICE_COMMAND_TIMEOUT_SECONDS,
      );
      await files.replace_file(file.path, converted.stdout);
    }
  }
  for (const link of service.links) await files.link(link.path, link.target);
}
/** A wrapper, not a link: the app's own bin/panels finds its app from its own path. */
export function home_command_wrapper(root_path: string): string {
  // It names the app folder, so it follows each update that swaps that folder.
  return `#!/bin/sh\nexec ${shell_quote(join(root_path, 'app/bin/panels'))} "$@"\n`;
}
export async function install_home(
  input: HomeInstallSubmission,
  deps: HomeInstallDependencies,
): Promise<string> {
  const { update_token, ...validated } = home_install_request(input);
  let request: HomeInstallRequest = validated;
  validate_home_host(request, deps);
  const command_link = join(deps.account_home, HOME_COMMAND);
  assert_release_execution(deps.execution);
  const root_path = await canonical_new_root(request.root, deps.account_home);
  assert_installer_path(root_path, deps.environment);
  assert_installer_path(request.archive, deps.environment);
  request = { ...request, root: root_path };
  const attempt: InstallationAttempt = { files: new InstallationFiles(deps.execution), root_path };
  const { files } = attempt;
  try {
    const service = (attempt.service = await deps.service(request, deps));
    await service.preflight(deps.execution);
    if (!request.skip_command_link) await require_absent_command(command_link);
    await check_home_port(request.port);
    assert_release_execution(deps.execution);
    const temporary = (attempt.temporary = await admission_directory(files, root_path));
    await admit_release(request, temporary, deps);
    const root = (attempt.root = await files.create_root(root_path));
    current_directory(root);
    current_directory(temporary);
    assert_release_execution(deps.execution);
    rename_relative(temporary.fd, 'app', root.fd, 'app');
    for (const name of ['data', 'logs', 'workspaces', 'machine', 'machine/data'])
      await files.ensure_directory(join(root_path, name));
    await files.ensure_directory(join(root_path, 'data/secrets'));
    if (request.isolated_providers)
      for (const { manifest } of BUILT_IN_MANIFESTS)
        if (manifest.home)
          await files.ensure_directory(
            join(root_path, 'machine/providers', manifest.home.isolated_directory_name),
          );
    await files.file(join(root_path, 'data/config.yaml'), home_settings(root_path), 0o600);
    // The private secrets folder is created 0700 with the file.
    await files.file(join(root_path, HOME_UPDATE_TOKEN_FILE), update_token + '\n', 0o600);
    await write_service_files(service, files, deps);
    if (!request.skip_command_link)
      await files.file(command_link, home_command_wrapper(root_path), 0o755);
    current_directory(root);
    const data = files.directory(join(root_path, 'data'));
    await deps.initialize_database(
      join(root_path, 'app'),
      join(root_path, 'data/planner.db'),
      deps.execution,
      join(root_path, 'machine/data'),
    );
    await deps.prepare_machine(
      join(root_path, 'app'),
      join(root_path, 'machine/runtime'),
      deps.execution,
    );
    current_directory(root);
    current_directory(data);
    assert_release_execution(deps.execution);
    await service.start_home(deps.execution);
    if (!(await deps.healthy(request.port, request.revision, deps.execution.signal)))
      throw new Error(`The home did not report ready release ${request.revision}`);
    assert_release_execution(deps.execution);
    const machine_started_ms = Date.now();
    await service.start_machine(deps.execution);
    await deps.machine_healthy(request, deps.execution, machine_started_ms);
    await service.start_auxiliaries(deps.execution);
    files.remove_tree(temporary, deps.execution);
    attempt.temporary = undefined;
    return `http://127.0.0.1:${request.port}`;
  } catch (error) {
    return await abandon_installation(error, attempt);
  } finally {
    if (!files.retained) files.close();
  }
}
