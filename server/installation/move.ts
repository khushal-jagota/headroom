/**
 * The installer's move mode. `install` puts a held server around a copied, checked data folder;
 * `remove` takes back a moved home that did not take over. Neither makes a database, a
 * credential, an identity or machine code: the target machine already runs machine code.
 */
import { constants, fstatSync, fsyncSync, lstatSync, readFileSync, writeFileSync } from 'node:fs';
import { lstat, readdir, readFile, unlink } from 'node:fs/promises';
import { basename, dirname, join } from 'node:path';
import { path_is_within } from '../core/path_containment.ts';
import { executor_link_socket_path } from '../core/http/executor_link.ts';
import { assert_installer_path } from '../environments/updates/installer/configuration.ts';
import { assert_release_execution } from '../environments/release/process.ts';
import {
  close_native_descriptor,
  directory_open_flags,
  open_relative,
  rename_relative,
} from '../executor/mac/platform.ts';
import { SERVER_MOVE_HELD_FILE } from '../server_move/contracts.ts';
import type { ServerMoveInstallResult } from '../server_move/contracts.ts';
import {
  InstallationFiles,
  abandon_installation,
  admission_directory,
  admit_release,
  canonical_new_root,
  canonical_root,
  check_home_port,
  current_directory,
  home_command_wrapper,
  home_settings,
  same_inode,
  write_service_files,
} from './home.ts';
import type { InstallationAttempt } from './home.ts';
import { validate_home_host } from './logic/home.ts';
import {
  MOVED_HOME_ENTRIES,
  move_install_request,
  move_remove_request,
  moved_home_settings,
} from './logic/move.ts';
import { HOME_COMMAND } from './configuration.ts';
import type { HomeInstallRequest, HomeMoveDependencies } from './contracts.ts';

function absent(error: unknown): boolean {
  return (error as NodeJS.ErrnoException)?.code === 'ENOENT';
}
async function exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if (absent(error)) return false;
    throw error;
  }
}

/**
 * Write the held marker into the staged folder before it moves in. A failed attempt leaves it
 * there, so a retry of the same move accepts the same marker and refuses any other.
 */
function write_held_marker(directory: number, content: string): void {
  let fd: number;
  try {
    fd = open_relative(
      directory,
      SERVER_MOVE_HELD_FILE,
      constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY | constants.O_NOFOLLOW,
      0o600,
    );
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
    const existing = open_relative(
      directory,
      SERVER_MOVE_HELD_FILE,
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
    try {
      if (!fstatSync(existing).isFile() || readFileSync(existing, 'utf8') !== content)
        throw new Error('The staged data folder holds the marker of another move');
    } finally {
      close_native_descriptor(existing);
    }
    return;
  }
  try {
    writeFileSync(fd, content);
    fsyncSync(fd);
  } finally {
    close_native_descriptor(fd);
  }
}
export async function install_moved_home(
  input: unknown,
  deps: HomeMoveDependencies,
): Promise<ServerMoveInstallResult> {
  const move = move_install_request(input);
  validate_home_host(move, deps);
  assert_release_execution(deps.execution);
  const root_path = await canonical_new_root(move.root, deps.account_home);
  // The staged folder itself is never followed: only its parent spelling is resolved.
  const staged = await canonical_root(move.data_directory, deps.account_home);
  for (const path of [root_path, move.archive, staged.path])
    assert_installer_path(path, deps.environment);
  if (!staged.exists || !lstatSync(staged.path).isDirectory())
    throw new Error(`The staged data folder is not a directory: ${move.data_directory}`);
  if (path_is_within(staged.path, root_path))
    throw new Error('The installation root must be outside the staged data folder');
  if (!lstatSync(join(staged.path, 'planner.db')).isFile())
    throw new Error('The staged data folder has no database file');
  const request: HomeInstallRequest = {
    root: root_path,
    service: move.service,
    port: move.port,
    isolated_providers: false,
    archive: move.archive,
    archive_sha256: move.archive_sha256,
    revision: move.revision,
  };
  const command_link = join(deps.account_home, HOME_COMMAND);
  const attempt: InstallationAttempt = { files: new InstallationFiles(deps.execution), root_path };
  const { files } = attempt;
  try {
    const service = (attempt.service = await deps.service(request, deps));
    await service.preflight(deps.execution);
    await check_home_port(request.port);
    assert_release_execution(deps.execution);
    const temporary = (attempt.temporary = await admission_directory(files, root_path));
    await admit_release(request, temporary, deps);
    const root = (attempt.root = await files.create_root(root_path));
    current_directory(root);
    current_directory(temporary);
    assert_release_execution(deps.execution);
    rename_relative(temporary.fd, 'app', root.fd, 'app');

    // The staged folder moves in by one rename. A failure moves it back for the caller.
    const source = files.directory(dirname(staged.path)),
      name = basename(staged.path),
      before = lstatSync(staged.path);
    current_directory(source);
    assert_release_execution(deps.execution);
    // The marker travels with the data: a moved data folder without it took over.
    write_held_marker(files.directory(staged.path).fd, JSON.stringify(move.held) + '\n');
    current_directory(source);
    try {
      rename_relative(source.fd, name, root.fd, 'data');
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EXDEV')
        throw new Error('The staged data folder must be on the installation root filesystem', {
          cause: error,
        });
      throw error;
    }
    attempt.restore = (execution) => {
      assert_release_execution(execution);
      current_directory(root);
      current_directory(source);
      const moved = lstatSync(join(root_path, 'data'));
      if (!moved.isDirectory() || !same_inode(moved, before))
        throw new Error(`The moved data folder changed: ${join(root_path, 'data')}`);
      try {
        lstatSync(staged.path);
        throw new Error(`The staged data folder path is occupied: ${staged.path}`);
      } catch (error) {
        if (!absent(error)) throw error;
      }
      rename_relative(root.fd, 'data', source.fd, name);
    };
    const data = files.directory(join(root_path, 'data'));
    if (!same_inode(data.info, before))
      throw new Error(`The moved data folder changed: ${join(root_path, 'data')}`);
    for (const name of ['logs', 'workspaces']) await files.ensure_directory(join(root_path, name));
    await files.file(
      join(root_path, 'data/config.yaml'),
      home_settings(root_path, moved_home_settings(move)),
      0o600,
    );
    await deps.check_database(
      join(root_path, 'app'),
      join(root_path, 'data/planner.db'),
      deps.execution,
    );
    await write_service_files(service, files, deps);
    // The machine's join may have written the command already. It is never replaced.
    if (!(await exists(command_link)))
      await files.file(command_link, home_command_wrapper(root_path), 0o755);
    current_directory(root);
    current_directory(data);
    assert_release_execution(deps.execution);
    await service.start_home(deps.execution);
    if (!(await deps.healthy(request.port, request.revision, deps.execution.signal)))
      throw new Error(`The moved home did not report ready release ${request.revision}`);
    assert_release_execution(deps.execution);
    await service.start_auxiliaries(deps.execution);
    files.remove_tree(temporary, deps.execution);
    attempt.temporary = undefined;
    return {
      root: root_path,
      data_directory: join(root_path, 'data'),
      executor_link: executor_link_socket_path(join(root_path, 'data/planner.db')),
    };
  } catch (error) {
    return await abandon_installation(error, attempt);
  } finally {
    if (!files.retained) files.close();
  }
}

/** Only a root this mode made: its entries, and a data folder that still holds the marker. */
async function require_moved_home(root: string): Promise<void> {
  const info = await lstat(root);
  if (!info.isDirectory()) throw new Error(`The root is not a directory: ${root}`);
  for (const name of await readdir(root))
    if (!MOVED_HOME_ENTRIES.has(name))
      throw new Error(`The root was not made by a server move and is kept: ${root}`);
  if (!(await exists(join(root, 'data')))) return;
  let marker;
  try {
    marker = await lstat(join(root, 'data', SERVER_MOVE_HELD_FILE));
  } catch (error) {
    if (!absent(error)) throw error;
  }
  if (!marker?.isFile())
    throw new Error(`The root holds a server that is not held by a move and is kept: ${root}`);
}

/** Idempotent: an absent root and absent services are already removed. */
export async function remove_moved_home(
  input: unknown,
  deps: Omit<HomeMoveDependencies, 'extract' | 'admit' | 'healthy' | 'service' | 'check_database'>,
): Promise<void> {
  const request = move_remove_request(input);
  validate_home_host(request, deps);
  const root = await canonical_root(request.root, deps.account_home);
  assert_installer_path(root.path, deps.environment);
  if (root.exists) await require_moved_home(root.path);
  await deps.remove_service(root.path, request.service, deps, deps.execution);
  const command_link = join(deps.account_home, HOME_COMMAND);
  try {
    if (
      lstatSync(command_link).isFile() &&
      (await readFile(command_link, 'utf8')) === home_command_wrapper(root.path)
    )
      await unlink(command_link);
  } catch (error) {
    if (!absent(error)) throw error;
  }
  if (!root.exists) return;
  const files = new InstallationFiles(deps.execution);
  try {
    const parent = files.directory(dirname(root.path));
    current_directory(parent);
    const fd = open_relative(parent.fd, basename(root.path), directory_open_flags);
    const proof = { path: root.path, fd, info: fstatSync(fd), parent };
    files.directories.set(root.path, proof);
    current_directory(proof);
    files.remove_tree(proof, deps.execution);
  } finally {
    files.close();
  }
}
